"""Tests for memory tools: recall, remember, observe."""

import pytest
from unittest.mock import MagicMock

from smollama.memory import LocalStore, MockEmbeddings
from smollama.tools.memory_tools import RecallTool, RememberTool, ObserveTool


@pytest.fixture
def mock_store():
    """Create a mock LocalStore with in-memory database."""
    store = LocalStore(":memory:", "test-node", MockEmbeddings())
    store.connect()
    yield store
    store.close()


class TestRecallTool:
    """Tests for the recall tool."""

    def test_recall_tool_properties(self, mock_store):
        """Test recall tool properties."""
        tool = RecallTool(mock_store)

        assert tool.name == "recall"
        assert "memory" in tool.description.lower()
        assert len(tool.parameters) == 2

    def test_recall_tool_ollama_format(self, mock_store):
        """Test recall tool Ollama format."""
        tool = RecallTool(mock_store)
        fmt = tool.to_ollama_format()

        assert fmt["type"] == "function"
        assert fmt["function"]["name"] == "recall"
        assert "query" in fmt["function"]["parameters"]["properties"]

    @pytest.mark.asyncio
    async def test_recall_tool_returns_results(self, mock_store):
        """Test recall returns both observations and memories."""
        # Add some data
        mock_store.add_observation("Temperature is 45C", "status", 0.9)
        mock_store.add_memory("Normal temp is 40-50C", 0.85)

        tool = RecallTool(mock_store)
        result = await tool.execute(query="temperature")

        assert "query" in result
        assert result["query"] == "temperature"
        assert "observations" in result
        assert "memories" in result
        assert "total_results" in result

    @pytest.mark.asyncio
    async def test_recall_tool_empty_results(self, mock_store):
        """Test recall with no matching results."""
        tool = RecallTool(mock_store)
        result = await tool.execute(query="nonexistent query xyz")

        assert result["total_results"] == 0
        assert result["observations"] == []
        assert result["memories"] == []

    @pytest.mark.asyncio
    async def test_recall_tool_formats_observations(self, mock_store):
        """Test observation formatting in results."""
        mock_store.add_observation(
            text="Motion detected at 3pm",
            observation_type="anomaly",
            confidence=0.85,
            related_sources=["gpio:17"],
        )

        tool = RecallTool(mock_store)
        result = await tool.execute(query="motion")

        assert len(result["observations"]) >= 1
        obs = result["observations"][0]
        assert "text" in obs
        assert "timestamp" in obs
        assert "type" in obs
        assert "confidence" in obs
        assert "relevance" in obs

    @pytest.mark.asyncio
    async def test_recall_tool_formats_memories(self, mock_store):
        """Test memory formatting in results."""
        mock_store.add_memory("User prefers dark mode", 0.9)

        tool = RecallTool(mock_store)
        result = await tool.execute(query="user")

        assert len(result["memories"]) >= 1
        mem = result["memories"][0]
        assert "fact" in mem
        assert "confidence" in mem
        assert "times_confirmed" in mem
        assert "relevance" in mem

    @pytest.mark.asyncio
    async def test_recall_tool_custom_limit(self, mock_store):
        """Test recall with custom limit."""
        # Add multiple items
        for i in range(10):
            mock_store.add_observation(f"Test observation {i}", "status", 0.8)

        tool = RecallTool(mock_store)
        result = await tool.execute(query="test", limit=3)

        # Note: limit applies per category
        assert len(result["observations"]) <= 3


class TestRememberTool:
    """Tests for the remember tool."""

    def test_remember_tool_properties(self, mock_store):
        """Test remember tool properties."""
        tool = RememberTool(mock_store)

        assert tool.name == "remember"
        assert "fact" in tool.description.lower()
        assert len(tool.parameters) == 2

    def test_remember_tool_ollama_format(self, mock_store):
        """Test remember tool Ollama format."""
        tool = RememberTool(mock_store)
        fmt = tool.to_ollama_format()

        assert fmt["type"] == "function"
        assert fmt["function"]["name"] == "remember"
        params = fmt["function"]["parameters"]["properties"]
        assert "fact" in params
        assert "confidence" in params

    @pytest.mark.asyncio
    async def test_remember_tool_stores_fact(self, mock_store):
        """Test remember stores a new fact."""
        tool = RememberTool(mock_store)
        result = await tool.execute(fact="User prefers metric units", confidence=0.9)

        assert result["status"] == "stored"
        assert result["fact"] == "User prefers metric units"
        assert result["confidence"] == 0.9
        assert "memory_id" in result
        assert result["memory_id"] == 1

        # Verify in database
        assert mock_store.get_stats()["memories_count"] == 1

    @pytest.mark.asyncio
    async def test_remember_tool_default_confidence(self, mock_store):
        """Test remember with default confidence."""
        tool = RememberTool(mock_store)
        result = await tool.execute(fact="Some fact")

        assert result["confidence"] == 0.8  # Default

    @pytest.mark.asyncio
    async def test_remember_tool_clamps_confidence_high(self, mock_store):
        """Test confidence is clamped to 1.0 max."""
        tool = RememberTool(mock_store)
        result = await tool.execute(fact="Test fact", confidence=1.5)

        assert result["confidence"] == 1.0

    @pytest.mark.asyncio
    async def test_remember_tool_clamps_confidence_low(self, mock_store):
        """Test confidence is clamped to 0.0 min."""
        tool = RememberTool(mock_store)
        result = await tool.execute(fact="Test fact", confidence=-0.5)

        assert result["confidence"] == 0.0


class TestObserveTool:
    """Tests for the observe tool."""

    def test_observe_tool_properties(self, mock_store):
        """Test observe tool properties."""
        tool = ObserveTool(mock_store)

        assert tool.name == "observe"
        assert "observation" in tool.description.lower()
        assert len(tool.parameters) == 4

    def test_observe_tool_ollama_format(self, mock_store):
        """Test observe tool Ollama format."""
        tool = ObserveTool(mock_store)
        fmt = tool.to_ollama_format()

        assert fmt["type"] == "function"
        assert fmt["function"]["name"] == "observe"
        params = fmt["function"]["parameters"]["properties"]
        assert "observation" in params
        assert "observation_type" in params
        assert "related_sources" in params
        assert "confidence" in params

    def test_observe_tool_has_enum_for_type(self, mock_store):
        """Test observation_type has valid enum values."""
        tool = ObserveTool(mock_store)

        # Find the observation_type parameter
        type_param = next(p for p in tool.parameters if p.name == "observation_type")
        assert type_param.enum == ["pattern", "anomaly", "status", "general"]

    @pytest.mark.asyncio
    async def test_observe_tool_records_observation(self, mock_store):
        """Test observe records an observation."""
        tool = ObserveTool(mock_store)
        result = await tool.execute(
            observation="CPU temperature is unusually high",
            observation_type="anomaly",
            confidence=0.85,
        )

        assert result["status"] == "recorded"
        assert result["observation"] == "CPU temperature is unusually high"
        assert result["type"] == "anomaly"
        assert result["confidence"] == 0.85
        assert "observation_id" in result

        # Verify in database
        assert mock_store.get_stats()["observations_count"] == 1

    @pytest.mark.asyncio
    async def test_observe_tool_default_type(self, mock_store):
        """Test observe with default observation type."""
        tool = ObserveTool(mock_store)
        result = await tool.execute(observation="Generic observation")

        assert result["type"] == "general"

    @pytest.mark.asyncio
    async def test_observe_tool_with_related_sources(self, mock_store):
        """Test observe with related_sources."""
        tool = ObserveTool(mock_store)
        result = await tool.execute(
            observation="Temperature spike detected",
            observation_type="anomaly",
            related_sources=["system:cpu_temp", "gpio:17"],
        )

        assert result["related_sources"] == ["system:cpu_temp", "gpio:17"]

    @pytest.mark.asyncio
    async def test_observe_tool_clamps_confidence(self, mock_store):
        """Test observe clamps confidence to valid range."""
        tool = ObserveTool(mock_store)

        result = await tool.execute(observation="Test", confidence=2.0)
        assert result["confidence"] == 1.0

        result = await tool.execute(observation="Test 2", confidence=-1.0)
        assert result["confidence"] == 0.0

    @pytest.mark.asyncio
    async def test_observe_tool_valid_types(self, mock_store):
        """Test observe accepts all valid types."""
        tool = ObserveTool(mock_store)

        for obs_type in ["pattern", "anomaly", "status", "general"]:
            result = await tool.execute(
                observation=f"Test {obs_type}",
                observation_type=obs_type,
            )
            assert result["type"] == obs_type
