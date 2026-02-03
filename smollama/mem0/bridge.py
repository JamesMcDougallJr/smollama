"""Mem0 bridge for indexing CRDT entries to mem0.

Runs on the Llama (master) node to provide cross-node semantic search.
Polls the CRDT log for new entries and indexes observations and memories.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any

from ..config import Mem0Config
from ..sync.crdt_log import CRDTLog, LogEntry
from .client import Mem0Client

logger = logging.getLogger(__name__)


class Mem0Bridge:
    """Bridge that indexes CRDT entries to Mem0.

    Runs as a background task on the Llama node, polling the CRDT log
    for new observations and memories, then indexing them to mem0 for
    cross-node semantic search.
    """

    def __init__(
        self,
        config: Mem0Config,
        crdt_log: CRDTLog,
    ):
        """Initialize the bridge.

        Args:
            config: Mem0 configuration.
            crdt_log: CRDT log to poll for entries.
        """
        self.config = config
        self.crdt_log = crdt_log
        self.client = Mem0Client(config.server_url)

        self._running = False
        self._task: asyncio.Task | None = None
        self._last_indexed_ts: int = 0
        self._indexed_ids: set[str] = set()

    async def start(self) -> None:
        """Start the bridge background task."""
        if self._running:
            logger.warning("Mem0Bridge already running")
            return

        # Check mem0 server health
        if not await self.client.health_check():
            logger.error("Mem0 server not reachable, bridge not started")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            f"Mem0Bridge started, polling every {self.config.bridge_interval_seconds}s"
        )

    async def stop(self) -> None:
        """Stop the bridge."""
        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        await self.client.close()
        logger.info("Mem0Bridge stopped")

    async def _run_loop(self) -> None:
        """Main polling loop."""
        while self._running:
            try:
                await self._index_new_entries()
            except Exception as e:
                logger.error(f"Error indexing entries: {e}", exc_info=True)

            await asyncio.sleep(self.config.bridge_interval_seconds)

    async def _index_new_entries(self) -> None:
        """Index new CRDT entries to mem0."""
        # Get entries since last indexed timestamp
        entries = self.crdt_log.get_entries_since(self._last_indexed_ts, limit=100)

        if not entries:
            return

        indexed_count = 0
        for entry in entries:
            # Skip if already indexed (by ID)
            if entry.id in self._indexed_ids:
                continue

            # Only index observations and memories (not readings)
            if entry.event_type == "observation" and self.config.index_observations:
                await self._index_observation(entry)
                indexed_count += 1
            elif entry.event_type == "memory" and self.config.index_memories:
                await self._index_memory(entry)
                indexed_count += 1

            # Track indexed entries
            self._indexed_ids.add(entry.id)
            self._last_indexed_ts = max(self._last_indexed_ts, entry.lamport_ts)

        if indexed_count > 0:
            logger.info(f"Indexed {indexed_count} entries to mem0")

    async def _index_observation(self, entry: LogEntry) -> None:
        """Index an observation entry.

        Args:
            entry: CRDT log entry with observation data.
        """
        payload = entry.payload
        text = payload.get("text", "")

        if not text:
            return

        metadata = {
            "crdt_id": entry.id,
            "lamport_ts": entry.lamport_ts,
            "observation_type": payload.get("type", "general"),
            "confidence": payload.get("confidence", 0.5),
            "created_at": entry.created_at.isoformat(),
            "source_node": entry.node_id,
        }

        if related := payload.get("related_sources"):
            metadata["related_sources"] = related

        try:
            await self.client.add_memory(
                text=text,
                user_id=entry.node_id,  # Node name as user_id
                agent_id="observations",  # Data type as agent_id
                metadata=metadata,
            )
        except Exception as e:
            logger.error(f"Failed to index observation {entry.id}: {e}")

    async def _index_memory(self, entry: LogEntry) -> None:
        """Index a memory entry.

        Args:
            entry: CRDT log entry with memory data.
        """
        payload = entry.payload
        text = payload.get("text", "")

        if not text:
            return

        metadata = {
            "crdt_id": entry.id,
            "lamport_ts": entry.lamport_ts,
            "confidence": payload.get("confidence", 0.5),
            "times_confirmed": payload.get("times_confirmed", 1),
            "created_at": entry.created_at.isoformat(),
            "source_node": entry.node_id,
        }

        try:
            await self.client.add_memory(
                text=text,
                user_id=entry.node_id,  # Node name as user_id
                agent_id="memories",  # Data type as agent_id
                metadata=metadata,
            )
        except Exception as e:
            logger.error(f"Failed to index memory {entry.id}: {e}")

    def get_stats(self) -> dict[str, Any]:
        """Get bridge statistics.

        Returns:
            Dictionary with bridge stats.
        """
        return {
            "running": self._running,
            "last_indexed_ts": self._last_indexed_ts,
            "indexed_count": len(self._indexed_ids),
            "interval_seconds": self.config.bridge_interval_seconds,
            "server_url": self.config.server_url,
        }
