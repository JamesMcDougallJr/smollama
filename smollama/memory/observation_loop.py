"""Background observation loop that periodically analyzes readings."""

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING

from ..plugins.base import ObservationHook
from ..readings import ReadingManager

if TYPE_CHECKING:
    from ..agent import Agent
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

Your task:
1. Identify any notable patterns, trends, or anomalies
2. Note any significant changes from previous observations
3. Record important observations that should be remembered

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
}}

Only include observations if there's something noteworthy. Empty lists are fine if readings are normal."""


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
    ):
        """Initialize the observation loop.

        Args:
            store: LocalStore for logging readings and observations.
            readings: ReadingManager for reading all sources.
            agent: Agent for running LLM queries.
            interval_minutes: How often to generate observations.
            lookback_minutes: How far back to look for context.
            plugins: Loaded plugin instances. Any that implement ObservationHook
                     will receive on_observation_begin/end callbacks each cycle.
            observation_max_age_days: Delete observations older than this on each tick.
            readings_max_age_days: Delete readings older than this on each tick.
            compact_memory_threshold_mb: Compact when free RAM drops below this (MB).
            compact_batch_size: Number of observations to summarize per compaction run.
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
        self._hooks: list[ObservationHook] = [
            p for p in (plugins or []) if isinstance(p, ObservationHook)
        ]
        if self._hooks:
            logger.info(
                "Observation hooks registered: %s",
                ", ".join(type(h).__name__ for h in self._hooks),
            )
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        """Start the observation loop as a background task."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            f"Observation loop started (interval={self._interval // 60}min, "
            f"lookback={self._lookback}min)"
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
        self._store.log_readings(current_readings)

        # 2. Get recent reading history
        recent_history = self._store.get_recent_readings(
            minutes=self._lookback,
            source_types=None,  # All types
        )

        # 3. Get relevant past observations
        # Search for observations related to current sources
        source_ids = [r.full_id for r in current_readings]
        past_obs = self._store.search_observations(
            query=" ".join(source_ids),
            limit=5,
        )

        # 4. Format prompt
        prompt = OBSERVATION_PROMPT.format(
            lookback_minutes=self._lookback,
            current_readings=self._format_current_readings(current_readings),
            recent_history=self._format_history(recent_history),
            past_observations=self._format_past_observations(past_obs),
        )

        # 5. Run LLM query (with graceful degradation)
        try:
            response = await self._agent.query(prompt)

            if not response:
                logger.warning("No response from LLM for observation - operating in degraded mode")
                return

            # 6. Parse and store observations
            await self._process_response(response)

        except Exception as e:
            logger.error(f"LLM query failed during observation: {e}")
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

    async def _process_response(self, response: str) -> None:
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
            # Response wasn't valid JSON, try to extract text as single observation
            if response and len(response) > 10:
                if len(response) > 500:
                    logger.warning(
                        f"LLM response truncated from {len(response)} to 500 chars"
                    )
                self._store.add_observation(
                    text=response[:500],
                    observation_type="general",
                    confidence=0.6,
                )
                logger.debug("Stored raw response as observation")
        except Exception as e:
            logger.error(f"Failed to process observation response: {e}")

    async def run_once(self) -> None:
        """Run a single observation cycle (useful for testing)."""
        await self._generate_observation()
