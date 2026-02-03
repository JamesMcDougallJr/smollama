"""Tests for Mem0 integration: client, bridge, and cross-node recall tool."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from smollama.config import Mem0Config
from smollama.mem0.client import Mem0Client
from smollama.mem0.bridge import Mem0Bridge
from smollama.mem0.tools import CrossNodeRecallTool
from smollama.sync.crdt_log import CRDTLog, LogEntry
from datetime import datetime


class TestMem0Client:
    """Tests for the Mem0Client."""

    def test_client_initialization(self):
        """Test client initializes with correct URL."""
        client = Mem0Client("http://localhost:8050")
        assert client.server_url == "http://localhost:8050"

        # Test trailing slash is stripped
        client2 = Mem0Client("http://localhost:8050/")
        assert client2.server_url == "http://localhost:8050"

    @pytest.mark.asyncio
    async def test_health_check_success(self):
        """Test health check returns True when server is healthy."""
        client = Mem0Client("http://localhost:8050")

        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_http.get = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_http

            result = await client.health_check()
            assert result is True

    @pytest.mark.asyncio
    async def test_health_check_failure(self):
        """Test health check returns False when server is down."""
        client = Mem0Client("http://localhost:8050")

        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.get = AsyncMock(side_effect=Exception("Connection refused"))
            mock_get.return_value = mock_http

            result = await client.health_check()
            assert result is False

    @pytest.mark.asyncio
    async def test_add_memory(self):
        """Test adding a memory."""
        client = Mem0Client("http://localhost:8050")

        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"id": "mem-123"}
            mock_response.raise_for_status = MagicMock()
            mock_http.post = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_http

            result = await client.add_memory(
                text="Test memory",
                user_id="test-node",
                agent_id="observations",
                metadata={"confidence": 0.9},
            )

            assert result == {"id": "mem-123"}
            mock_http.post.assert_called_once()
            call_args = mock_http.post.call_args
            assert call_args[0][0] == "/v1/memories/"
            payload = call_args[1]["json"]
            assert payload["user_id"] == "test-node"
            assert payload["agent_id"] == "observations"

    @pytest.mark.asyncio
    async def test_search_memories(self):
        """Test searching memories."""
        client = Mem0Client("http://localhost:8050")

        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "results": [
                    {"memory": "Motion detected", "score": 0.95, "user_id": "node-1"},
                    {"memory": "Temperature normal", "score": 0.75, "user_id": "node-2"},
                ]
            }
            mock_response.raise_for_status = MagicMock()
            mock_http.post = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_http

            result = await client.search_memories(
                query="motion detected",
                limit=10,
            )

            assert len(result) == 2
            assert result[0]["memory"] == "Motion detected"
            assert result[0]["score"] == 0.95

    @pytest.mark.asyncio
    async def test_search_memories_with_filters(self):
        """Test searching with node and type filters."""
        client = Mem0Client("http://localhost:8050")

        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"results": []}
            mock_response.raise_for_status = MagicMock()
            mock_http.post = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_http

            await client.search_memories(
                query="test",
                user_id="specific-node",
                agent_id="observations",
            )

            call_args = mock_http.post.call_args
            payload = call_args[1]["json"]
            assert payload["user_id"] == "specific-node"
            assert payload["agent_id"] == "observations"


class TestMem0Bridge:
    """Tests for the Mem0Bridge."""

    @pytest.fixture
    def mock_config(self):
        """Create a mock Mem0Config."""
        return Mem0Config(
            enabled=True,
            server_url="http://localhost:8050",
            bridge_enabled=True,
            index_observations=True,
            index_memories=True,
            bridge_interval_seconds=30,
        )

    @pytest.fixture
    def mock_crdt_log(self, tmp_path):
        """Create a mock CRDT log."""
        log = CRDTLog(tmp_path / "sync.db", "test-node")
        log.connect()
        yield log
        log.close()

    def test_bridge_initialization(self, mock_config, mock_crdt_log):
        """Test bridge initializes correctly."""
        bridge = Mem0Bridge(mock_config, mock_crdt_log)

        assert bridge.config == mock_config
        assert bridge.crdt_log == mock_crdt_log
        assert bridge._running is False
        assert bridge._last_indexed_ts == 0

    @pytest.mark.asyncio
    async def test_bridge_start_requires_healthy_server(self, mock_config, mock_crdt_log):
        """Test bridge doesn't start if server unhealthy."""
        bridge = Mem0Bridge(mock_config, mock_crdt_log)

        with patch.object(bridge.client, "health_check", return_value=False):
            await bridge.start()
            assert bridge._running is False
            assert bridge._task is None

    @pytest.mark.asyncio
    async def test_bridge_get_stats(self, mock_config, mock_crdt_log):
        """Test bridge stats reporting."""
        bridge = Mem0Bridge(mock_config, mock_crdt_log)

        stats = bridge.get_stats()
        assert stats["running"] is False
        assert stats["last_indexed_ts"] == 0
        assert stats["indexed_count"] == 0
        assert stats["interval_seconds"] == 30
        assert stats["server_url"] == "http://localhost:8050"

    @pytest.mark.asyncio
    async def test_bridge_indexes_observations(self, mock_config, mock_crdt_log):
        """Test bridge indexes observation entries."""
        bridge = Mem0Bridge(mock_config, mock_crdt_log)

        # Add an observation to CRDT log
        mock_crdt_log.append("observation", {
            "text": "Temperature spike detected",
            "type": "anomaly",
            "confidence": 0.9,
        })

        with patch.object(bridge.client, "add_memory", new_callable=AsyncMock) as mock_add:
            mock_add.return_value = {"id": "mem-1"}
            await bridge._index_new_entries()

            mock_add.assert_called_once()
            call_args = mock_add.call_args
            assert call_args[1]["text"] == "Temperature spike detected"
            assert call_args[1]["user_id"] == "test-node"
            assert call_args[1]["agent_id"] == "observations"

    @pytest.mark.asyncio
    async def test_bridge_indexes_memories(self, mock_config, mock_crdt_log):
        """Test bridge indexes memory entries."""
        bridge = Mem0Bridge(mock_config, mock_crdt_log)

        # Add a memory to CRDT log
        mock_crdt_log.append("memory", {
            "text": "Normal temperature is 40-50C",
            "confidence": 0.85,
            "times_confirmed": 3,
        })

        with patch.object(bridge.client, "add_memory", new_callable=AsyncMock) as mock_add:
            mock_add.return_value = {"id": "mem-1"}
            await bridge._index_new_entries()

            mock_add.assert_called_once()
            call_args = mock_add.call_args
            assert call_args[1]["text"] == "Normal temperature is 40-50C"
            assert call_args[1]["agent_id"] == "memories"

    @pytest.mark.asyncio
    async def test_bridge_skips_readings(self, mock_config, mock_crdt_log):
        """Test bridge skips reading entries (high volume, low semantic value)."""
        bridge = Mem0Bridge(mock_config, mock_crdt_log)

        # Add a reading to CRDT log
        mock_crdt_log.append("reading", {
            "source_id": "system:cpu_temp",
            "value": 45.5,
        })

        with patch.object(bridge.client, "add_memory", new_callable=AsyncMock) as mock_add:
            await bridge._index_new_entries()
            mock_add.assert_not_called()

    @pytest.mark.asyncio
    async def test_bridge_deduplicates_entries(self, mock_config, mock_crdt_log):
        """Test bridge doesn't re-index same entry."""
        bridge = Mem0Bridge(mock_config, mock_crdt_log)

        # Add an observation
        mock_crdt_log.append("observation", {"text": "Test observation"})

        with patch.object(bridge.client, "add_memory", new_callable=AsyncMock) as mock_add:
            mock_add.return_value = {"id": "mem-1"}

            # Index twice
            await bridge._index_new_entries()
            await bridge._index_new_entries()

            # Should only be called once
            assert mock_add.call_count == 1


class TestCrossNodeRecallTool:
    """Tests for the CrossNodeRecallTool."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock Mem0Client."""
        return MagicMock(spec=Mem0Client)

    def test_tool_properties(self, mock_client):
        """Test tool properties."""
        tool = CrossNodeRecallTool(mock_client)

        assert tool.name == "cross_node_recall"
        assert "all nodes" in tool.description.lower()
        assert len(tool.parameters) == 4

    def test_tool_ollama_format(self, mock_client):
        """Test tool Ollama format."""
        tool = CrossNodeRecallTool(mock_client)
        fmt = tool.to_ollama_format()

        assert fmt["type"] == "function"
        assert fmt["function"]["name"] == "cross_node_recall"
        params = fmt["function"]["parameters"]["properties"]
        assert "query" in params
        assert "node_filter" in params
        assert "type_filter" in params
        assert "limit" in params

    def test_tool_has_type_filter_enum(self, mock_client):
        """Test type_filter has valid enum values."""
        tool = CrossNodeRecallTool(mock_client)
        type_param = next(p for p in tool.parameters if p.name == "type_filter")
        assert type_param.enum == ["observations", "memories"]

    @pytest.mark.asyncio
    async def test_tool_search_all_nodes(self, mock_client):
        """Test searching across all nodes."""
        tool = CrossNodeRecallTool(mock_client)

        mock_client.search_memories = AsyncMock(return_value=[
            {
                "memory": "Motion detected",
                "score": 0.95,
                "metadata": {"source_node": "node-1", "observation_type": "anomaly"},
            },
            {
                "memory": "Door opened",
                "score": 0.85,
                "metadata": {"source_node": "node-2", "observation_type": "status"},
            },
        ])

        result = await tool.execute(query="motion activity")

        assert result["query"] == "motion activity"
        assert result["total_results"] == 2
        assert "node-1" in result["nodes_with_results"]
        assert "node-2" in result["nodes_with_results"]

    @pytest.mark.asyncio
    async def test_tool_search_with_node_filter(self, mock_client):
        """Test searching specific node."""
        tool = CrossNodeRecallTool(mock_client)

        mock_client.search_memories = AsyncMock(return_value=[])

        await tool.execute(query="test", node_filter="alpaca-living-room")

        mock_client.search_memories.assert_called_with(
            query="test",
            user_id="alpaca-living-room",
            agent_id=None,
            limit=10,
        )

    @pytest.mark.asyncio
    async def test_tool_search_with_type_filter(self, mock_client):
        """Test searching specific type."""
        tool = CrossNodeRecallTool(mock_client)

        mock_client.search_memories = AsyncMock(return_value=[])

        await tool.execute(query="test", type_filter="observations")

        mock_client.search_memories.assert_called_with(
            query="test",
            user_id=None,
            agent_id="observations",
            limit=10,
        )

    @pytest.mark.asyncio
    async def test_tool_handles_errors(self, mock_client):
        """Test tool handles errors gracefully."""
        tool = CrossNodeRecallTool(mock_client)

        mock_client.search_memories = AsyncMock(side_effect=Exception("Connection error"))

        result = await tool.execute(query="test")

        assert "error" in result
        assert "Connection error" in result["error"]
        assert result["results"] == []

    @pytest.mark.asyncio
    async def test_tool_formats_results(self, mock_client):
        """Test result formatting for LLM consumption."""
        tool = CrossNodeRecallTool(mock_client)

        mock_client.search_memories = AsyncMock(return_value=[
            {
                "memory": "Test memory",
                "score": 0.92,
                "metadata": {
                    "source_node": "test-node",
                    "observation_type": "pattern",
                    "confidence": 0.85,
                    "created_at": "2026-02-03T10:00:00",
                },
            }
        ])

        result = await tool.execute(query="test")

        assert len(result["results"]) == 1
        item = result["results"][0]
        assert item["text"] == "Test memory"
        assert item["relevance"] == 0.92
        assert item["node"] == "test-node"
        assert item["type"] == "pattern"
        assert item["confidence"] == 0.85
        assert item["timestamp"] == "2026-02-03T10:00:00"


class TestMem0Config:
    """Tests for Mem0Config dataclass."""

    def test_default_values(self):
        """Test default configuration values."""
        config = Mem0Config()

        assert config.enabled is False
        assert config.server_url == "http://localhost:8050"
        assert config.bridge_enabled is False
        assert config.index_observations is True
        assert config.index_memories is True
        assert config.bridge_interval_seconds == 30
        assert config.compose_file == "deploy/mem0/docker-compose.yml"

    def test_custom_values(self):
        """Test custom configuration values."""
        config = Mem0Config(
            enabled=True,
            server_url="http://mem0.local:8050",
            bridge_enabled=True,
            index_observations=False,
            bridge_interval_seconds=60,
        )

        assert config.enabled is True
        assert config.server_url == "http://mem0.local:8050"
        assert config.bridge_enabled is True
        assert config.index_observations is False
        assert config.bridge_interval_seconds == 60
