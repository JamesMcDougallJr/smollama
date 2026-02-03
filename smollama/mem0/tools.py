"""Mem0 tools for cross-node semantic search."""

from typing import Any

from ..tools.base import Tool, ToolParameter
from .client import Mem0Client


class CrossNodeRecallTool(Tool):
    """Tool for searching memories across ALL nodes via Mem0.

    This tool is only available on the Llama (master) node where
    Mem0 aggregates observations and memories from all Alpaca nodes.
    """

    def __init__(self, client: Mem0Client):
        """Initialize with a Mem0Client.

        Args:
            client: Mem0Client instance for cross-node queries.
        """
        self._client = client

    @property
    def name(self) -> str:
        return "cross_node_recall"

    @property
    def description(self) -> str:
        return (
            "Search memories and observations across ALL nodes in the network. "
            "Use this to find patterns, events, or facts from any connected node. "
            "You can optionally filter by specific node or data type."
        )

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="query",
                type="string",
                description="What to search for (e.g., 'motion detected', 'temperature spike')",
                required=True,
            ),
            ToolParameter(
                name="node_filter",
                type="string",
                description="Filter to a specific node name (e.g., 'alpaca-living-room')",
                required=False,
            ),
            ToolParameter(
                name="type_filter",
                type="string",
                description="Filter by data type",
                required=False,
                enum=["observations", "memories"],
            ),
            ToolParameter(
                name="limit",
                type="integer",
                description="Maximum results to return (default: 10)",
                required=False,
            ),
        ]

    async def execute(
        self,
        query: str,
        node_filter: str | None = None,
        type_filter: str | None = None,
        limit: int = 10,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Search across all nodes.

        Args:
            query: Search query.
            node_filter: Optional node name filter.
            type_filter: Optional data type filter.
            limit: Max results.

        Returns:
            Dict with search results from all matching nodes.
        """
        # Map filter to mem0 parameters
        user_id = node_filter  # Node name = user_id
        agent_id = type_filter  # Data type = agent_id

        try:
            memories = await self._client.search_memories(
                query=query,
                user_id=user_id,
                agent_id=agent_id,
                limit=limit,
            )
        except Exception as e:
            return {
                "error": str(e),
                "query": query,
                "results": [],
            }

        # Format results for LLM consumption
        results = []
        for mem in memories:
            result = {
                "text": mem.get("memory", mem.get("text", "")),
                "relevance": round(mem.get("score", mem.get("similarity", 0.5)), 2),
            }

            # Add metadata if present
            metadata = mem.get("metadata", {})
            if metadata:
                result["node"] = metadata.get("source_node", mem.get("user_id", "unknown"))
                result["type"] = metadata.get("observation_type", mem.get("agent_id", "unknown"))
                result["confidence"] = metadata.get("confidence", 0.5)
                result["timestamp"] = metadata.get("created_at", "")
            else:
                result["node"] = mem.get("user_id", "unknown")
                result["type"] = mem.get("agent_id", "unknown")

            results.append(result)

        # Group by node for easier reading
        nodes_seen = set(r.get("node", "unknown") for r in results)

        return {
            "query": query,
            "node_filter": node_filter,
            "type_filter": type_filter,
            "total_results": len(results),
            "nodes_with_results": list(nodes_seen),
            "results": results,
        }
