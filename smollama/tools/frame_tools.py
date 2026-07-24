"""Frame search tool: query CLIP-embedded camera keyframes."""

from typing import Any

import numpy as np

from ..frames import FrameStore
from ..frames.text_encoder import ClipTextEncoder
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


class ClassifyClipTool(Tool):
    """Zero-shot video classification for stored clips using CLIP embeddings."""

    def __init__(self, store: FrameStore, text_encoder: ClipTextEncoder):
        self._store = store
        self._encoder = text_encoder

    @property
    def name(self) -> str:
        return "classify_clip"

    @property
    def description(self) -> str:
        return (
            "Classify a stored camera clip against custom text labels using "
            "CLIP zero-shot video classification. Provide a frame_id (from "
            "search_frames results) or a time range (start + end as ISO "
            "timestamps). Returns a similarity score per label — highest score "
            "is the predicted class. Works on both single keyframes and "
            "temporal activity windows."
        )

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="labels",
                type="array",
                description="Candidate text labels to classify against (e.g. ['person at door', 'delivery', 'empty scene'])",
                required=True,
            ),
            ToolParameter(
                name="frame_id",
                type="integer",
                description="ID of a specific frame/window to classify (from search_frames results)",
                required=False,
            ),
            ToolParameter(
                name="start",
                type="string",
                description="Start of time range to classify (ISO timestamp; pair with end)",
                required=False,
            ),
            ToolParameter(
                name="end",
                type="string",
                description="End of time range (ISO timestamp; pair with start)",
                required=False,
            ),
        ]

    async def execute(
        self,
        labels: list[str],
        frame_id: int | None = None,
        start: str | None = None,
        end: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if not labels:
            return {"error": "labels list is required"}

        # Resolve embedding
        if frame_id is not None:
            embedding = self._store.get_embedding(frame_id)
            if embedding is None:
                return {"error": f"No embedding found for frame {frame_id}"}
            frame = self._store.get_frame(frame_id)
            clip_ref = frame["timestamp"] if frame else f"frame_{frame_id}"
        elif start and end:
            frames = self._store.get_frames_in_range(start, end, kind="window")
            if not frames:
                frames = self._store.get_frames_in_range(start, end, kind="keyframe")
            if not frames:
                return {"error": f"No frames found between {start} and {end}"}

            vecs = []
            for f in frames:
                emb = self._store.get_embedding(f["id"])
                if emb:
                    vecs.append(np.asarray(emb, dtype=np.float32))
            if not vecs:
                return {"error": "Embeddings not available (sqlite-vec required)"}

            mean = np.mean(vecs, axis=0)
            norm = float(np.linalg.norm(mean))
            if norm > 0:
                mean = mean / norm
            embedding = mean.tolist()
            clip_ref = f"{start} → {end} ({len(vecs)} frames)"
        else:
            return {"error": "Provide either frame_id or both start and end"}

        # Score each label
        clip_vec = np.asarray(embedding, dtype=np.float32)
        scores: dict[str, float] = {}
        for label in labels:
            label_vec = np.asarray(self._encoder.embed_floats(label), dtype=np.float32)
            scores[label] = round(float(np.dot(clip_vec, label_vec)), 4)

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return {
            "clip": clip_ref,
            "top_label": ranked[0][0] if ranked else None,
            "scores": dict(ranked),
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
