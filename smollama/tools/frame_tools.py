"""Frame search tool: query CLIP-embedded camera keyframes."""

from typing import Any

from ..frames import FrameStore
from .base import Tool, ToolParameter


class SearchFramesTool(Tool):
    """Tool for semantic search over stored camera keyframes."""

    def __init__(self, store: FrameStore):
        self._store = store

    @property
    def name(self) -> str:
        return "search_frames"

    @property
    def description(self) -> str:
        return (
            "Search stored camera keyframes by describing what to look for "
            "(e.g. 'a black cat', 'person carrying a box'). Returns matching "
            "frames with timestamps, camera node, and detected object labels — "
            "use it to answer questions like 'when did you last see X?'."
        )

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="query",
                type="string",
                description="Description of the scene or object to find",
                required=True,
            ),
            ToolParameter(
                name="limit",
                type="integer",
                description="Maximum frames to return (default: 5)",
                required=False,
            ),
        ]

    async def execute(self, query: str, limit: int = 5, **kwargs: Any) -> dict[str, Any]:
        result = self._store.search_text(query, limit=limit)
        return {
            "query": query,
            "search_mode": result["mode"],
            "frames": [
                {
                    "timestamp": f["timestamp"],
                    "node": f["node_id"],
                    "labels": f["labels"],
                    "trigger": f["trigger"],
                    "kind": f["kind"],
                    "relevance": round(f["similarity"], 3) if f["similarity"] is not None else None,
                }
                for f in result["results"]
            ],
        }


class RecentActivityTool(Tool):
    """Tool for querying recent zero-shot activity matches (triage review queue)."""

    def __init__(self, store: FrameStore):
        self._store = store

    @property
    def name(self) -> str:
        return "recent_activity"

    @property
    def description(self) -> str:
        return (
            "Look up recently flagged activity windows from the camera triage "
            "system (e.g. 'person at door', 'package delivery'). Categories "
            "are configured in config/activity_prompts.yaml. Returns scored, "
            "human-review candidates, not confirmed events — use it to answer "
            "questions like 'has anything been flagged recently?'."
        )

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="category",
                type="string",
                description="Filter to one activity category (omit for all)",
                required=False,
            ),
            ToolParameter(
                name="hours",
                type="integer",
                description="How far back to look, in hours (default: 24)",
                required=False,
            ),
            ToolParameter(
                name="limit",
                type="integer",
                description="Maximum windows to return (default: 10)",
                required=False,
            ),
        ]

    async def execute(
        self,
        category: str | None = None,
        hours: int = 24,
        limit: int = 10,
        **kwargs: Any,
    ) -> dict[str, Any]:
        windows = self._store.recent_activity(hours=hours, category=category, limit=limit)
        return {
            "hours": hours,
            "category": category,
            "windows": [
                {
                    "start": w["window_start"],
                    "end": w["window_end"],
                    "node": w["node_id"],
                    "category": w["activity_category"],
                    "score": round(w["activity_score"], 3) if w["activity_score"] is not None else None,
                    "person_max": (w["meta"] or {}).get("person_max"),
                    "labels": w["labels"],
                }
                for w in windows
            ],
        }
