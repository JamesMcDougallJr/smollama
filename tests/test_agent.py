"""Tests for agent functionality."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from smollama.config import (
    Config,
    NodeConfig,
    OllamaConfig,
    MQTTConfig,
    MQTTTopicsConfig,
    GPIOConfig,
    GPIOPinConfig,
    AgentConfig,
)
from smollama.agent import Agent
from smollama.ollama_client import ChatResponse, ToolCall


@pytest.fixture
def test_config():
    """Create a test configuration."""
    return Config(
        node=NodeConfig(name="test-node"),
        ollama=OllamaConfig(host="localhost", port=11434, model="llama3.2:1b"),
        mqtt=MQTTConfig(
            broker="localhost",
            port=1883,
            topics=MQTTTopicsConfig(
                subscribe=["test/#"],
                publish_prefix="test/node",
            ),
        ),
        gpio=GPIOConfig(
            pins=[GPIOPinConfig(pin=17, name="sensor", mode="input")],
            mock=True,
        ),
        agent=AgentConfig(system_prompt="Test prompt"),
    )


class TestAgent:
    """Tests for the Agent class."""

    def test_agent_initialization(self, test_config):
        """Test agent initializes components correctly."""
        agent = Agent(test_config)

        assert agent.config == test_config
        assert agent._ollama is not None
        assert agent._mqtt is not None
        assert agent._gpio is not None
        assert agent._tools is not None

    def test_agent_tools_registered(self, test_config):
        """Test that default tools are registered."""
        agent = Agent(test_config)
        tools = agent._tools.list_tools()

        tool_names = [t.name for t in tools]
        # Reading tools (new unified interface)
        assert "read_source" in tool_names
        assert "list_sources" in tool_names
        assert "get_reading_history" in tool_names
        # Memory tools
        assert "recall" in tool_names
        assert "remember" in tool_names
        assert "observe" in tool_names
        # MQTT tools
        assert "publish" in tool_names
        assert "get_recent_messages" in tool_names

    @pytest.mark.asyncio
    async def test_run_agent_loop_no_tools(self, test_config):
        """Test agent loop with simple response (no tool calls)."""
        agent = Agent(test_config)

        # Mock ollama client
        mock_response = ChatResponse(
            content="Hello, this is a test response",
            tool_calls=[],
            done=True,
        )
        agent._ollama.chat = AsyncMock(return_value=mock_response)

        result = await agent._run_agent_loop("Test message")

        assert result == "Hello, this is a test response"
        agent._ollama.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_agent_loop_with_tools(self, test_config):
        """Test agent loop with tool calls."""
        agent = Agent(test_config)

        # First response has tool calls
        tool_response = ChatResponse(
            content=None,
            tool_calls=[
                ToolCall(name="list_gpio", arguments={}),
            ],
            done=False,
        )

        # Second response is final
        final_response = ChatResponse(
            content="I checked the GPIO pins.",
            tool_calls=[],
            done=True,
        )

        agent._ollama.chat = AsyncMock(
            side_effect=[tool_response, final_response]
        )

        result = await agent._run_agent_loop("Check sensors")

        assert result == "I checked the GPIO pins."
        assert agent._ollama.chat.call_count == 2

    @pytest.mark.asyncio
    async def test_run_agent_loop_max_iterations(self, test_config):
        """Test agent loop respects max iterations."""
        agent = Agent(test_config)

        # Always return tool calls
        tool_response = ChatResponse(
            content=None,
            tool_calls=[ToolCall(name="list_gpio", arguments={})],
            done=False,
        )
        agent._ollama.chat = AsyncMock(return_value=tool_response)

        result = await agent._run_agent_loop("Test", max_iterations=3)

        # Should stop after max iterations
        assert result is None
        assert agent._ollama.chat.call_count == 3

    @pytest.mark.asyncio
    async def test_execute_tool_calls(self, test_config):
        """Test tool execution."""
        agent = Agent(test_config)

        response = ChatResponse(
            content=None,
            tool_calls=[ToolCall(name="list_sources", arguments={})],
            done=False,
        )

        results = await agent._execute_tool_calls(response)

        assert len(results) == 1
        assert results[0]["role"] == "tool"
        assert "sources_by_type" in results[0]["content"]

    @pytest.mark.asyncio
    async def test_query(self, test_config):
        """Test direct query method."""
        agent = Agent(test_config)

        mock_response = ChatResponse(
            content="Query response",
            tool_calls=[],
            done=True,
        )
        agent._ollama.chat = AsyncMock(return_value=mock_response)

        result = await agent.query("What is the status?")

        assert result == "Query response"


class TestConfig:
    """Tests for configuration loading."""

    def test_default_config(self):
        """Test default configuration values."""
        from smollama.config import load_config

        config = load_config()

        assert config.node.name == "smollama-node"
        assert config.ollama.host == "localhost"
        assert config.ollama.port == 11434
        assert config.mqtt.broker == "localhost"
        assert config.mqtt.port == 1883

    def test_env_override(self, monkeypatch):
        """Test environment variable overrides."""
        from smollama.config import load_config

        monkeypatch.setenv("SMOLLAMA_NODE_NAME", "env-node")
        monkeypatch.setenv("SMOLLAMA_OLLAMA_HOST", "ollama.local")
        monkeypatch.setenv("SMOLLAMA_MQTT_BROKER", "mqtt.local")

        config = load_config()

        assert config.node.name == "env-node"
        assert config.ollama.host == "ollama.local"
        assert config.mqtt.broker == "mqtt.local"
