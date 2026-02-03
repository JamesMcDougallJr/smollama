"""Local SQLite storage for readings, observations, and memories."""

import json
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..readings import Reading
from .embeddings import EmbeddingProvider

logger = logging.getLogger(__name__)

# SQL schema for the memory database
SCHEMA = """
-- Readings log: append-only sensor/system data
CREATE TABLE IF NOT EXISTS readings_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    full_id TEXT GENERATED ALWAYS AS (source_type || ':' || source_id) STORED,
    value TEXT NOT NULL,
    value_numeric REAL,
    unit TEXT,
    metadata TEXT,
    node_id TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_readings_source ON readings_log(source_type, source_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_readings_full_id ON readings_log(full_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_readings_timestamp ON readings_log(timestamp);

-- Observations: LLM-generated insights about readings
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    text TEXT NOT NULL,
    confidence REAL DEFAULT 0.8,
    observation_type TEXT DEFAULT 'general',
    related_sources TEXT,
    node_id TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_observations_timestamp ON observations(timestamp);
CREATE INDEX IF NOT EXISTS idx_observations_type ON observations(observation_type);

-- Memories: persistent facts that survive restarts
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    confidence REAL DEFAULT 0.8,
    times_confirmed INTEGER DEFAULT 1,
    active INTEGER DEFAULT 1,
    node_id TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_memories_active ON memories(active);
"""

# Vector table schema (sqlite-vec specific)
VECTOR_SCHEMA = """
-- Vector embeddings for observations (virtual table)
CREATE VIRTUAL TABLE IF NOT EXISTS observations_vec USING vec0(
    observation_id INTEGER PRIMARY KEY,
    embedding FLOAT[{dimension}] distance_metric=cosine
);

-- Vector embeddings for memories (virtual table)
CREATE VIRTUAL TABLE IF NOT EXISTS memories_vec USING vec0(
    memory_id INTEGER PRIMARY KEY,
    embedding FLOAT[{dimension}] distance_metric=cosine
);
"""


class LocalStore:
    """SQLite-based local storage with vector search capabilities."""

    def __init__(
        self,
        db_path: str | Path,
        node_id: str,
        embeddings: EmbeddingProvider,
    ):
        """Initialize the local store.

        Args:
            db_path: Path to SQLite database file.
            node_id: Unique identifier for this node.
            embeddings: Provider for generating text embeddings.
        """
        self.db_path = Path(db_path).expanduser()
        self.node_id = node_id
        self._embeddings = embeddings
        self._conn: sqlite3.Connection | None = None
        self._vec_available = False

    def connect(self) -> None:
        """Initialize database connection and schema."""
        # Ensure parent directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

        # Create base schema
        self._conn.executescript(SCHEMA)
        self._conn.commit()

        # Try to load sqlite-vec extension
        self._init_vector_tables()

        logger.info(f"LocalStore connected to {self.db_path}")

    def _init_vector_tables(self) -> None:
        """Initialize sqlite-vec virtual tables if available."""
        try:
            import sqlite_vec

            self._conn.enable_load_extension(True)
            sqlite_vec.load(self._conn)
            self._conn.enable_load_extension(False)

            # Create vector tables with correct dimension
            vec_schema = VECTOR_SCHEMA.format(dimension=self._embeddings.dimension)
            self._conn.executescript(vec_schema)
            self._conn.commit()

            self._vec_available = True
            logger.info(
                f"sqlite-vec loaded, dimension={self._embeddings.dimension}"
            )

        except ImportError:
            logger.warning(
                "sqlite-vec not available, semantic search disabled. "
                "Install with: pip install sqlite-vec"
            )
        except Exception as e:
            logger.warning(f"Failed to initialize sqlite-vec: {e}")

    def close(self) -> None:
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None
            logger.info("LocalStore connection closed")

    def _ensure_connected(self) -> sqlite3.Connection:
        """Ensure we have a database connection."""
        if self._conn is None:
            self.connect()
        return self._conn

    # ==================== Reading Operations ====================

    def log_reading(self, reading: Reading) -> int:
        """Log a reading to the database.

        Args:
            reading: Reading object to store.

        Returns:
            Row ID of the inserted reading.
        """
        conn = self._ensure_connected()

        # Extract numeric value for aggregation queries
        value_numeric = None
        if isinstance(reading.value, (int, float)):
            value_numeric = float(reading.value)

        cursor = conn.execute(
            """
            INSERT INTO readings_log (
                timestamp, source_type, source_id, value,
                value_numeric, unit, metadata, node_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reading.timestamp.isoformat(),
                reading.source_type,
                reading.source_id,
                json.dumps(reading.value),
                value_numeric,
                reading.unit,
                json.dumps(reading.metadata) if reading.metadata else None,
                self.node_id,
            ),
        )
        conn.commit()

        return cursor.lastrowid

    def log_readings(self, readings: list[Reading]) -> int:
        """Log multiple readings in a single transaction.

        Args:
            readings: List of Reading objects to store.

        Returns:
            Number of readings inserted.
        """
        if not readings:
            return 0

        conn = self._ensure_connected()

        rows = []
        for r in readings:
            value_numeric = None
            if isinstance(r.value, (int, float)):
                value_numeric = float(r.value)

            rows.append((
                r.timestamp.isoformat(),
                r.source_type,
                r.source_id,
                json.dumps(r.value),
                value_numeric,
                r.unit,
                json.dumps(r.metadata) if r.metadata else None,
                self.node_id,
            ))

        conn.executemany(
            """
            INSERT INTO readings_log (
                timestamp, source_type, source_id, value,
                value_numeric, unit, metadata, node_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()

        return len(rows)

    async def get_reading_history(
        self,
        full_id: str,
        minutes: int = 60,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Get historical readings for a source.

        Args:
            full_id: Full source ID (e.g., "gpio:17").
            minutes: How far back to look.
            limit: Maximum readings to return.

        Returns:
            List of reading dicts, newest first.
        """
        conn = self._ensure_connected()

        since = (datetime.now() - timedelta(minutes=minutes)).isoformat()

        cursor = conn.execute(
            """
            SELECT timestamp, value, value_numeric, unit, metadata
            FROM readings_log
            WHERE full_id = ? AND timestamp >= ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (full_id, since, limit),
        )

        results = []
        for row in cursor:
            value = json.loads(row["value"])
            results.append({
                "timestamp": row["timestamp"],
                "value": row["value_numeric"] if row["value_numeric"] is not None else value,
                "unit": row["unit"],
                "metadata": json.loads(row["metadata"]) if row["metadata"] else None,
            })

        return results

    def get_recent_readings(
        self,
        minutes: int = 60,
        source_types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Get recent readings across all sources.

        Args:
            minutes: How far back to look.
            source_types: Optional filter by source types.

        Returns:
            List of reading dicts grouped by source.
        """
        conn = self._ensure_connected()

        since = (datetime.now() - timedelta(minutes=minutes)).isoformat()

        if source_types:
            placeholders = ",".join("?" * len(source_types))
            cursor = conn.execute(
                f"""
                SELECT full_id, timestamp, value, value_numeric, unit
                FROM readings_log
                WHERE timestamp >= ? AND source_type IN ({placeholders})
                ORDER BY full_id, timestamp DESC
                """,
                (since, *source_types),
            )
        else:
            cursor = conn.execute(
                """
                SELECT full_id, timestamp, value, value_numeric, unit
                FROM readings_log
                WHERE timestamp >= ?
                ORDER BY full_id, timestamp DESC
                """,
                (since,),
            )

        results = []
        for row in cursor:
            value = json.loads(row["value"])
            results.append({
                "full_id": row["full_id"],
                "timestamp": row["timestamp"],
                "value": row["value_numeric"] if row["value_numeric"] is not None else value,
                "unit": row["unit"],
            })

        return results

    # ==================== Observation Operations ====================

    def add_observation(
        self,
        text: str,
        observation_type: str = "general",
        confidence: float = 0.8,
        related_sources: list[str] | None = None,
    ) -> int:
        """Store an observation with embedding.

        Args:
            text: Observation text.
            observation_type: Category (e.g., "pattern", "anomaly", "general").
            confidence: Confidence score 0-1.
            related_sources: List of source IDs this observation relates to.

        Returns:
            Row ID of the inserted observation.
        """
        conn = self._ensure_connected()

        timestamp = datetime.now().isoformat()

        cursor = conn.execute(
            """
            INSERT INTO observations (
                timestamp, text, confidence, observation_type,
                related_sources, node_id
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp,
                text,
                confidence,
                observation_type,
                json.dumps(related_sources) if related_sources else None,
                self.node_id,
            ),
        )
        observation_id = cursor.lastrowid

        # Add vector embedding if available
        if self._vec_available:
            embedding = self._embeddings.embed(text)
            conn.execute(
                "INSERT INTO observations_vec (observation_id, embedding) VALUES (?, ?)",
                (observation_id, embedding),
            )

        conn.commit()
        return observation_id

    def search_observations(
        self,
        query: str,
        limit: int = 10,
        observation_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Semantic search across observations.

        Args:
            query: Search query text.
            limit: Maximum results to return.
            observation_type: Optional filter by type.

        Returns:
            List of matching observations with similarity scores.
        """
        conn = self._ensure_connected()

        if not self._vec_available:
            # Fallback to basic text search
            return self._text_search_observations(query, limit, observation_type)

        # Vector similarity search
        query_embedding = self._embeddings.embed(query)

        cursor = conn.execute(
            """
            SELECT
                o.id, o.timestamp, o.text, o.confidence,
                o.observation_type, o.related_sources,
                v.distance
            FROM observations_vec v
            JOIN observations o ON o.id = v.observation_id
            WHERE v.embedding MATCH ?
            ORDER BY v.distance
            LIMIT ?
            """,
            (query_embedding, limit),
        )

        results = []
        for row in cursor:
            # Filter by type if specified
            if observation_type and row["observation_type"] != observation_type:
                continue

            results.append({
                "id": row["id"],
                "timestamp": row["timestamp"],
                "text": row["text"],
                "confidence": row["confidence"],
                "type": row["observation_type"],
                "related_sources": json.loads(row["related_sources"]) if row["related_sources"] else None,
                "similarity": 1 - row["distance"],  # Convert distance to similarity
            })

        return results

    def _text_search_observations(
        self,
        query: str,
        limit: int,
        observation_type: str | None,
    ) -> list[dict[str, Any]]:
        """Fallback text-based search for observations."""
        conn = self._ensure_connected()

        # Simple LIKE search
        pattern = f"%{query}%"

        if observation_type:
            cursor = conn.execute(
                """
                SELECT id, timestamp, text, confidence, observation_type, related_sources
                FROM observations
                WHERE text LIKE ? AND observation_type = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (pattern, observation_type, limit),
            )
        else:
            cursor = conn.execute(
                """
                SELECT id, timestamp, text, confidence, observation_type, related_sources
                FROM observations
                WHERE text LIKE ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (pattern, limit),
            )

        results = []
        for row in cursor:
            results.append({
                "id": row["id"],
                "timestamp": row["timestamp"],
                "text": row["text"],
                "confidence": row["confidence"],
                "type": row["observation_type"],
                "related_sources": json.loads(row["related_sources"]) if row["related_sources"] else None,
                "similarity": 0.5,  # Unknown similarity for text search
            })

        return results

    # ==================== Memory Operations ====================

    def add_memory(
        self,
        text: str,
        confidence: float = 0.8,
    ) -> int:
        """Store a new memory or reinforce an existing similar one.

        Args:
            text: Memory/fact text.
            confidence: Confidence score 0-1.

        Returns:
            Row ID of the memory (new or existing).
        """
        conn = self._ensure_connected()

        # Check for similar existing memory
        if self._vec_available:
            similar = self.search_memories(text, limit=1)
            if similar and similar[0]["similarity"] > 0.9:
                # Reinforce existing memory
                existing_id = similar[0]["id"]
                conn.execute(
                    """
                    UPDATE memories
                    SET times_confirmed = times_confirmed + 1,
                        confidence = MIN(1.0, confidence + 0.05),
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (datetime.now().isoformat(), existing_id),
                )
                conn.commit()
                logger.debug(f"Reinforced memory {existing_id}")
                return existing_id

        # Create new memory
        cursor = conn.execute(
            """
            INSERT INTO memories (text, confidence, node_id)
            VALUES (?, ?, ?)
            """,
            (text, confidence, self.node_id),
        )
        memory_id = cursor.lastrowid

        # Add vector embedding if available
        if self._vec_available:
            embedding = self._embeddings.embed(text)
            conn.execute(
                "INSERT INTO memories_vec (memory_id, embedding) VALUES (?, ?)",
                (memory_id, embedding),
            )

        conn.commit()
        return memory_id

    def search_memories(
        self,
        query: str,
        limit: int = 10,
        active_only: bool = True,
    ) -> list[dict[str, Any]]:
        """Semantic search across memories.

        Args:
            query: Search query text.
            limit: Maximum results to return.
            active_only: Only return active memories.

        Returns:
            List of matching memories with similarity scores.
        """
        conn = self._ensure_connected()

        if not self._vec_available:
            return self._text_search_memories(query, limit, active_only)

        query_embedding = self._embeddings.embed(query)

        cursor = conn.execute(
            """
            SELECT
                m.id, m.text, m.confidence, m.times_confirmed,
                m.active, m.created_at, m.updated_at,
                v.distance
            FROM memories_vec v
            JOIN memories m ON m.id = v.memory_id
            WHERE v.embedding MATCH ?
            ORDER BY v.distance
            LIMIT ?
            """,
            (query_embedding, limit * 2),  # Fetch extra to filter
        )

        results = []
        for row in cursor:
            if active_only and not row["active"]:
                continue

            results.append({
                "id": row["id"],
                "text": row["text"],
                "confidence": row["confidence"],
                "times_confirmed": row["times_confirmed"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "similarity": 1 - row["distance"],
            })

            if len(results) >= limit:
                break

        return results

    def _text_search_memories(
        self,
        query: str,
        limit: int,
        active_only: bool,
    ) -> list[dict[str, Any]]:
        """Fallback text-based search for memories."""
        conn = self._ensure_connected()

        pattern = f"%{query}%"

        if active_only:
            cursor = conn.execute(
                """
                SELECT id, text, confidence, times_confirmed, created_at, updated_at
                FROM memories
                WHERE text LIKE ? AND active = 1
                ORDER BY times_confirmed DESC, updated_at DESC
                LIMIT ?
                """,
                (pattern, limit),
            )
        else:
            cursor = conn.execute(
                """
                SELECT id, text, confidence, times_confirmed, active, created_at, updated_at
                FROM memories
                WHERE text LIKE ?
                ORDER BY times_confirmed DESC, updated_at DESC
                LIMIT ?
                """,
                (pattern, limit),
            )

        results = []
        for row in cursor:
            results.append({
                "id": row["id"],
                "text": row["text"],
                "confidence": row["confidence"],
                "times_confirmed": row["times_confirmed"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "similarity": 0.5,
            })

        return results

    def deactivate_memory(self, memory_id: int) -> bool:
        """Mark a memory as inactive.

        Args:
            memory_id: ID of memory to deactivate.

        Returns:
            True if memory was found and deactivated.
        """
        conn = self._ensure_connected()

        cursor = conn.execute(
            """
            UPDATE memories
            SET active = 0, updated_at = ?
            WHERE id = ?
            """,
            (datetime.now().isoformat(), memory_id),
        )
        conn.commit()

        return cursor.rowcount > 0

    # ==================== Combined Search ====================

    def recall(
        self,
        query: str,
        limit: int = 10,
    ) -> dict[str, list[dict[str, Any]]]:
        """Combined semantic search across observations and memories.

        Args:
            query: Search query text.
            limit: Maximum results per category.

        Returns:
            Dict with "observations" and "memories" lists.
        """
        return {
            "observations": self.search_observations(query, limit=limit),
            "memories": self.search_memories(query, limit=limit),
        }

    # ==================== Maintenance ====================

    def cleanup_old_readings(self, days: int = 90) -> int:
        """Delete readings older than specified days.

        Args:
            days: Age threshold in days.

        Returns:
            Number of readings deleted.
        """
        conn = self._ensure_connected()

        cutoff = (datetime.now() - timedelta(days=days)).isoformat()

        cursor = conn.execute(
            "DELETE FROM readings_log WHERE timestamp < ?",
            (cutoff,),
        )
        conn.commit()

        deleted = cursor.rowcount
        if deleted > 0:
            logger.info(f"Cleaned up {deleted} readings older than {days} days")

        return deleted

    def get_stats(self) -> dict[str, Any]:
        """Get database statistics.

        Returns:
            Dict with counts and size info.
        """
        conn = self._ensure_connected()

        stats = {}

        # Count readings
        cursor = conn.execute("SELECT COUNT(*) FROM readings_log")
        stats["readings_count"] = cursor.fetchone()[0]

        # Count observations
        cursor = conn.execute("SELECT COUNT(*) FROM observations")
        stats["observations_count"] = cursor.fetchone()[0]

        # Count memories
        cursor = conn.execute("SELECT COUNT(*) FROM memories WHERE active = 1")
        stats["memories_count"] = cursor.fetchone()[0]

        # Database file size
        if self.db_path.exists():
            stats["db_size_mb"] = round(self.db_path.stat().st_size / (1024 * 1024), 2)

        stats["vector_search_enabled"] = self._vec_available

        return stats
