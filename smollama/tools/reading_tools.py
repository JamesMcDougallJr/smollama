"""Tools for reading from all input sources via the unified Reading abstraction."""

from typing import Any

from ..readings import ReadingManager
from .base import Tool, ToolParameter


class ReadSourceTool(Tool):
    """Tool to read from any source by full ID."""

    def __init__(self, readings: ReadingManager):
        """Initialize with a ReadingManager.

        Args:
            readings: ReadingManager with registered providers.
        """
        self._readings = readings

    @property
    def name(self) -> str:
        return "read_source"

    @property
    def description(self) -> str:
        return (
            "Read the current value from any input source. "
            "Use full ID format like 'gpio:17' or 'system:cpu_temp'. "
            "Use list_sources to discover available sources."
        )

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="source_id",
                type="string",
                description="Full source ID (e.g., 'gpio:17', 'system:cpu_temp', 'mqtt:kitchen/temp')",
                required=True,
            ),
        ]

    async def execute(self, source_id: str, **kwargs: Any) -> dict[str, Any]:
        """Read from the specified source.

        Args:
            source_id: Full source ID to read from.

        Returns:
            Dict with reading data or error message.
        """
        reading = await self._readings.read(source_id)

        if reading is None:
            return {
                "error": f"Source '{source_id}' not found",
                "available_types": self._readings.source_types,
            }

        return {
            "source_id": reading.full_id,
            "value": reading.value,
            "unit": reading.unit,
            "timestamp": reading.timestamp.isoformat(),
            "metadata": reading.metadata,
        }


class ListSourcesTool(Tool):
    """Tool to list all available reading sources."""

    def __init__(self, readings: ReadingManager):
        """Initialize with a ReadingManager.

        Args:
            readings: ReadingManager with registered providers.
        """
        self._readings = readings

    @property
    def name(self) -> str:
        return "list_sources"

    @property
    def description(self) -> str:
        return (
            "List all available input sources that can be read from. "
            "Optionally filter by source type (gpio, system, mqtt, etc.)."
        )

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="source_type",
                type="string",
                description="Filter by source type (e.g., 'gpio', 'system'). Leave empty for all sources.",
                required=False,
            ),
        ]

    async def execute(
        self, source_type: str | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        """List available sources.

        Args:
            source_type: Optional filter by type.

        Returns:
            Dict with available sources grouped by type.
        """
        sources = self._readings.list_sources(source_type)

        # Group by type for easier reading
        grouped: dict[str, list[str]] = {}
        for source in sources:
            stype, sid = source.split(":", 1)
            if stype not in grouped:
                grouped[stype] = []
            grouped[stype].append(sid)

        return {
            "total_count": len(sources),
            "sources_by_type": grouped,
            "all_sources": sources,
        }


class GetReadingHistoryTool(Tool):
    """Tool to get historical readings for a source."""

    def __init__(self, memory_store: Any):
        """Initialize with a LocalStore for history queries.

        Args:
            memory_store: LocalStore instance with reading history.
        """
        self._store = memory_store

    @property
    def name(self) -> str:
        return "get_reading_history"

    @property
    def description(self) -> str:
        return (
            "Get historical readings for a source over a time period. "
            "Useful for analyzing trends and patterns."
        )

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="source_id",
                type="string",
                description="Full source ID (e.g., 'gpio:17', 'system:cpu_temp')",
                required=True,
            ),
            ToolParameter(
                name="minutes",
                type="integer",
                description="Number of minutes to look back (default: 60)",
                required=False,
            ),
            ToolParameter(
                name="limit",
                type="integer",
                description="Maximum number of readings to return (default: 100)",
                required=False,
            ),
        ]

    async def execute(
        self,
        source_id: str,
        minutes: int = 60,
        limit: int = 100,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Get reading history for a source.

        Args:
            source_id: Full source ID.
            minutes: Lookback period in minutes.
            limit: Max readings to return.

        Returns:
            Dict with historical readings and summary.
        """
        readings = await self._store.get_reading_history(
            full_id=source_id,
            minutes=minutes,
            limit=limit,
        )

        if not readings:
            return {
                "source_id": source_id,
                "count": 0,
                "readings": [],
                "message": f"No readings found for {source_id} in last {minutes} minutes",
            }

        # Calculate basic stats for numeric values
        values = [r["value"] for r in readings if isinstance(r["value"], (int, float))]
        stats = {}
        if values:
            stats = {
                "min": min(values),
                "max": max(values),
                "avg": round(sum(values) / len(values), 2),
            }

        return {
            "source_id": source_id,
            "count": len(readings),
            "period_minutes": minutes,
            "statistics": stats,
            "readings": readings,
        }
