"""Sync client for communicating with other Smollama nodes.

Handles network synchronization with retry logic and batching optimization.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

import httpx

from .crdt_log import CRDTLog, LogEntry

logger = logging.getLogger(__name__)


class SyncStatus(Enum):
    """Status of a sync operation."""

    SUCCESS = "success"
    PARTIAL = "partial"  # Some entries synced
    FAILED = "failed"
    OFFLINE = "offline"  # Remote unavailable


@dataclass
class SyncResult:
    """Result of a sync operation."""

    status: SyncStatus
    entries_pushed: int = 0
    entries_pulled: int = 0
    error: str | None = None
    timestamp: datetime | None = None


class SyncClient:
    """Client for synchronizing CRDT logs between nodes.

    Supports:
    - Push: Send local unsynced entries to remote
    - Pull: Fetch new entries from remote
    - Full sync: Bidirectional sync

    Uses exponential backoff for retries and batching for efficiency.
    """

    def __init__(
        self,
        crdt_log: CRDTLog,
        remote_url: str | None = None,
        batch_size: int = 100,
        max_retries: int = 3,
        timeout: float = 30.0,
    ):
        """Initialize the sync client.

        Args:
            crdt_log: Local CRDT log to sync.
            remote_url: Base URL of remote node (e.g., "http://llama:8080").
            batch_size: Maximum entries per sync batch.
            max_retries: Maximum retry attempts.
            timeout: Request timeout in seconds.
        """
        self.log = crdt_log
        self.remote_url = remote_url
        self.batch_size = batch_size
        self.max_retries = max_retries
        self.timeout = timeout
        self._last_sync: datetime | None = None
        self._consecutive_failures = 0

    def set_remote_url(self, url: str) -> None:
        """Set or update the remote URL.

        Args:
            url: New remote URL.
        """
        self.remote_url = url
        logger.info(f"Remote URL set to {url}")

    async def _request_with_retry(
        self,
        method: str,
        path: str,
        json_data: Any = None,
    ) -> tuple[Any, str | None]:
        """Make HTTP request with exponential backoff retry.

        Args:
            method: HTTP method (GET, POST).
            path: URL path to append to remote_url.
            json_data: Optional JSON body.

        Returns:
            Tuple of (response_data, error_message).
        """
        if not self.remote_url:
            return None, "No remote URL configured"

        url = f"{self.remote_url.rstrip('/')}{path}"
        backoff = 1.0

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(self.max_retries):
                try:
                    if method == "GET":
                        response = await client.get(url)
                    elif method == "POST":
                        response = await client.post(url, json=json_data)
                    else:
                        return None, f"Unsupported method: {method}"

                    if response.status_code == 200:
                        self._consecutive_failures = 0
                        return response.json(), None

                    elif response.status_code >= 500:
                        # Server error, retry
                        logger.warning(
                            f"Server error {response.status_code}, "
                            f"attempt {attempt + 1}/{self.max_retries}"
                        )
                    else:
                        # Client error, don't retry
                        return None, f"HTTP {response.status_code}: {response.text}"

                except httpx.ConnectError:
                    logger.warning(
                        f"Connection failed, attempt {attempt + 1}/{self.max_retries}"
                    )
                except httpx.TimeoutException:
                    logger.warning(
                        f"Request timeout, attempt {attempt + 1}/{self.max_retries}"
                    )
                except Exception as e:
                    logger.error(f"Request error: {e}")
                    return None, str(e)

                # Exponential backoff
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(backoff)
                    backoff *= 2

        self._consecutive_failures += 1
        return None, f"Max retries ({self.max_retries}) exceeded"

    async def push_entries(self) -> SyncResult:
        """Push local unsynced entries to remote.

        Returns:
            SyncResult with push statistics.
        """
        if not self.remote_url:
            return SyncResult(
                status=SyncStatus.FAILED,
                error="No remote URL configured",
            )

        entries = self.log.get_unsynced(limit=self.batch_size)
        if not entries:
            return SyncResult(
                status=SyncStatus.SUCCESS,
                entries_pushed=0,
                timestamp=datetime.now(),
            )

        # Serialize entries for transport
        payload = {
            "node_id": self.log.node_id,
            "entries": [e.to_dict() for e in entries],
        }

        data, error = await self._request_with_retry(
            "POST", "/api/sync/push", payload
        )

        if error:
            return SyncResult(
                status=SyncStatus.OFFLINE if "Connection" in error else SyncStatus.FAILED,
                error=error,
            )

        # Mark successfully pushed entries as synced
        accepted_ids = data.get("accepted_ids", [])
        self.log.mark_synced(accepted_ids)

        self._last_sync = datetime.now()

        return SyncResult(
            status=SyncStatus.SUCCESS,
            entries_pushed=len(accepted_ids),
            timestamp=self._last_sync,
        )

    async def pull_entries(self, since_ts: int | None = None) -> SyncResult:
        """Pull new entries from remote.

        Args:
            since_ts: Only fetch entries after this Lamport timestamp.
                     If None, uses highest local timestamp.

        Returns:
            SyncResult with pull statistics.
        """
        if not self.remote_url:
            return SyncResult(
                status=SyncStatus.FAILED,
                error="No remote URL configured",
            )

        if since_ts is None:
            since_ts = self.log.get_latest_timestamp()

        data, error = await self._request_with_retry(
            "GET", f"/api/sync/pull?since={since_ts}&limit={self.batch_size}"
        )

        if error:
            return SyncResult(
                status=SyncStatus.OFFLINE if "Connection" in error else SyncStatus.FAILED,
                error=error,
            )

        # Parse and merge remote entries
        remote_entries = [
            LogEntry.from_dict(e) for e in data.get("entries", [])
        ]

        added = self.log.merge(remote_entries)
        self._last_sync = datetime.now()

        return SyncResult(
            status=SyncStatus.SUCCESS,
            entries_pulled=added,
            timestamp=self._last_sync,
        )

    async def full_sync(self) -> SyncResult:
        """Perform bidirectional sync.

        Returns:
            Combined SyncResult.
        """
        # Push first
        push_result = await self.push_entries()
        if push_result.status == SyncStatus.OFFLINE:
            return push_result

        # Then pull
        pull_result = await self.pull_entries()

        return SyncResult(
            status=(
                SyncStatus.SUCCESS
                if pull_result.status == SyncStatus.SUCCESS
                else pull_result.status
            ),
            entries_pushed=push_result.entries_pushed,
            entries_pulled=pull_result.entries_pulled,
            error=pull_result.error,
            timestamp=datetime.now(),
        )

    async def sync_loop(
        self,
        interval_seconds: int = 300,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        """Run continuous sync loop.

        Args:
            interval_seconds: Seconds between sync attempts.
            stop_event: Event to signal loop should stop.
        """
        logger.info(f"Starting sync loop with {interval_seconds}s interval")

        while True:
            if stop_event and stop_event.is_set():
                break

            try:
                result = await self.full_sync()
                logger.info(
                    f"Sync: {result.status.value}, "
                    f"pushed={result.entries_pushed}, "
                    f"pulled={result.entries_pulled}"
                )
            except Exception as e:
                logger.error(f"Sync loop error: {e}")

            # Adaptive interval: back off if consecutive failures
            wait_time = interval_seconds
            if self._consecutive_failures > 0:
                wait_time = min(
                    interval_seconds * (2 ** self._consecutive_failures),
                    3600,  # Max 1 hour
                )
                logger.debug(f"Backing off sync for {wait_time}s")

            if stop_event:
                try:
                    await asyncio.wait_for(
                        stop_event.wait(), timeout=wait_time
                    )
                    break  # Stop event was set
                except asyncio.TimeoutError:
                    pass  # Normal timeout, continue loop
            else:
                await asyncio.sleep(wait_time)

        logger.info("Sync loop stopped")

    @property
    def last_sync(self) -> datetime | None:
        """Get timestamp of last successful sync."""
        return self._last_sync

    def get_sync_status(self) -> dict[str, Any]:
        """Get current sync status.

        Returns:
            Dictionary with sync statistics.
        """
        log_stats = self.log.get_stats()

        return {
            "remote_url": self.remote_url,
            "last_sync": self._last_sync.isoformat() if self._last_sync else None,
            "consecutive_failures": self._consecutive_failures,
            "pending_entries": log_stats["unsynced_entries"],
            "total_entries": log_stats["total_entries"],
        }
