"""CRDT-based append-only event log for offline-first synchronization.

Uses Lamport timestamps for conflict-free ordering of events from multiple
nodes that may be offline for extended periods.
"""

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Schema for the CRDT event log
CRDT_SCHEMA = """
-- CRDT event log: append-only with Lamport timestamps
CREATE TABLE IF NOT EXISTS crdt_log (
    id TEXT PRIMARY KEY,
    lamport_ts INTEGER NOT NULL,
    node_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    synced_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_crdt_lamport ON crdt_log(lamport_ts);
CREATE INDEX IF NOT EXISTS idx_crdt_node ON crdt_log(node_id);
CREATE INDEX IF NOT EXISTS idx_crdt_synced ON crdt_log(synced_at);
CREATE INDEX IF NOT EXISTS idx_crdt_type ON crdt_log(event_type);
"""


@dataclass
class LogEntry:
    """A single entry in the CRDT event log."""

    id: str
    lamport_ts: int
    node_id: str
    event_type: str  # "reading", "observation", "memory"
    payload: dict[str, Any]
    created_at: datetime
    synced_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "lamport_ts": self.lamport_ts,
            "node_id": self.node_id,
            "event_type": self.event_type,
            "payload": self.payload,
            "created_at": self.created_at.isoformat(),
            "synced_at": self.synced_at.isoformat() if self.synced_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LogEntry":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            lamport_ts=data["lamport_ts"],
            node_id=data["node_id"],
            event_type=data["event_type"],
            payload=data["payload"],
            created_at=datetime.fromisoformat(data["created_at"]),
            synced_at=(
                datetime.fromisoformat(data["synced_at"])
                if data.get("synced_at")
                else None
            ),
        )


class CRDTLog:
    """CRDT-based append-only event log with Lamport timestamps.

    Supports offline-first operation where nodes can accumulate events
    and later merge with other nodes using Lamport timestamp ordering.
    """

    def __init__(self, db_path: str | Path, node_id: str):
        """Initialize the CRDT log.

        Args:
            db_path: Path to SQLite database file.
            node_id: Unique identifier for this node.
        """
        self.db_path = Path(db_path).expanduser()
        self.node_id = node_id
        self._conn: sqlite3.Connection | None = None
        self._lamport_clock: int = 0

    def connect(self) -> None:
        """Initialize database connection and schema."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(CRDT_SCHEMA)
        self._conn.commit()

        # Initialize Lamport clock from highest seen timestamp
        cursor = self._conn.execute("SELECT MAX(lamport_ts) FROM crdt_log")
        row = cursor.fetchone()
        if row[0] is not None:
            self._lamport_clock = row[0]

        logger.info(
            f"CRDTLog connected to {self.db_path}, "
            f"lamport_clock={self._lamport_clock}"
        )

    def close(self) -> None:
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def _ensure_connected(self) -> sqlite3.Connection:
        """Ensure database connection exists."""
        if self._conn is None:
            self.connect()
        return self._conn

    def _tick(self) -> int:
        """Increment and return the Lamport clock."""
        self._lamport_clock += 1
        return self._lamport_clock

    def _update_clock(self, remote_ts: int) -> None:
        """Update Lamport clock based on remote timestamp."""
        self._lamport_clock = max(self._lamport_clock, remote_ts) + 1

    def append(self, event_type: str, payload: dict[str, Any]) -> LogEntry:
        """Append a new entry to the log.

        Args:
            event_type: Type of event ("reading", "observation", "memory").
            payload: Event data as dictionary.

        Returns:
            The created LogEntry.
        """
        conn = self._ensure_connected()

        entry = LogEntry(
            id=str(uuid.uuid4()),
            lamport_ts=self._tick(),
            node_id=self.node_id,
            event_type=event_type,
            payload=payload,
            created_at=datetime.now(),
            synced_at=None,
        )

        conn.execute(
            """
            INSERT INTO crdt_log (
                id, lamport_ts, node_id, event_type, payload, created_at, synced_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.id,
                entry.lamport_ts,
                entry.node_id,
                entry.event_type,
                json.dumps(entry.payload),
                entry.created_at.isoformat(),
                None,
            ),
        )
        conn.commit()

        logger.debug(f"Appended entry {entry.id} with ts={entry.lamport_ts}")
        return entry

    def get_unsynced(self, limit: int = 1000) -> list[LogEntry]:
        """Get entries that haven't been synced yet.

        Args:
            limit: Maximum entries to return.

        Returns:
            List of unsynced LogEntry objects, oldest first.
        """
        conn = self._ensure_connected()

        cursor = conn.execute(
            """
            SELECT id, lamport_ts, node_id, event_type, payload, created_at, synced_at
            FROM crdt_log
            WHERE synced_at IS NULL
            ORDER BY lamport_ts ASC
            LIMIT ?
            """,
            (limit,),
        )

        entries = []
        for row in cursor:
            entries.append(
                LogEntry(
                    id=row["id"],
                    lamport_ts=row["lamport_ts"],
                    node_id=row["node_id"],
                    event_type=row["event_type"],
                    payload=json.loads(row["payload"]),
                    created_at=datetime.fromisoformat(row["created_at"]),
                    synced_at=None,
                )
            )

        return entries

    def get_entries_since(
        self, lamport_ts: int, limit: int = 1000
    ) -> list[LogEntry]:
        """Get entries with timestamp greater than specified.

        Args:
            lamport_ts: Minimum Lamport timestamp (exclusive).
            limit: Maximum entries to return.

        Returns:
            List of LogEntry objects, oldest first.
        """
        conn = self._ensure_connected()

        cursor = conn.execute(
            """
            SELECT id, lamport_ts, node_id, event_type, payload, created_at, synced_at
            FROM crdt_log
            WHERE lamport_ts > ?
            ORDER BY lamport_ts ASC
            LIMIT ?
            """,
            (lamport_ts, limit),
        )

        entries = []
        for row in cursor:
            entries.append(
                LogEntry(
                    id=row["id"],
                    lamport_ts=row["lamport_ts"],
                    node_id=row["node_id"],
                    event_type=row["event_type"],
                    payload=json.loads(row["payload"]),
                    created_at=datetime.fromisoformat(row["created_at"]),
                    synced_at=(
                        datetime.fromisoformat(row["synced_at"])
                        if row["synced_at"]
                        else None
                    ),
                )
            )

        return entries

    def mark_synced(self, entry_ids: list[str]) -> int:
        """Mark entries as synced.

        Args:
            entry_ids: List of entry IDs to mark.

        Returns:
            Number of entries updated.
        """
        if not entry_ids:
            return 0

        conn = self._ensure_connected()

        now = datetime.now().isoformat()
        placeholders = ",".join("?" * len(entry_ids))

        cursor = conn.execute(
            f"""
            UPDATE crdt_log
            SET synced_at = ?
            WHERE id IN ({placeholders}) AND synced_at IS NULL
            """,
            (now, *entry_ids),
        )
        conn.commit()

        count = cursor.rowcount
        logger.debug(f"Marked {count} entries as synced")
        return count

    def merge(self, remote_entries: list[LogEntry]) -> int:
        """Merge remote entries into the local log.

        Uses Lamport timestamps and entry IDs to handle conflicts:
        - Entries with same ID are skipped (idempotent)
        - Updates local clock to max of local and remote timestamps

        Args:
            remote_entries: List of entries from remote node.

        Returns:
            Number of new entries added.
        """
        if not remote_entries:
            return 0

        conn = self._ensure_connected()

        added = 0
        for entry in remote_entries:
            # Update our Lamport clock
            self._update_clock(entry.lamport_ts)

            # Check if entry already exists
            existing = conn.execute(
                "SELECT 1 FROM crdt_log WHERE id = ?", (entry.id,)
            ).fetchone()

            if existing:
                continue  # Skip duplicate

            # Insert new entry
            conn.execute(
                """
                INSERT INTO crdt_log (
                    id, lamport_ts, node_id, event_type, payload,
                    created_at, synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.id,
                    entry.lamport_ts,
                    entry.node_id,
                    entry.event_type,
                    json.dumps(entry.payload),
                    entry.created_at.isoformat(),
                    datetime.now().isoformat(),  # Mark as synced on receipt
                ),
            )
            added += 1

        conn.commit()
        logger.info(f"Merged {added} new entries from remote")
        return added

    def get_latest_timestamp(self) -> int:
        """Get the highest Lamport timestamp in the log.

        Returns:
            Highest timestamp, or 0 if log is empty.
        """
        conn = self._ensure_connected()

        cursor = conn.execute("SELECT MAX(lamport_ts) FROM crdt_log")
        row = cursor.fetchone()
        return row[0] if row[0] is not None else 0

    def get_stats(self) -> dict[str, Any]:
        """Get log statistics.

        Returns:
            Dictionary with entry counts and other stats.
        """
        conn = self._ensure_connected()

        stats = {
            "lamport_clock": self._lamport_clock,
            "node_id": self.node_id,
        }

        # Total entries
        cursor = conn.execute("SELECT COUNT(*) FROM crdt_log")
        stats["total_entries"] = cursor.fetchone()[0]

        # Unsynced entries
        cursor = conn.execute(
            "SELECT COUNT(*) FROM crdt_log WHERE synced_at IS NULL"
        )
        stats["unsynced_entries"] = cursor.fetchone()[0]

        # Entries by type
        cursor = conn.execute(
            "SELECT event_type, COUNT(*) FROM crdt_log GROUP BY event_type"
        )
        stats["entries_by_type"] = {row[0]: row[1] for row in cursor}

        # Database size
        if self.db_path.exists():
            stats["db_size_mb"] = round(
                self.db_path.stat().st_size / (1024 * 1024), 2
            )

        return stats

    def cleanup_old_entries(self, days: int = 90) -> int:
        """Delete entries older than specified days that are synced.

        Args:
            days: Age threshold in days.

        Returns:
            Number of entries deleted.
        """
        from datetime import timedelta

        conn = self._ensure_connected()

        cutoff = (datetime.now() - timedelta(days=days)).isoformat()

        cursor = conn.execute(
            """
            DELETE FROM crdt_log
            WHERE created_at < ? AND synced_at IS NOT NULL
            """,
            (cutoff,),
        )
        conn.commit()

        deleted = cursor.rowcount
        if deleted > 0:
            logger.info(f"Cleaned up {deleted} synced entries older than {days} days")

        return deleted
