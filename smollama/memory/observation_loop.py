"""Background observation loop that periodically analyzes readings."""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ..detectors import DetectorConfig, Signal, dedupe_correlated, detect_all
from ..detectors.source import known_sources, load_series
from ..readings import ReadingManager
from ..rules import apply_maintenance, uncovered_signals

if TYPE_CHECKING:
    from ..agent import Agent
    from ..plugins.base import ObservationDomain, ObservationHook
    from ..rules import RuleStore
    from .local_store import LocalStore

logger = logging.getLogger(__name__)

# Prompt template for generating observations
OBSERVATION_PROMPT = """Analyze the following sensor readings and system metrics from the last {lookback_minutes} minutes.

Current readings:
{current_readings}

Recent reading history:
{recent_history}

Relevant past observations:
{past_observations}

Report only what is genuinely noteworthy: a pattern, a trend, an anomaly, or a
change from the past observations above. Steady, unremarkable readings warrant
no observation — return empty lists.

Rules:
- At most {max_items} observations. Fewer is better. None is fine.
- Each "text" must be one sentence, under {max_chars} characters.
- State the reading and the number that justifies it. No preamble, no
  restating the input, no advice, no speculation.
- Output JSON only — no prose before or after it.

Respond with a JSON object containing:
{{
    "observations": [
        {{
            "text": "Description of what you observed",
            "type": "pattern|anomaly|status",
            "confidence": 0.0-1.0,
            "related_sources": ["gpio:17", "system:cpu_temp"]
        }}
    ],
    "memories": [
        {{
            "fact": "Important fact to remember long-term",
            "confidence": 0.0-1.0
        }}
    ]
}}"""


# The prompt that replaces OBSERVATION_PROMPT once detectors are on. The difference
# is not wording, it is which party does the detecting: above, the model is asked to
# find something in a dump of every source; here, code has already found it and the
# model only writes the sentence. The eight golden cases measured qwen2.5:1.5b at
# 0.00 detection and gemma3:1b at 0.00 restraint on the scanning task — neither can
# discriminate, and no prompt fixes that. Describing a finding it was handed is a
# task both can do.
#
# The no-arithmetic rule is there because qwen2.5:1.5b wrote "exceeds baseline by
# 12.6 degrees" from a 63.0 reading against a 50.4 baseline. The subtraction is
# right, but a derived number cannot be checked against the readings, so it fails
# the no-invented-numbers gate and would be unverifiable in the store as well.
SIGNAL_OBSERVATION_PROMPT = """Statistical checks flagged {count} deviation(s). \
The detection is already done. Your only job is to describe each one in a single \
sentence.

Detected:
{signals}

Relevant past observations:
{past_observations}

Rules:
- One observation per deviation above, at most {max_items}.
- Each "text" must be one sentence, under {max_chars} characters.
- Quote only the numbers and time spans written above. Do not compute your own
  differences, percentages or rates.
- No advice, no speculation, no cause you were not given.
- If a past observation above already reports the same deviation, omit it.
- Output JSON only — no prose before or after it.

Respond with a JSON object containing:
{{
    "observations": [
        {{
            "text": "Description of the deviation",
            "type": "pattern|anomaly|status",
            "confidence": 0.0-1.0,
            "related_sources": []
        }}
    ],
    "memories": []
}}

Leave "related_sources" empty — the source identifiers are filled in from the \
detection, so copying them here is unnecessary."""


OBSERVATION_SYSTEM_PROMPT = """You are a sensor-monitoring function, not an assistant.

You receive sensor readings and return JSON matching the provided schema. You do \
not call tools, ask questions, greet, explain your reasoning, or write prose \
outside the JSON.

Report only what is genuinely noteworthy. Unremarkable readings warrant nothing: \
empty lists are the correct answer more often than not."""


def build_observation_schema(max_items: int, max_chars: int) -> dict:
    """JSON Schema constraining the observation response.

    Ollama enforces this during decoding, which matters far more than the prompt
    text on small models. Measured on this Pi with an 11-source prompt: given
    only format="json", qwen2.5:1.5b returned empty objects and gemma3:1b
    enumerated every input until it blew the token cap mid-object (unparseable).
    The same two models under this schema both produced conformant, correctly
    typed output — qwen in a third of the wall time of the 5B model.

    maxItems/maxLength do the work the prompt was only asking for politely.
    """
    return {
        "type": "object",
        "properties": {
            "observations": {
                "type": "array",
                "maxItems": max_items,
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "maxLength": max_chars},
                        "type": {
                            "type": "string",
                            "enum": ["pattern", "anomaly", "status"],
                        },
                        "confidence": {"type": "number"},
                        "related_sources": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["text", "type", "confidence"],
                },
            },
            "memories": {
                "type": "array",
                "maxItems": 2,
                "items": {
                    "type": "object",
                    "properties": {
                        "fact": {"type": "string", "maxLength": max_chars},
                        "confidence": {"type": "number"},
                    },
                    "required": ["fact", "confidence"],
                },
            },
        },
        "required": ["observations", "memories"],
    }


class ObservationLoop:
    """Background task that periodically generates observations from readings."""

    def __init__(
        self,
        store: "LocalStore",
        readings: ReadingManager,
        agent: "Agent",
        interval_minutes: int = 15,
        lookback_minutes: int = 60,
        plugins: list | None = None,
        observation_max_age_days: int = 3,
        readings_max_age_days: int = 7,
        compact_memory_threshold_mb: int = 200,
        compact_batch_size: int = 20,
        domains_mode: str = "replace",
        num_predict: int = 256,
        max_items: int = 3,
        max_chars: int = 200,
        structured_output: bool = True,
        use_tools: bool = False,
        system_prompt: str = "",
        use_detectors: bool = False,
        rule_store: "RuleStore | None" = None,
        max_signals: int = 3,
        maintenance_every: int = 10,
        detector_config: DetectorConfig | None = None,
        detector_window_seconds: float = 604800.0,
    ):
        """Initialize the observation loop.

        Args:
            store: LocalStore for logging readings and observations.
            readings: ReadingManager for reading all sources.
            agent: Agent for running LLM queries.
            interval_minutes: How often to generate observations.
            lookback_minutes: How far back to look for context.
            plugins: Loaded plugin instances. Any that implement ObservationHook
                     will receive on_observation_begin/end callbacks each cycle;
                     any that implement ObservationDomain get domain-focused
                     observation passes over the sources they claim.
            observation_max_age_days: Delete observations older than this on each tick.
            readings_max_age_days: Delete readings older than this on each tick.
            compact_memory_threshold_mb: Compact when free RAM drops below this (MB).
            compact_batch_size: Number of observations to summarize per compaction run.
            domains_mode: "replace" — an active domain pass takes the cycle's single
                          LLM call (rotating among active domains), for
                          memory-constrained nodes; "parallel" — generic + every
                          active domain pass run each cycle.
            use_detectors: Replace the generic scanning pass with detect-then-narrate.
                           Defaults False here so an explicit caller opts in; the
                           product default lives in MemoryConfig and is on.
            rule_store: RuleStore for the coverage pre-filter, evaluation counters,
                        and maintenance. Without it detection still gates the model,
                        but the rule lifecycle stays inert.
            max_signals: Most signals to describe in one cycle, highest score first.
            maintenance_every: Run apply_maintenance once per this many cycles.
                               0 disables it.
            detector_window_seconds: History window detectors see. Deliberately much
                                     wider than lookback_minutes — a level shift is
                                     only a shift relative to a long baseline.
        """
        self._store = store
        self._readings = readings
        self._agent = agent
        self._interval = interval_minutes * 60  # Convert to seconds
        self._lookback = lookback_minutes
        self._obs_max_age_days = observation_max_age_days
        self._readings_max_age_days = readings_max_age_days
        self._compact_threshold_mb = compact_memory_threshold_mb
        self._compact_batch_size = compact_batch_size
        self._domains_mode = domains_mode
        self._domain_rotation = 0  # replace-mode round-robin cursor
        self._num_predict = num_predict
        self._max_items = max_items
        self._max_chars = max_chars
        self._use_tools = use_tools
        self._use_detectors = use_detectors
        self._rules = rule_store
        self._max_signals = max_signals
        self._maintenance_every = maintenance_every
        self._detector_config = detector_config or DetectorConfig()
        self._detector_window = detector_window_seconds
        self._cycles_since_maintenance = 0

        # Verified against Ollama: with tools AND a schema, the model emitted zero
        # tool calls and its output was forced into the schema instead; with tools
        # and no schema the same model emitted a real tool call. So a schema
        # silently disables tool use — resolve the conflict loudly here rather
        # than letting it look like the model simply chose not to act.
        self._structured_output = structured_output
        if structured_output and use_tools:
            logger.warning(
                "observation_structured_output and observation_use_tools are "
                "mutually exclusive (a schema leaves no room for tool calls) — "
                "honouring use_tools and dropping the schema."
            )
            self._structured_output = False
        self._schema = (
            build_observation_schema(max_items, max_chars)
            if self._structured_output
            else None
        )

        # "" = built-in task prompt; "node" = inherit the node persona; else literal
        if system_prompt == "node":
            self._system_prompt = None  # None => agent uses its configured prompt
        else:
            self._system_prompt = system_prompt or OBSERVATION_SYSTEM_PROMPT
        # Imported here rather than at module level: plugins.base transitively
        # imports this module (via tools -> memory), so a top-level import is
        # circular when the plugins package is imported first.
        from ..plugins.base import ObservationDomain, ObservationHook

        self._hooks: list[ObservationHook] = [
            p for p in (plugins or []) if isinstance(p, ObservationHook)
        ]
        self._domains: list[ObservationDomain] = [
            p for p in (plugins or []) if isinstance(p, ObservationDomain)
        ]
        if self._hooks:
            logger.info(
                "Observation hooks registered: %s",
                ", ".join(type(h).__name__ for h in self._hooks),
            )
        if self._domains:
            logger.info(
                "Observation domains registered (%s mode): %s",
                self._domains_mode,
                ", ".join(d.domain_name for d in self._domains),
            )
        self._task: asyncio.Task | None = None
        self._running = False
        self.session_id = str(uuid.uuid4())

    async def start(self) -> None:
        """Start the observation loop as a background task."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            f"Observation loop started (session={self.session_id}, "
            f"interval={self._interval // 60}min, lookback={self._lookback}min)"
        )

    async def stop(self) -> None:
        """Stop the observation loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Observation loop stopped")

    async def _run_loop(self) -> None:
        """Main observation loop."""
        # Initial delay to let system stabilize
        await asyncio.sleep(30)

        while self._running:
            try:
                await self._generate_observation()
            except Exception as e:
                logger.error(f"Observation generation failed: {e}", exc_info=True)

            # Wait for next interval
            await asyncio.sleep(self._interval)

    async def _generate_observation(self) -> None:
        """Generate an observation from current readings."""
        logger.debug("Generating observation...")

        for hook in self._hooks:
            try:
                await hook.on_observation_begin()
            except Exception as e:
                logger.debug("on_observation_begin error in %s: %s", type(hook).__name__, e)

        success = False
        try:
            await self._do_generate_observation()
            success = True
        finally:
            for hook in self._hooks:
                try:
                    await hook.on_observation_end(success)
                except Exception as e:
                    logger.debug("on_observation_end error in %s: %s", type(hook).__name__, e)

    async def _do_generate_observation(self) -> None:
        """Inner implementation of observation generation."""
        # 0. Prune stale data and compact if memory is low
        self._store.cleanup_old_readings(days=self._readings_max_age_days)
        self._store.cleanup_old_observations(days=self._obs_max_age_days)
        await self._maybe_compact()

        # 1. Read all current values and log them
        current_readings = await self._readings.read_all()

        if not current_readings:
            logger.debug("No readings available, skipping observation")
            return

        # Log readings to database
        self._store.log_readings(current_readings, session_id=self.session_id)

        # 2. Get recent reading history (shared by all passes)
        recent_history = self._store.get_recent_readings(
            minutes=self._lookback,
            source_types=None,  # All types
        )

        # 3. Deterministic detection over the full history, before any prompt exists.
        # Rule bookkeeping uses every signal (a rule on a domain-claimed source is
        # still being evaluated); only narration is restricted to unclaimed sources.
        signals = self._run_detection() if self._use_detectors else []
        self._record_rule_evaluations(signals)

        # 4. Partition readings between domains and the generic pass.
        # A domain is active when it claims any current or recent reading
        # (recent-only means the source went quiet — worth observing too).
        claimed_ids: set[str] = set()
        active_domains: list["ObservationDomain"] = []
        for domain in self._domains:
            current_claimed = [r for r in current_readings if domain.matches(r.full_id)]
            history_claimed = [h for h in recent_history if domain.matches(h["full_id"])]
            if current_claimed or history_claimed:
                active_domains.append(domain)
            claimed_ids.update(r.full_id for r in current_claimed)
            claimed_ids.update(h["full_id"] for h in history_claimed)

        generic_readings = [r for r in current_readings if r.full_id not in claimed_ids]
        generic_history = [h for h in recent_history if h["full_id"] not in claimed_ids]

        # 5. Dispatch passes according to mode. Domains are untouched by detection:
        # they derive their own state and build their own prompt, so they claim their
        # sources first and the detector pass covers only what is left over.
        if not active_domains:
            await self._run_unclaimed_pass(current_readings, recent_history, signals)
        elif self._domains_mode == "parallel":
            for domain in active_domains:
                await self._run_domain_pass(domain, current_readings, recent_history)
            if generic_readings:
                unclaimed = [s for s in signals if s.source not in claimed_ids]
                await self._run_unclaimed_pass(
                    generic_readings, generic_history, unclaimed
                )
        else:  # "replace": one focused pass per cycle, rotating among active domains
            domain = active_domains[self._domain_rotation % len(active_domains)]
            self._domain_rotation += 1
            await self._run_domain_pass(domain, current_readings, recent_history)

        # 6. Lifecycle maintenance, on a slower cadence than observation.
        self._maybe_run_maintenance()

    async def _run_unclaimed_pass(
        self,
        readings: list,
        history: list[dict],
        signals: list[Signal],
    ) -> None:
        """Observe the sources no domain claimed, by whichever path is configured."""
        if self._use_detectors:
            await self._run_detector_pass(readings, signals)
        else:
            await self._run_generic_pass(readings, history)

    def _run_detection(self) -> list[Signal]:
        """Run every detector over the stored history. Never raises.

        A failure here deliberately does not fall back to the scanning pass:
        that path was measured at 0.00 detection, so falling back would spend
        35-104s of model time to learn nothing. Better to log and stay quiet.
        """
        now = datetime.now(timezone.utc)
        db = str(self._store.db_path)
        try:
            series = load_series(db, window_seconds=self._detector_window, now=now)
            return detect_all(
                series,
                now=now,
                config=self._detector_config,
                expected_sources=known_sources(db),
            )
        except Exception as e:
            logger.error("Detection pass failed: %s", e, exc_info=True)
            return []

    def _record_rule_evaluations(self, signals: list[Signal]) -> None:
        """Count this cycle against every active rule, fired or not.

        This is what makes the lifecycle move. `apply_maintenance` decides on
        `evaluations` and `fire_rate`, so without a count per cycle — including the
        quiet ones — auto-mute and auto-retire can never trigger and every rule
        lives forever. Only active rules are counted: a proposed rule is not
        monitoring anything yet, so crediting it with evaluations would let it be
        auto-promoted on evidence it never gathered.
        """
        if self._rules is None or not self._use_detectors:
            return
        fired = {(s.source, s.detector, s.direction) for s in signals}
        for rule in self._rules.active_rules():
            try:
                self._rules.record_evaluation(rule.id, fired=rule.identity in fired)
            except Exception as e:
                logger.warning("Could not record evaluation for rule %s: %s", rule.id, e)

    def _maybe_run_maintenance(self) -> None:
        """Apply deterministic lifecycle transitions once per N cycles."""
        if self._rules is None or self._maintenance_every <= 0:
            return
        self._cycles_since_maintenance += 1
        if self._cycles_since_maintenance < self._maintenance_every:
            return
        self._cycles_since_maintenance = 0
        try:
            actions = apply_maintenance(
                self._rules, known_sources=known_sources(str(self._store.db_path))
            )
        except Exception as e:
            logger.error("Rule maintenance failed: %s", e, exc_info=True)
            return
        if actions:
            logger.info(
                "Rule maintenance: %s",
                ", ".join(f"{a.action} {a.rule_id}" for a in actions),
            )

    async def _run_detector_pass(
        self,
        readings: list,
        signals: list[Signal],
    ) -> None:
        """Describe the signals code already found — or call no model at all.

        The skip is the point. Every cycle previously cost 35-104s of inference
        whether or not anything had happened, and most cycles are quiet.
        """
        if self._rules is not None:
            signals = uncovered_signals(signals, self._rules)

        if not signals:
            logger.debug("No uncovered signals this cycle — no model call")
            return

        # Collapse correlated sources before spending narration slots on them: on
        # this system mem_percent and mem_available_mb fire together every cycle on
        # both nodes, which is four signals for two events. Done here rather than in
        # detect_all so rule evaluation above still saw every signal.
        signals = dedupe_correlated(signals)

        # dedupe_correlated sorts, but sort here too so max_signals truncation is
        # correct no matter where the list came from.
        top = sorted(signals, key=lambda s: s.score, reverse=True)[: self._max_signals]
        flagged = {s.source for s in top}
        for s in top:
            flagged.update(s.meta.get("correlated", []))

        past_obs = self._store.search_observations(
            query=" ".join(sorted(flagged)),
            limit=5,
        )

        prompt = SIGNAL_OBSERVATION_PROMPT.format(
            count=len(top),
            # Just the detail. Each Signal.detail is self-contained by design, and a
            # "[detector/direction]" prefix was echoed straight into stored
            # observation text by qwen2.5:1.5b — a machine tag in a human sentence.
            signals="\n".join(f"- {s.detail}" for s in top),
            past_observations=self._format_past_observations(past_obs),
            max_items=self._max_items,
            max_chars=self._max_chars,
        )

        # Snapshot only the flagged sources: the input snapshot is the provenance a
        # later keep/drop verdict is attributed to, and pinning it to every reading
        # would credit feedback to sources that had nothing to do with the finding.
        # A stale signal has no current reading at all, which is correct — absence
        # is the finding.
        await self._query_and_store(
            prompt,
            [r for r in readings if r.full_id in flagged],
            pass_name="detectors",
            attribute_to=flagged,
        )

    async def _run_generic_pass(
        self,
        readings: list,
        history: list[dict],
    ) -> None:
        """Run the generic observation pass over the given readings."""
        source_ids = [r.full_id for r in readings]
        past_obs = self._store.search_observations(
            query=" ".join(source_ids),
            limit=5,
        )

        prompt = OBSERVATION_PROMPT.format(
            lookback_minutes=self._lookback,
            current_readings=self._format_current_readings(readings),
            recent_history=self._format_history(history),
            past_observations=self._format_past_observations(past_obs),
            max_items=self._max_items,
            max_chars=self._max_chars,
        )

        await self._query_and_store(prompt, readings, pass_name="generic")

    async def _run_domain_pass(
        self,
        domain: "ObservationDomain",
        current_readings: list,
        recent_history: list[dict],
    ) -> None:
        """Run one domain-focused observation pass."""
        claimed_current = [r for r in current_readings if domain.matches(r.full_id)]
        claimed_history = [h for h in recent_history if domain.matches(h["full_id"])]

        try:
            state = domain.derive_state(claimed_history)
            past_obs = self._store.search_observations(
                query=domain.domain_name,
                limit=5,
            )
            prompt = domain.build_prompt(
                state=state,
                current_readings=claimed_current,
                history=claimed_history,
                past_observations=past_obs,
                lookback_minutes=self._lookback,
            )
        except Exception as e:
            logger.error(
                f"Domain '{domain.domain_name}' prompt construction failed: {e}",
                exc_info=True,
            )
            return

        await self._query_and_store(
            prompt, claimed_current, pass_name=domain.domain_name
        )

    async def _query_and_store(
        self,
        prompt: str,
        readings: list,
        pass_name: str,
        attribute_to: set[str] | None = None,
    ) -> None:
        """Run the LLM query for one pass and store the parsed results."""
        input_snapshot = [
            {
                "full_id": r.full_id,
                "value": r.value,
                "unit": r.unit,
                "timestamp": r.timestamp.isoformat(),
            }
            for r in readings
        ]

        try:
            # A JSON *schema* (not just format="json") is what actually holds
            # small models to shape — see build_observation_schema. num_predict
            # bounds wall time; no tools because this is single-shot analysis.
            # Shape of the call is configurable so a capable model can be let off
            # the leash (tools, unbounded output, node persona) while a small one
            # stays pinned. See the observation_* keys in MemoryConfig.
            response = await self._agent.query(
                prompt,
                options=({"num_predict": self._num_predict}
                         if self._num_predict > 0 else None),
                format=self._schema,
                use_tools=self._use_tools,
                system=self._system_prompt,
            )

            if not response:
                logger.warning(
                    f"No response from LLM for {pass_name} observation pass "
                    "- operating in degraded mode"
                )
                return

            await self._process_response(
                response, self.session_id, input_snapshot,
                attribute_to=attribute_to,
            )

        except Exception as e:
            logger.error(f"LLM query failed during {pass_name} observation pass: {e}")
            # Continue loop - sensor logging already completed

    @staticmethod
    def _get_free_memory_mb() -> float:
        """Return available system RAM in MB, reading /proc/meminfo."""
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        return int(line.split()[1]) / 1024  # kB → MB
        except OSError:
            pass
        return float("inf")  # non-Linux: never compact

    async def _maybe_compact(self) -> None:
        """Compact old observations into a summary if free RAM is low."""
        free_mb = self._get_free_memory_mb()
        if free_mb >= self._compact_threshold_mb:
            return

        oldest = self._store.get_oldest_observations(limit=self._compact_batch_size)
        if len(oldest) < 5:
            return  # not enough to bother

        logger.info(
            "Free RAM %.0f MB below threshold (%d MB) — compacting %d observations",
            free_mb,
            self._compact_threshold_mb,
            len(oldest),
        )

        summary = await self._summarize_observations(oldest)
        if summary:
            self._store.add_observation(summary, observation_type="summary", confidence=0.9)
            self._store.delete_observations([o["id"] for o in oldest])
            logger.info("Compacted %d observations into 1 summary", len(oldest))

    async def _summarize_observations(self, observations: list[dict]) -> str | None:
        """Ask the LLM to summarize a batch of observations into 2-3 sentences."""
        numbered = "\n".join(
            f"{i + 1}. [{o['timestamp'][:16]}] {o['text']}"
            for i, o in enumerate(observations)
        )
        prompt = (
            f"Summarize the following {len(observations)} sensor observations from a Raspberry Pi "
            f"into 2-3 sentences capturing the key patterns, trends, and anomalies. "
            f"Be concise.\n\nObservations:\n{numbered}\n\nSummary:"
        )
        try:
            response = await self._agent.query(prompt)
            return response.strip() if response else None
        except Exception as e:
            logger.warning("Compaction summarization failed: %s", e)
            return None

    def _format_current_readings(self, readings) -> str:
        """Format current readings for the prompt."""
        if not readings:
            return "No readings available"

        lines = []
        for r in readings:
            unit = f" {r.unit}" if r.unit else ""
            lines.append(f"- {r.full_id}: {r.value}{unit}")

        return "\n".join(lines)

    def _format_history(self, history: list[dict]) -> str:
        """Format reading history for the prompt."""
        if not history:
            return "No recent history"

        # Group by source
        by_source: dict[str, list] = {}
        for h in history:
            fid = h["full_id"]
            if fid not in by_source:
                by_source[fid] = []
            by_source[fid].append(h)

        lines = []
        for source_id, readings in by_source.items():
            # Show summary stats
            values = [r["value"] for r in readings if isinstance(r["value"], (int, float))]
            if values:
                lines.append(
                    f"- {source_id}: {len(readings)} readings, "
                    f"min={min(values)}, max={max(values)}, "
                    f"avg={sum(values)/len(values):.1f}"
                )
            else:
                lines.append(f"- {source_id}: {len(readings)} readings")

        return "\n".join(lines) if lines else "No summarizable history"

    def _format_past_observations(self, observations: list[dict]) -> str:
        """Format past observations for the prompt."""
        if not observations:
            return "No relevant past observations"

        lines = []
        for obs in observations:
            lines.append(f"- [{obs['type']}] {obs['text']}")

        return "\n".join(lines)

    @staticmethod
    def _sanitize_sources(
        cited: list | None, attribute_to: set[str] | None
    ) -> list[str] | None:
        """Keep only sources the pass was actually about; fall back to all of them.

        Measured on qwen2.5:1.5b under the detector path: text that correctly
        described the finding, paired with `related_sources` of "system:cpu_temp" on
        a case that never mentioned it, and "system:hcsr04" for "hcsr04:distance".
        A 1.5B model does not reliably copy an identifier, and there is no reason to
        let it try — the detector already knows which source fired.

        This is not cosmetic. `related_sources` is what a later keep/drop verdict is
        attributed to (`feedback_for_sources`) and what a rule would be authored
        against, so a wrong one teaches the system about a source that had nothing
        to do with the finding.
        """
        if not attribute_to:
            return cited or None
        kept = [s for s in (cited or []) if s in attribute_to]
        return kept or sorted(attribute_to)

    async def _process_response(
        self,
        response: str,
        session_id: str,
        input_snapshot: list[dict],
        attribute_to: set[str] | None = None,
    ) -> None:
        """Parse LLM response and store observations/memories.

        `attribute_to` is the set of sources the pass is actually about. When given,
        the model's `related_sources` is filtered to it and falls back to it when
        nothing survives — see _sanitize_sources for why.
        """
        import json

        try:
            # Try to extract JSON from response
            # Handle case where response might have markdown code blocks
            response = response.strip()
            if response.startswith("```"):
                # Remove code block markers
                lines = response.split("\n")
                lines = [l for l in lines if not l.startswith("```")]
                response = "\n".join(lines)

            data = json.loads(response)

            # Store observations
            observations = data.get("observations", [])
            for obs in observations:
                self._store.add_observation(
                    text=obs["text"],
                    observation_type=obs.get("type", "general"),
                    confidence=obs.get("confidence", 0.8),
                    related_sources=self._sanitize_sources(
                        obs.get("related_sources"), attribute_to
                    ),
                    session_id=session_id,
                    input_snapshot=input_snapshot,
                )
                logger.info(f"Recorded observation: {obs['text'][:50]}...")

            # Store memories
            memories = data.get("memories", [])
            for mem in memories:
                self._store.add_memory(
                    text=mem["fact"],
                    confidence=mem.get("confidence", 0.8),
                )
                logger.info(f"Stored memory: {mem['fact'][:50]}...")

        except json.JSONDecodeError:
            # Do NOT fall back to storing the raw text. An unparseable response
            # means the model didn't do the task, and its output is usually a
            # half-emitted JSON object or an essay preamble. Storing that put
            # fragments like '{"observations": [{"text": ...' into the store as
            # observation *text* — and because observations are embedded and fed
            # back in as "relevant past observations", the junk compounded into
            # later prompts. Skipping loses nothing: the readings are already
            # logged, so the next cycle sees the same data.
            logger.warning(
                "Discarding unparseable observation response (%d chars): %s",
                len(response or ""),
                (response or "")[:120].replace("\n", " "),
            )
        except Exception as e:
            logger.error(f"Failed to process observation response: {e}")

    async def run_once(self) -> None:
        """Run a single observation cycle (useful for testing)."""
        await self._generate_observation()
