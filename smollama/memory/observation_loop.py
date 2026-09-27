"""Background observation loop that periodically analyzes readings."""

import asyncio
import logging
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from ..readings import ReadingManager

if TYPE_CHECKING:
    from ..agent import Agent
    from ..plugins.base import ObservationDomain, ObservationHook
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

        # 3. Partition readings between domains and the generic pass.
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

        # 4. Dispatch passes according to mode
        if not active_domains:
            await self._run_generic_pass(current_readings, recent_history)
        elif self._domains_mode == "parallel":
            for domain in active_domains:
                await self._run_domain_pass(domain, current_readings, recent_history)
            if generic_readings:
                await self._run_generic_pass(generic_readings, generic_history)
        else:  # "replace": one focused pass per cycle, rotating among active domains
            domain = active_domains[self._domain_rotation % len(active_domains)]
            self._domain_rotation += 1
            await self._run_domain_pass(domain, current_readings, recent_history)

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

            await self._process_response(response, self.session_id, input_snapshot)

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

    async def _process_response(
        self,
        response: str,
        session_id: str,
        input_snapshot: list[dict],
    ) -> None:
        """Parse LLM response and store observations/memories."""
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
                    related_sources=obs.get("related_sources"),
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
