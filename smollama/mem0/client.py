"""Mem0 client wrapper for semantic memory operations."""

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class Mem0Client:
    """Client for interacting with Mem0 server.

    Provides methods for adding and searching memories across nodes.
    Uses the REST API directly for maximum compatibility.
    """

    def __init__(self, server_url: str = "http://localhost:8050"):
        """Initialize the Mem0 client.

        Args:
            server_url: Base URL of the Mem0 server.
        """
        self.server_url = server_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.server_url,
                timeout=30.0,
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def health_check(self) -> bool:
        """Check if mem0 server is healthy.

        Returns:
            True if server is healthy, False otherwise.
        """
        try:
            client = await self._get_client()
            response = await client.get("/health")
            return response.status_code == 200
        except Exception as e:
            logger.debug(f"Health check failed: {e}")
            return False

    async def add_memory(
        self,
        text: str,
        user_id: str,
        agent_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Add a memory to mem0.

        Args:
            text: The memory text content.
            user_id: Node identifier (e.g., "alpaca-living-room").
            agent_id: Data type identifier (e.g., "observations", "memories").
            metadata: Additional metadata to store.

        Returns:
            Response from mem0 with memory ID.
        """
        client = await self._get_client()

        payload: dict[str, Any] = {
            "messages": [{"role": "user", "content": text}],
            "user_id": user_id,
        }

        if agent_id:
            payload["agent_id"] = agent_id

        if metadata:
            payload["metadata"] = metadata

        response = await client.post("/v1/memories/", json=payload)
        response.raise_for_status()

        result = response.json()
        logger.debug(f"Added memory for user={user_id}, agent={agent_id}")
        return result

    async def search_memories(
        self,
        query: str,
        user_id: str | None = None,
        agent_id: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Search memories semantically.

        Args:
            query: Search query text.
            user_id: Filter by node (None for all nodes).
            agent_id: Filter by data type (None for all types).
            limit: Maximum results to return.

        Returns:
            List of matching memories with similarity scores.
        """
        client = await self._get_client()

        payload: dict[str, Any] = {
            "query": query,
            "limit": limit,
        }

        if user_id:
            payload["user_id"] = user_id

        if agent_id:
            payload["agent_id"] = agent_id

        response = await client.post("/v1/memories/search/", json=payload)
        response.raise_for_status()

        result = response.json()
        memories = result.get("results", result.get("memories", []))
        logger.debug(f"Search returned {len(memories)} results for query: {query[:50]}")
        return memories

    async def get_all_memories(
        self,
        user_id: str | None = None,
        agent_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Get all memories, optionally filtered.

        Args:
            user_id: Filter by node.
            agent_id: Filter by data type.
            limit: Maximum results.

        Returns:
            List of memories.
        """
        client = await self._get_client()

        params: dict[str, Any] = {"limit": limit}

        if user_id:
            params["user_id"] = user_id

        if agent_id:
            params["agent_id"] = agent_id

        response = await client.get("/v1/memories/", params=params)
        response.raise_for_status()

        result = response.json()
        return result.get("results", result.get("memories", []))

    async def delete_memory(self, memory_id: str) -> bool:
        """Delete a memory by ID.

        Args:
            memory_id: The memory ID to delete.

        Returns:
            True if deleted successfully.
        """
        client = await self._get_client()

        response = await client.delete(f"/v1/memories/{memory_id}/")

        if response.status_code == 404:
            return False

        response.raise_for_status()
        return True

    async def get_memory(self, memory_id: str) -> dict[str, Any] | None:
        """Get a specific memory by ID.

        Args:
            memory_id: The memory ID.

        Returns:
            Memory data or None if not found.
        """
        client = await self._get_client()

        response = await client.get(f"/v1/memories/{memory_id}/")

        if response.status_code == 404:
            return None

        response.raise_for_status()
        return response.json()
