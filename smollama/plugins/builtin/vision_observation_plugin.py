"""Vision observation-domain plugin.

Teaches the master's observation loop how to interpret camera/vision readings
(person counts, activities, detected objects — e.g. from the jetson_inference
bridge on an edge node) and how to prompt the LLM for camera-centric
observations like "No humans were detected in the past 3 hours" instead of
generic numeric-trend analysis.

Runs on the master, has no hardware dependencies, and is disabled by default.
Claimed sources are matched by full_id pattern so relayed edge readings
('pipi:jetson_inference:person_count') and local ones
('jetson_inference:person_count') are both recognized.
"""

import fnmatch
from datetime import datetime
from typing import Any

from smollama.plugins.base import ObserverPlugin, PluginMetadata

_DEFAULT_SOURCE_PATTERNS = ["*jetson_inference*"]
_DEFAULT_ABSENCE_WINDOW_HOURS = 3

# Default semantics for the jetson_infer.py writer's contract. Overridable via
# config for other vision pipelines that emit differently named sources.
_DEFAULT_SOURCE_SEMANTICS = {
    "person_count": "number of humans currently detected by the camera",
    "object_count": "total number of objects currently detected",
    "top_object": "most prominent object class currently in view",
    "pose_count": "number of human poses currently tracked",
    "activity": "recognized human pose/activity (e.g. arms_raised)",
    "network_fps": "camera inference pipeline speed (not scene content)",
}

VISION_PROMPT = """You are summarizing what a camera saw. The readings below come from a \
computer-vision pipeline (object detection / pose estimation) on a camera node.

What each source means:
{source_semantics}

Derived scene state (computed deterministically from the reading history):
{state_summary}

Current readings:
{current_readings}

Reading history over the last {lookback_minutes} minutes:
{recent_history}

Relevant past observations:
{past_observations}

Write observations about the SCENE (what happened in front of the camera), not about \
the numbers themselves. Consider ALL of the following observation kinds and record any \
that apply — do not skip one because it seems routine:
- presence/absence: state plainly whether humans were detected, and for how long \
(e.g. "No humans were detected in the past 3 hours; a person was last seen at 14:32")
- transition: someone appeared, left, or the number of people changed
- activity: what people were doing, and changes in activity
- objects: notable objects appearing or disappearing from view
- anomaly: anything unusual versus the past observations
- camera health: the pipeline going quiet or its frame rate degrading

Respond with a JSON object containing:
{{
    "observations": [
        {{
            "text": "Plain-language description of what the camera scene showed",
            "type": "status|transition|activity|anomaly|pattern",
            "confidence": 0.0-1.0,
            "related_sources": ["{example_source}"]
        }}
    ],
    "memories": [
        {{
            "fact": "Important long-term fact about this location (routines, recurring visitors)",
            "confidence": 0.0-1.0
        }}
    ]
}}

Always include at least one "status" observation summarizing current presence/absence."""


def _source_tail(full_id: str) -> str:
    """Bare source name: 'pipi:jetson_inference:person_count' -> 'person_count'."""
    return full_id.rsplit(":", 1)[-1]


def _parse_ts(ts: str) -> datetime | None:
    """Parse an ISO timestamp to naive local time (history mixes naive/aware)."""
    try:
        dt = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


class VisionObservationPlugin(ObserverPlugin):
    """Observation domain for camera/vision sources."""

    def __init__(self) -> None:
        self._patterns: list[str] = list(_DEFAULT_SOURCE_PATTERNS)
        self._semantics: dict[str, str] = dict(_DEFAULT_SOURCE_SEMANTICS)
        self._absence_window_hours: float = _DEFAULT_ABSENCE_WINDOW_HOURS

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="vision_observation",
            version="1.0.0",
            author="James",
            description="Camera-centric observation generation for vision sources (jetson_inference etc.)",
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
                    "default": _DEFAULT_SOURCE_PATTERNS,
                },
                "source_semantics": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
                "absence_window_hours": {
                    "type": "number",
                    "minimum": 0,
                    "default": _DEFAULT_ABSENCE_WINDOW_HOURS,
                },
            },
            "additionalProperties": False,
        }

    def check_dependencies(self) -> tuple[bool, str | None]:
        return (True, None)

    def setup(self) -> None:
        cfg = getattr(self, "_config", {}) or {}
        self._patterns = cfg.get("source_patterns", list(_DEFAULT_SOURCE_PATTERNS))
        self._semantics = {**_DEFAULT_SOURCE_SEMANTICS, **cfg.get("source_semantics", {})}
        self._absence_window_hours = cfg.get(
            "absence_window_hours", _DEFAULT_ABSENCE_WINDOW_HOURS
        )

    def teardown(self) -> None:
        pass

    # ---- ObservationDomain ----

    @property
    def domain_name(self) -> str:
        return "vision"

    @property
    def status_lookback_minutes(self) -> int:
        return int(self._absence_window_hours * 60)

    def matches(self, full_id: str) -> bool:
        return any(fnmatch.fnmatch(full_id, p) for p in self._patterns)

    def describe_sources(self) -> dict[str, str]:
        return dict(self._semantics)

    def derive_state(self, history: list[dict], now: Any = None) -> dict[str, Any]:
        now = now or datetime.now()

        latest: dict[str, dict] = {}  # tail name -> newest reading dict
        latest_ts: datetime | None = None
        last_person_seen: datetime | None = None
        person_counts: list[float] = []
        activities: dict[str, int] = {}
        objects: dict[str, int] = {}

        for h in history:
            ts = _parse_ts(h.get("timestamp", ""))
            if ts is None:
                continue
            tail = _source_tail(h.get("full_id", ""))
            value = h.get("value")

            if latest_ts is None or ts > latest_ts:
                latest_ts = ts
            prev = latest.get(tail)
            if prev is None or ts > (_parse_ts(prev["timestamp"]) or ts):
                latest[tail] = h

            if tail in ("person_count", "pose_count") and isinstance(value, (int, float)):
                if tail == "person_count":
                    person_counts.append(float(value))
                if value > 0 and (last_person_seen is None or ts > last_person_seen):
                    last_person_seen = ts
            elif tail == "activity" and isinstance(value, str) and value:
                activities[value] = activities.get(value, 0) + 1
            elif tail == "top_object" and isinstance(value, str) and value:
                objects[value] = objects.get(value, 0) + 1

        latest_person = latest.get("person_count", {}).get("value")
        person_present = isinstance(latest_person, (int, float)) and latest_person > 0

        return {
            "has_data": latest_ts is not None,
            "data_age_seconds": (now - latest_ts).total_seconds() if latest_ts else None,
            "sample_count": len(history),
            "person_present": person_present,
            "person_count_now": latest_person if isinstance(latest_person, (int, float)) else None,
            "last_person_seen": last_person_seen.isoformat() if last_person_seen else None,
            "hours_since_person": (
                (now - last_person_seen).total_seconds() / 3600 if last_person_seen else None
            ),
            "person_count_max": max(person_counts) if person_counts else None,
            "activities_seen": activities,
            "objects_seen": objects,
            "latest": {tail: h.get("value") for tail, h in latest.items()},
            "absence_window_hours": self._absence_window_hours,
        }

    def build_prompt(
        self,
        state: dict[str, Any],
        current_readings: list,
        history: list[dict],
        past_observations: list[dict],
        lookback_minutes: int,
    ) -> str:
        semantics = "\n".join(f"- {name}: {desc}" for name, desc in self._semantics.items())

        current_lines = []
        example_source = "jetson_inference:person_count"
        for r in current_readings:
            unit = f" {r.unit}" if r.unit else ""
            line = f"- {r.full_id}: {r.value}{unit}"
            if r.metadata and _source_tail(r.full_id) in ("top_object", "activity"):
                line += f" (detail: {r.metadata})"
            current_lines.append(line)
            example_source = r.full_id

        return VISION_PROMPT.format(
            source_semantics=semantics,
            state_summary=self._format_state(state),
            current_readings="\n".join(current_lines) or "No current readings (camera may be offline)",
            lookback_minutes=lookback_minutes,
            recent_history=self._format_history(history),
            past_observations="\n".join(
                f"- [{o['type']}] {o['text']}" for o in past_observations
            ) or "No relevant past observations",
            example_source=example_source,
        )

    def derive_status(self, state: dict[str, Any]) -> str:
        if not state.get("has_data"):
            return "No vision data received — camera node may be offline"

        if state.get("person_present"):
            n = state.get("person_count_now")
            count = int(n) if isinstance(n, (int, float)) else 1
            activity = None
            latest = state.get("latest", {})
            if isinstance(latest.get("activity"), str) and latest["activity"]:
                activity = latest["activity"]
            base = f"{count} {'person' if count == 1 else 'people'} currently detected"
            return f"{base} ({activity})" if activity else base

        hours = state.get("hours_since_person")
        window = state.get("absence_window_hours", self._absence_window_hours)
        if hours is not None:
            last_seen = _parse_ts(state.get("last_person_seen") or "")
            when = last_seen.strftime("%H:%M") if last_seen else "unknown"
            if hours < 1:
                return f"No humans detected in the past {int(hours * 60)} minutes (last seen {when})"
            return f"No humans detected in the past {hours:.1f} hours (last seen {when})"
        return f"No humans detected in the past {window:g} hours"

    # ---- formatting helpers ----

    def _format_state(self, state: dict[str, Any]) -> str:
        lines = [f"- {self.derive_status(state)}"]
        if state.get("activities_seen"):
            acts = ", ".join(
                f"{a} (x{c})" for a, c in sorted(state["activities_seen"].items())
            )
            lines.append(f"- Activities observed in window: {acts}")
        if state.get("objects_seen"):
            objs = ", ".join(
                f"{o} (x{c})" for o, c in sorted(state["objects_seen"].items())
            )
            lines.append(f"- Objects observed in window: {objs}")
        if state.get("person_count_max") is not None:
            lines.append(f"- Peak simultaneous people in window: {int(state['person_count_max'])}")
        if state.get("data_age_seconds") is not None:
            lines.append(f"- Newest reading is {int(state['data_age_seconds'])}s old")
        return "\n".join(lines)

    def _format_history(self, history: list[dict]) -> str:
        if not history:
            return "No recent history"
        by_source: dict[str, list[dict]] = {}
        for h in history:
            by_source.setdefault(h["full_id"], []).append(h)

        lines = []
        for full_id, entries in by_source.items():
            values = [e["value"] for e in entries if isinstance(e["value"], (int, float))]
            if values:
                lines.append(
                    f"- {full_id}: {len(entries)} readings, "
                    f"min={min(values)}, max={max(values)}, "
                    f"avg={sum(values) / len(values):.1f}"
                )
            else:
                # String-valued sources (activity, top_object): show the sequence of changes
                seen: list[str] = []
                for e in reversed(entries):  # oldest first
                    v = str(e["value"])
                    if not seen or seen[-1] != v:
                        seen.append(v)
                lines.append(f"- {full_id}: {len(entries)} readings, sequence: {' → '.join(seen[-8:])}")
        return "\n".join(lines)
