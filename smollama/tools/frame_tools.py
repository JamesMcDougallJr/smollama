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
                    "relevance": round(f["similarity"], 3) if f["similarity"] is not None else None,
                }
                for f in result["results"]
            ],
        }
