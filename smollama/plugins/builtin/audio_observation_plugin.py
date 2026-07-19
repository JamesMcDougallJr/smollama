"""Audio observation-domain skeleton.

Placeholder domain proving the ObservationDomain interface is media-generic.
No audio sensor exists yet; when a mic/audio-classifier node comes online,
fill in the source patterns and semantics via config (or extend this class)
and enable the plugin — the observation loop and dashboard need no changes.

Disabled by default and claims no sources out of the box.
"""

import fnmatch
from datetime import datetime
from typing import Any

from smollama.plugins.base import ObserverPlugin, PluginMetadata


class AudioObservationPlugin(ObserverPlugin):
    """Observation domain for audio sources (skeleton — configure to activate)."""

    def __init__(self) -> None:
        self._patterns: list[str] = []
        self._semantics: dict[str, str] = {}

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="audio_observation",
            version="0.1.0",
            author="James",
            description="Audio-centric observation generation (skeleton; no audio sensor yet)",
            dependencies=[],
            plugin_type="observer",
        )

    @property
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "source_patterns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": [],
                },
                "source_semantics": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
            },
            "additionalProperties": False,
        }

    def check_dependencies(self) -> tuple[bool, str | None]:
        return (True, None)

    def setup(self) -> None:
        cfg = getattr(self, "_config", {}) or {}
        self._patterns = cfg.get("source_patterns", [])
        self._semantics = cfg.get("source_semantics", {})

    def teardown(self) -> None:
        pass

    # ---- ObservationDomain ----

    @property
    def domain_name(self) -> str:
        return "audio"

    def matches(self, full_id: str) -> bool:
        return any(fnmatch.fnmatch(full_id, p) for p in self._patterns)

    def describe_sources(self) -> dict[str, str]:
        return dict(self._semantics)

    def derive_state(self, history: list[dict], now: Any = None) -> dict[str, Any]:
        now = now or datetime.now()
        return {
            "has_data": bool(history),
            "sample_count": len(history),
        }

    def build_prompt(
        self,
        state: dict[str, Any],
        current_readings: list,
        history: list[dict],
        past_observations: list[dict],
        lookback_minutes: int,
    ) -> str:
        readings = "\n".join(f"- {r.full_id}: {r.value}" for r in current_readings)
        return (
            "You are summarizing what a microphone heard. Audio-classification "
            f"readings from the last {lookback_minutes} minutes:\n{readings}\n\n"
            'Respond with a JSON object: {"observations": [{"text": "...", '
            '"type": "status", "confidence": 0.8, "related_sources": []}], "memories": []}'
        )

    def derive_status(self, state: dict[str, Any]) -> str:
        if not state.get("has_data"):
            return "No audio data received"
        return f"Audio data present ({state['sample_count']} recent samples)"
