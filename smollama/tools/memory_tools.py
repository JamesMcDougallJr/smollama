"""Memory tools for semantic search, remembering facts, and recording observations."""

from typing import Any

from ..memory import LocalStore
from .base import Tool, ToolParameter


class RecallTool(Tool):
    """Tool for semantic search across observations and memories."""

    def __init__(self, store: LocalStore):
        """Initialize with a LocalStore.

        Args:
            store: LocalStore instance for memory operations.
        """
        self._store = store

    @property
    def name(self) -> str:
        return "recall"

    @property
    def description(self) -> str:
        return (
            "Search your memory for relevant observations and facts. "
            "Use this to recall past patterns, anomalies, and learned information. "
            "Returns both recent observations and persistent memories."
        )

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="query",
                type="string",
                description="What to search for (e.g., 'temperature spikes', 'motion patterns')",
                required=True,
            ),
            ToolParameter(
                name="limit",
                type="integer",
                description="Maximum results per category (default: 5)",
                required=False,
            ),
        ]

    async def execute(
        self,
        query: str,
        limit: int = 5,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Search memory for relevant information.

        Args:
            query: Search query.
            limit: Max results per category.

        Returns:
            Dict with observations and memories.
        """
        results = self._store.recall(query, limit=limit)

        # Format for LLM consumption
        return {
            "query": query,
            "observations": [
                {
                    "text": obs["text"],
                    "timestamp": obs["timestamp"],
                    "type": obs["type"],
                    "confidence": obs["confidence"],
                    "relevance": round(obs.get("similarity", 0.5), 2),
                }
                for obs in results["observations"]
            ],
            "memories": [
                {
                    "fact": mem["text"],
                    "confidence": mem["confidence"],
                    "times_confirmed": mem["times_confirmed"],
                    "relevance": round(mem.get("similarity", 0.5), 2),
                }
                for mem in results["memories"]
            ],
            "total_results": len(results["observations"]) + len(results["memories"]),
        }


class RememberTool(Tool):
    """Tool for storing persistent facts/memories."""

    def __init__(self, store: LocalStore):
        """Initialize with a LocalStore.

        Args:
            store: LocalStore instance for memory operations.
        """
        self._store = store

    @property
    def name(self) -> str:
        return "remember"

    @property
    def description(self) -> str:
        return (
            "Store a new fact or piece of information in persistent memory. "
            "Use this for important patterns, user preferences, or learned behaviors "
            "that should survive restarts. Similar facts will be reinforced rather than duplicated."
        )

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="fact",
                type="string",
                description="The fact or information to remember",
                required=True,
            ),
            ToolParameter(
                name="confidence",
                type="number",
                description="How confident you are in this fact (0.0-1.0, default: 0.8)",
                required=False,
            ),
        ]

    async def execute(
        self,
        fact: str,
        confidence: float = 0.8,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Store a fact in memory.

        Args:
            fact: The fact to remember.
            confidence: Confidence level.

        Returns:
            Confirmation of storage.
        """
        # Clamp confidence to valid range
        confidence = max(0.0, min(1.0, confidence))

        memory_id = self._store.add_memory(fact, confidence)

        return {
            "status": "stored",
            "memory_id": memory_id,
            "fact": fact,
            "confidence": confidence,
        }


class ObserveTool(Tool):
    """Tool for recording timestamped observations."""

    def __init__(self, store: LocalStore):
        """Initialize with a LocalStore.

        Args:
            store: LocalStore instance for memory operations.
        """
        self._store = store

    @property
    def name(self) -> str:
        return "observe"

    @property
    def description(self) -> str:
        return (
            "Record an observation about current sensor readings or system state. "
            "Use this to note patterns, anomalies, or interesting events. "
            "Observations are timestamped and searchable."
        )

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="observation",
                type="string",
                description="Your observation about the current state or pattern",
                required=True,
            ),
            ToolParameter(
                name="observation_type",
                type="string",
                description="Category of observation",
                required=False,
                enum=["pattern", "anomaly", "status", "general"],
            ),
            ToolParameter(
                name="related_sources",
                type="array",
                description="Source IDs this observation relates to (e.g., ['gpio:17', 'system:cpu_temp'])",
                required=False,
            ),
            ToolParameter(
                name="confidence",
                type="number",
                description="Confidence in this observation (0.0-1.0, default: 0.8)",
                required=False,
            ),
        ]

    async def execute(
        self,
        observation: str,
        observation_type: str = "general",
        related_sources: list[str] | None = None,
        confidence: float = 0.8,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Record an observation.

        Args:
            observation: The observation text.
            observation_type: Category of observation.
            related_sources: Related source IDs.
            confidence: Confidence level.

        Returns:
            Confirmation of storage.
        """
        confidence = max(0.0, min(1.0, confidence))

        observation_id = self._store.add_observation(
            text=observation,
            observation_type=observation_type,
            confidence=confidence,
            related_sources=related_sources,
        )

        return {
            "status": "recorded",
            "observation_id": observation_id,
            "observation": observation,
            "type": observation_type,
            "confidence": confidence,
            "related_sources": related_sources,
        }
