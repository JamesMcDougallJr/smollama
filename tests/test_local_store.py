"""Tests for LocalStore memory database."""

import json
import pytest
from datetime import datetime, timedelta

from smollama.memory import LocalStore, MockEmbeddings
from smollama.readings import Reading


@pytest.fixture
def mock_embeddings():
    """Create mock embeddings provider."""
    return MockEmbeddings(dimension=384)


@pytest.fixture
def store(mock_embeddings):
    """Create an in-memory LocalStore for testing."""
    store = LocalStore(":memory:", "test-node", mock_embeddings)
    store.connect()
    yield store
    store.close()


@pytest.fixture
def sample_reading():
    """Create a sample reading for testing."""
    return Reading(
        source_type="system",
        source_id="cpu_temp",
        value=45.5,
        timestamp=datetime.now(),
        unit="celsius",
        metadata={"zone": "thermal_zone0"},
    )


class TestLocalStoreSchema:
    """Tests for database schema initialization."""

    def test_connect_creates_tables(self, mock_embeddings):
        """Test that connect() creates all required tables."""
        store = LocalStore(":memory:", "test-node", mock_embeddings)
        store.connect()

        # Check tables exist by querying them
        conn = store._conn
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = [t[0] for t in tables]

        assert "readings_log" in table_names
        assert "observations" in table_names
        assert "memories" in table_names

        store.close()

    def test_connect_is_idempotent(self, store):
        """Test that calling connect() multiple times is safe."""
        # Already connected via fixture
        store.connect()
        store.connect()

        # Should still work
        stats = store.get_stats()
        assert "readings_count" in stats


class TestReadingOperations:
    """Tests for reading log operations."""

    def test_log_reading_single(self, store, sample_reading):
        """Test logging a single reading."""
        row_id = store.log_reading(sample_reading)

        assert row_id == 1

        # Verify it was stored
        stats = store.get_stats()
        assert stats["readings_count"] == 1

    def test_log_readings_batch(self, store):
        """Test batch logging of multiple readings."""
        now = datetime.now()
        readings = [
            Reading("system", "cpu_temp", 45.5, now, "celsius"),
            Reading("system", "mem_percent", 67.2, now, "percent"),
            Reading("gpio", "17", 1, now, "boolean"),
        ]

        count = store.log_readings(readings)

        assert count == 3
        assert store.get_stats()["readings_count"] == 3

    def test_log_readings_empty_list(self, store):
        """Test batch logging with empty list."""
        count = store.log_readings([])
        assert count == 0

    def test_log_reading_with_dict_value(self, store):
        """Test logging a reading with complex dict value."""
        reading = Reading(
            source_type="mqtt",
            source_id="sensor/data",
            value={"temp": 22.5, "humidity": 45},
            timestamp=datetime.now(),
            unit=None,
            metadata=None,
        )

        row_id = store.log_reading(reading)
        assert row_id == 1

    @pytest.mark.asyncio
    async def test_get_reading_history(self, store):
        """Test retrieving reading history for a source."""
        now = datetime.now()

        # Log some readings over time
        for i in range(5):
            reading = Reading(
                source_type="system",
                source_id="cpu_temp",
                value=40.0 + i,
                timestamp=now - timedelta(minutes=i * 5),
                unit="celsius",
            )
            store.log_reading(reading)

        # Get history
        history = await store.get_reading_history("system:cpu_temp", minutes=60)

        assert len(history) == 5
        # Most recent first
        assert history[0]["value"] == 40.0

    @pytest.mark.asyncio
    async def test_get_reading_history_with_limit(self, store):
        """Test reading history respects limit."""
        now = datetime.now()

        for i in range(10):
            reading = Reading("system", "cpu_temp", 40.0 + i, now - timedelta(minutes=i))
            store.log_reading(reading)

        history = await store.get_reading_history("system:cpu_temp", minutes=60, limit=3)
        assert len(history) == 3

    def test_get_recent_readings(self, store):
        """Test getting recent readings across all sources."""
        now = datetime.now()

        # Log readings from different sources
        store.log_reading(Reading("system", "cpu_temp", 45.5, now, "celsius"))
        store.log_reading(Reading("system", "mem_percent", 67.2, now, "percent"))
        store.log_reading(Reading("gpio", "17", 1, now, "boolean"))

        recent = store.get_recent_readings(minutes=60)

        assert len(recent) == 3
        full_ids = [r["full_id"] for r in recent]
        assert "system:cpu_temp" in full_ids
        assert "gpio:17" in full_ids

    def test_get_recent_readings_filtered(self, store):
        """Test filtering recent readings by source type."""
        now = datetime.now()

        store.log_reading(Reading("system", "cpu_temp", 45.5, now, "celsius"))
        store.log_reading(Reading("gpio", "17", 1, now, "boolean"))

        recent = store.get_recent_readings(minutes=60, source_types=["system"])

        assert len(recent) == 1
        assert recent[0]["full_id"] == "system:cpu_temp"


class TestObservationOperations:
    """Tests for observation storage and search."""

    def test_add_observation_basic(self, store):
        """Test adding a basic observation."""
        obs_id = store.add_observation(
            text="CPU temperature is stable around 45°C",
            observation_type="status",
            confidence=0.9,
        )

        assert obs_id == 1
        assert store.get_stats()["observations_count"] == 1

    def test_add_observation_with_related_sources(self, store):
        """Test adding observation with related sources."""
        obs_id = store.add_observation(
            text="Motion detected on sensor 17",
            observation_type="anomaly",
            confidence=0.85,
            related_sources=["gpio:17"],
        )

        assert obs_id == 1

    def test_search_observations_text_fallback(self, store):
        """Test text search when vector search unavailable."""
        # Add observations
        store.add_observation("Temperature spike detected", "anomaly", 0.9)
        store.add_observation("System running normally", "status", 0.8)
        store.add_observation("Temperature returned to normal", "status", 0.85)

        # Search (falls back to text search without sqlite-vec)
        results = store.search_observations("temperature")

        assert len(results) >= 1
        assert any("temperature" in r["text"].lower() for r in results)

    def test_search_observations_by_type(self, store):
        """Test filtering observations by type."""
        store.add_observation("Anomaly 1", "anomaly", 0.9)
        store.add_observation("Status update", "status", 0.8)
        store.add_observation("Anomaly 2", "anomaly", 0.85)

        results = store._text_search_observations("", limit=10, observation_type="anomaly")

        # All results should be anomalies (empty pattern matches all)
        # Note: this only works for text search fallback
        assert all(r["type"] == "anomaly" for r in results)


class TestMemoryOperations:
    """Tests for persistent memory storage and search."""

    def test_add_memory_new(self, store):
        """Test adding a new memory."""
        mem_id = store.add_memory(
            text="User prefers Celsius for temperature readings",
            confidence=0.9,
        )

        assert mem_id == 1
        assert store.get_stats()["memories_count"] == 1

    def test_add_memory_without_vec_no_reinforcement(self, store):
        """Test that without vector search, similar memories are not merged."""
        # Without sqlite-vec, add_memory won't detect duplicates
        mem_id1 = store.add_memory("User likes Celsius", 0.9)
        mem_id2 = store.add_memory("User prefers Celsius", 0.9)

        # Both are stored separately without vector search
        assert store.get_stats()["memories_count"] == 2

    def test_search_memories_text_fallback(self, store):
        """Test text search for memories."""
        store.add_memory("User prefers dark mode", 0.9)
        store.add_memory("System timezone is UTC", 0.85)
        store.add_memory("User location is kitchen", 0.8)

        results = store.search_memories("user")

        assert len(results) >= 1
        assert any("user" in r["text"].lower() for r in results)

    def test_search_memories_active_only(self, store):
        """Test that inactive memories are filtered by default."""
        mem_id = store.add_memory("Active memory", 0.9)
        store.add_memory("Another active memory", 0.8)

        # Deactivate one
        store.deactivate_memory(mem_id)

        results = store.search_memories("")

        # Should only return active memories
        assert store.get_stats()["memories_count"] == 1

    def test_deactivate_memory(self, store):
        """Test soft-deleting a memory."""
        mem_id = store.add_memory("Temporary memory", 0.7)

        result = store.deactivate_memory(mem_id)

        assert result is True
        assert store.get_stats()["memories_count"] == 0  # Only counts active

    def test_deactivate_memory_nonexistent(self, store):
        """Test deactivating a non-existent memory."""
        result = store.deactivate_memory(999)
        assert result is False


class TestCombinedSearch:
    """Tests for combined recall operation."""

    def test_recall_combined(self, store):
        """Test recall returns both observations and memories."""
        store.add_observation("Temperature at 45C", "status", 0.9)
        store.add_memory("Normal temp range is 40-50C", 0.85)

        results = store.recall("temperature")

        assert "observations" in results
        assert "memories" in results


class TestMaintenance:
    """Tests for maintenance operations."""

    def test_cleanup_old_readings(self, store):
        """Test deletion of old readings."""
        now = datetime.now()

        # Log old reading (100 days ago)
        old_reading = Reading(
            source_type="system",
            source_id="cpu_temp",
            value=45.0,
            timestamp=now - timedelta(days=100),
            unit="celsius",
        )
        store.log_reading(old_reading)

        # Log recent reading
        recent_reading = Reading(
            source_type="system",
            source_id="cpu_temp",
            value=46.0,
            timestamp=now,
            unit="celsius",
        )
        store.log_reading(recent_reading)

        assert store.get_stats()["readings_count"] == 2

        # Clean up readings older than 90 days
        deleted = store.cleanup_old_readings(days=90)

        assert deleted == 1
        assert store.get_stats()["readings_count"] == 1

    def test_get_stats(self, store):
        """Test database statistics."""
        # Empty database
        stats = store.get_stats()

        assert stats["readings_count"] == 0
        assert stats["observations_count"] == 0
        assert stats["memories_count"] == 0
        assert stats["vector_search_enabled"] is False
        # No db_size_mb for in-memory database

    def test_get_stats_with_data(self, store, sample_reading):
        """Test stats reflect stored data."""
        store.log_reading(sample_reading)
        store.add_observation("Test observation", "status", 0.8)
        store.add_memory("Test memory", 0.9)

        stats = store.get_stats()

        assert stats["readings_count"] == 1
        assert stats["observations_count"] == 1
        assert stats["memories_count"] == 1


class TestConnectionManagement:
    """Tests for connection lifecycle."""

    def test_close_connection(self, mock_embeddings):
        """Test closing database connection."""
        store = LocalStore(":memory:", "test-node", mock_embeddings)
        store.connect()

        store.close()

        assert store._conn is None

    def test_auto_reconnect(self, mock_embeddings):
        """Test that operations auto-reconnect if needed."""
        store = LocalStore(":memory:", "test-node", mock_embeddings)
        # Don't explicitly connect

        # This should auto-connect
        stats = store.get_stats()

        assert "readings_count" in stats
        store.close()
