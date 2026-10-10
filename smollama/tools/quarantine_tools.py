"""Tools that let the agent stop (and resume) recording a source with invalid data.

The model supplies a source and a reason. It does not supply evidence: the tool
recomputes that from stored history, and refuses anything but a source reporting one
unchanging value. Refusals come back as data with a reason rather than as exceptions,
so the model can read why and correct course instead of retrying blindly.

See smollama/quarantine.py for the limits, all of which are enforced in code.
"""

from collections.abc import Callable
from typing import Any

from ..quarantine import Quarantine, QuarantineRefused, QuarantineStore
from .base import Tool, ToolParameter


def _summary(q: Quarantine) -> dict[str, Any]:
    return {
        "source_id": q.full_id,
        "constant": q.constant,
        "evidence_samples": q.evidence_samples,
        "evidence_hours": round(q.evidence_hours or 0.0, 1),
        "stopped_at": q.quarantined_at,
        "stopped_by": q.quarantined_by,
        "readings_not_stored": q.suppressed_count,
        "reason": q.reason,
    }


class StopRecordingTool(Tool):
    """Stop storing a source whose readings are one unchanging value."""

    def __init__(self, store: QuarantineStore, series_loader: Callable[[str], list]):
        """
        Args:
            store: Where quarantines live and are enforced.
            series_loader: source_id -> that source's stored numeric samples. Injected
                so evidence always comes from history, never from tool arguments.
        """
        self._store = store
        self._load = series_loader

    @property
    def name(self) -> str:
        return "stop_recording_source"

    @property
    def description(self) -> str:
        return (
            "Stop storing readings from a source whose data looks invalid, to save "
            "space. Only works for a source reporting one constant value for many "
            "hours (a dead, unplugged or stuck sensor). A source whose values change "
            "is refused: changes are what get recorded. The source is still read "
            "every cycle, and recording resumes automatically the moment its value "
            "differs, so this is reversible. Evidence is checked against stored "
            "history; you only supply the source and a short reason."
        )

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="source_id",
                type="string",
                description="Full source ID, e.g. 'hcsr04:distance'",
                required=True,
            ),
            ToolParameter(
                name="reason",
                type="string",
                description="One sentence on why the data looks invalid",
                required=True,
            ),
        ]

    async def execute(self, source_id: str, reason: str, **kwargs: Any) -> dict[str, Any]:
        # **kwargs is swallowed on purpose: whatever else a model passes (samples,
        # force, its own evidence) has no path into the decision.
        try:
            q = self._store.quarantine(
                source_id, reason, samples=self._load(source_id), by="agent"
            )
        except QuarantineRefused as e:
            return {"status": "refused", "source_id": source_id, "reason": str(e)}
        return {"status": "stopped", **_summary(q)}


class ResumeRecordingTool(Tool):
    """Undo a stop: record a source again."""

    def __init__(self, store: QuarantineStore):
        self._store = store

    @property
    def name(self) -> str:
        return "resume_recording_source"

    @property
    def description(self) -> str:
        return (
            "Resume storing readings from a source previously stopped with "
            "stop_recording_source, for example after a sensor was replaced."
        )

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(name="source_id", type="string",
                          description="Full source ID to resume", required=True),
            ToolParameter(name="reason", type="string",
                          description="Why recording should resume", required=True),
        ]

    async def execute(self, source_id: str, reason: str, **kwargs: Any) -> dict[str, Any]:
        try:
            self._store.release(source_id, reason, by="agent")
        except QuarantineRefused as e:
            return {"status": "refused", "source_id": source_id, "reason": str(e)}
        return {"status": "resumed", "source_id": source_id}


class ListStoppedSourcesTool(Tool):
    """Report which sources are currently not being stored, and why."""

    def __init__(self, store: QuarantineStore):
        self._store = store

    @property
    def name(self) -> str:
        return "list_stopped_sources"

    @property
    def description(self) -> str:
        return (
            "List sources whose recording is currently stopped, with the constant "
            "value each was stuck at, the evidence, and how many readings were not "
            "stored as a result."
        )

    @property
    def parameters(self) -> list[ToolParameter]:
        return []

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        stopped = self._store.quarantined()
        return {"count": len(stopped), "sources": [_summary(q) for q in stopped]}
