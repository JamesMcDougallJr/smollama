"""Tests for reading tools: read_source, list_sources, get_reading_history."""

import pytest
from datetime import datetime
from unittest.mock import MagicMock, AsyncMock

from smollama.readings import Reading, ReadingManager
from smollama.memory import LocalStore, MockEmbeddings
from smollama.tools.reading_tools import (
    ReadSourceTool,
    ListSourcesTool,
    GetReadingHistoryTool,
)


@pytest.fixture
def mock_manager():
    """Create a mock ReadingManager."""
    manager = MagicMock(spec=ReadingManager)
    manager.source_types = ["gpio", "system"]
    return manager


@pytest.fixture
def mock_store():
    """Create an in-memory LocalStore."""
    store = LocalStore(":memory:", "test-node", MockEmbeddings())
    store.connect()
    yield store
    store.close()


class TestReadSourceTool:
    """Tests for the read_source tool."""

    def test_read_source_properties(self, mock_manager):
        """Test read_source tool properties."""
        tool = ReadSourceTool(mock_manager)

        assert tool.name == "read_source"
        assert "read" in tool.description.lower()
        assert len(tool.parameters) == 1
        assert tool.parameters[0].name == "source_id"

    def test_read_source_ollama_format(self, mock_manager):
        """Test read_source Ollama format."""
        tool = ReadSourceTool(mock_manager)
        fmt = tool.to_ollama_format()

        assert fmt["type"] == "function"
        assert fmt["function"]["name"] == "read_source"
        params = fmt["function"]["parameters"]["properties"]
        assert "source_id" in params

    @pytest.mark.asyncio
    async def test_read_source_valid_id(self, mock_manager):
        """Test reading from a valid source."""
        mock_reading = Reading(
            source_type="system",
            source_id="cpu_temp",
            value=45.5,
            timestamp=datetime.now(),
            unit="celsius",
            metadata={"zone": "thermal_zone0"},
        )
        mock_manager.read = AsyncMock(return_value=mock_reading)

        tool = ReadSourceTool(mock_manager)
        result = await tool.execute(source_id="system:cpu_temp")

        assert result["source_id"] == "system:cpu_temp"
        assert result["value"] == 45.5
        assert result["unit"] == "celsius"
        assert "timestamp" in result
        assert result["metadata"] == {"zone": "thermal_zone0"}
        mock_manager.read.assert_called_once_with("system:cpu_temp")

    @pytest.mark.asyncio
    async def test_read_source_invalid_id(self, mock_manager):
        """Test reading from invalid source."""
        mock_manager.read = AsyncMock(return_value=None)

        tool = ReadSourceTool(mock_manager)
        result = await tool.execute(source_id="unknown:123")

        assert "error" in result
        assert "not found" in result["error"].lower()
        assert "available_types" in result

    @pytest.mark.asyncio
    async def test_read_source_gpio(self, mock_manager):
        """Test reading a GPIO pin."""
        mock_reading = Reading(
            source_type="gpio",
            source_id="17",
            value=1,
            timestamp=datetime.now(),
            unit="boolean",
            metadata={"name": "motion", "mode": "input"},
        )
        mock_manager.read = AsyncMock(return_value=mock_reading)

        tool = ReadSourceTool(mock_manager)
        result = await tool.execute(source_id="gpio:17")

        assert result["source_id"] == "gpio:17"
        assert result["value"] == 1


class TestListSourcesTool:
    """Tests for the list_sources tool."""

    def test_list_sources_properties(self, mock_manager):
        """Test list_sources tool properties."""
        tool = ListSourcesTool(mock_manager)

        assert tool.name == "list_sources"
        assert "list" in tool.description.lower()
        assert len(tool.parameters) == 1

    def test_list_sources_ollama_format(self, mock_manager):
        """Test list_sources Ollama format."""
        tool = ListSourcesTool(mock_manager)
        fmt = tool.to_ollama_format()

        assert fmt["type"] == "function"
        assert fmt["function"]["name"] == "list_sources"

    @pytest.mark.asyncio
    async def test_list_sources_all(self, mock_manager):
        """Test listing all sources."""
        mock_manager.list_sources = MagicMock(
            return_value=[
                "gpio:17",
                "gpio:27",
                "system:cpu_temp",
                "system:mem_percent",
            ]
        )

        tool = ListSourcesTool(mock_manager)
        result = await tool.execute()

        assert result["total_count"] == 4
        assert "sources_by_type" in result
        assert "gpio" in result["sources_by_type"]
        assert "system" in result["sources_by_type"]
        assert result["sources_by_type"]["gpio"] == ["17", "27"]
        assert result["sources_by_type"]["system"] == ["cpu_temp", "mem_percent"]
        assert "all_sources" in result
        mock_manager.list_sources.assert_called_once_with(None)

    @pytest.mark.asyncio
    async def test_list_sources_filtered(self, mock_manager):
        """Test listing sources filtered by type."""
        mock_manager.list_sources = MagicMock(
            return_value=["gpio:17", "gpio:27"]
        )

        tool = ListSourcesTool(mock_manager)
        result = await tool.execute(source_type="gpio")

        assert result["total_count"] == 2
        assert "gpio" in result["sources_by_type"]
        assert len(result["sources_by_type"]["gpio"]) == 2
        mock_manager.list_sources.assert_called_once_with("gpio")

    @pytest.mark.asyncio
    async def test_list_sources_empty(self, mock_manager):
        """Test listing sources when none available."""
        mock_manager.list_sources = MagicMock(return_value=[])

        tool = ListSourcesTool(mock_manager)
        result = await tool.execute()

        assert result["total_count"] == 0
        assert result["sources_by_type"] == {}
        assert result["all_sources"] == []


class TestGetReadingHistoryTool:
    """Tests for the get_reading_history tool."""

    def test_get_reading_history_properties(self, mock_store):
        """Test get_reading_history tool properties."""
        tool = GetReadingHistoryTool(mock_store)

        assert tool.name == "get_reading_history"
        assert "historical" in tool.description.lower()
        assert len(tool.parameters) == 3

    def test_get_reading_history_ollama_format(self, mock_store):
        """Test get_reading_history Ollama format."""
        tool = GetReadingHistoryTool(mock_store)
        fmt = tool.to_ollama_format()

        assert fmt["type"] == "function"
        assert fmt["function"]["name"] == "get_reading_history"
        params = fmt["function"]["parameters"]["properties"]
        assert "source_id" in params
        assert "minutes" in params
        assert "limit" in params

    @pytest.mark.asyncio
    async def test_get_reading_history_with_data(self, mock_store):
        """Test getting history with readings."""
        # Add some readings
        now = datetime.now()
        for i in range(5):
            reading = Reading(
                source_type="system",
                source_id="cpu_temp",
                value=40.0 + i,
                timestamp=now,
                unit="celsius",
            )
            mock_store.log_reading(reading)

        tool = GetReadingHistoryTool(mock_store)
        result = await tool.execute(source_id="system:cpu_temp")

        assert result["source_id"] == "system:cpu_temp"
        assert result["count"] == 5
        assert result["period_minutes"] == 60  # Default
        assert "readings" in result
        assert len(result["readings"]) == 5

    @pytest.mark.asyncio
    async def test_get_reading_history_statistics(self, mock_store):
        """Test statistics are calculated for numeric values."""
        now = datetime.now()
        for value in [10.0, 20.0, 30.0, 40.0, 50.0]:
            reading = Reading("system", "cpu_temp", value, now, "celsius")
            mock_store.log_reading(reading)

        tool = GetReadingHistoryTool(mock_store)
        result = await tool.execute(source_id="system:cpu_temp")

        assert "statistics" in result
        stats = result["statistics"]
        assert stats["min"] == 10.0
        assert stats["max"] == 50.0
        assert stats["avg"] == 30.0

    @pytest.mark.asyncio
    async def test_get_reading_history_no_data(self, mock_store):
        """Test getting history when no readings exist."""
        tool = GetReadingHistoryTool(mock_store)
        result = await tool.execute(source_id="system:cpu_temp")

        assert result["source_id"] == "system:cpu_temp"
        assert result["count"] == 0
        assert result["readings"] == []
        assert "message" in result
        assert "no readings" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_get_reading_history_custom_minutes(self, mock_store):
        """Test history with custom time range."""
        now = datetime.now()
        reading = Reading("system", "cpu_temp", 45.0, now, "celsius")
        mock_store.log_reading(reading)

        tool = GetReadingHistoryTool(mock_store)
        result = await tool.execute(source_id="system:cpu_temp", minutes=30)

        assert result["period_minutes"] == 30

    @pytest.mark.asyncio
    async def test_get_reading_history_custom_limit(self, mock_store):
        """Test history respects limit parameter."""
        now = datetime.now()
        for i in range(10):
            reading = Reading("system", "cpu_temp", 40.0 + i, now, "celsius")
            mock_store.log_reading(reading)

        tool = GetReadingHistoryTool(mock_store)
        result = await tool.execute(source_id="system:cpu_temp", limit=3)

        assert result["count"] == 3
        assert len(result["readings"]) == 3

    @pytest.mark.asyncio
    async def test_get_reading_history_non_numeric(self, mock_store):
        """Test history with non-numeric values."""
        now = datetime.now()
        for state in [1, 0, 1, 0]:
            reading = Reading("gpio", "17", state, now, "boolean")
            mock_store.log_reading(reading)

        tool = GetReadingHistoryTool(mock_store)
        result = await tool.execute(source_id="gpio:17")

        assert result["count"] == 4
        # Statistics should still work for 0/1 values
        assert "statistics" in result
