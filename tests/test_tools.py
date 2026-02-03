"""Tests for tool system."""

import pytest

from smollama.config import GPIOConfig, GPIOPinConfig, MQTTConfig
from smollama.gpio_reader import GPIOReader
from smollama.mqtt_client import MQTTClient
from smollama.tools.base import Tool, ToolParameter, ToolRegistry
from smollama.tools.gpio_tools import ReadGPIOTool, ListGPIOTool
from smollama.tools.mqtt_tools import PublishTool, GetRecentMessagesTool


class MockTool(Tool):
    """Simple mock tool for testing."""

    @property
    def name(self) -> str:
        return "mock_tool"

    @property
    def description(self) -> str:
        return "A mock tool for testing"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="input",
                type="string",
                description="Test input",
                required=True,
            ),
            ToolParameter(
                name="optional",
                type="integer",
                description="Optional param",
                required=False,
            ),
        ]

    async def execute(self, input: str, optional: int = 10, **kwargs):
        return {"echo": input, "optional": optional}


class TestToolRegistry:
    """Tests for ToolRegistry."""

    def test_register_and_get(self):
        registry = ToolRegistry()
        tool = MockTool()
        registry.register(tool)

        assert registry.get("mock_tool") is tool
        assert registry.get("nonexistent") is None

    def test_list_tools(self):
        registry = ToolRegistry()
        tool = MockTool()
        registry.register(tool)

        tools = registry.list_tools()
        assert len(tools) == 1
        assert tools[0] is tool

    def test_to_ollama_format(self):
        registry = ToolRegistry()
        tool = MockTool()
        registry.register(tool)

        formats = registry.to_ollama_format()
        assert len(formats) == 1

        fmt = formats[0]
        assert fmt["type"] == "function"
        assert fmt["function"]["name"] == "mock_tool"
        assert "parameters" in fmt["function"]

    @pytest.mark.asyncio
    async def test_execute(self):
        registry = ToolRegistry()
        registry.register(MockTool())

        result = await registry.execute("mock_tool", {"input": "test"})
        assert result["echo"] == "test"

    @pytest.mark.asyncio
    async def test_execute_unknown_tool(self):
        registry = ToolRegistry()

        with pytest.raises(ValueError, match="not found"):
            await registry.execute("unknown", {})


class TestGPIOTools:
    """Tests for GPIO tools."""

    @pytest.fixture
    def gpio_reader(self):
        config = GPIOConfig(
            pins=[
                GPIOPinConfig(pin=17, name="motion", mode="input"),
                GPIOPinConfig(pin=27, name="door", mode="input"),
            ],
            mock=True,
        )
        return GPIOReader(config)

    @pytest.mark.asyncio
    async def test_list_gpio(self, gpio_reader):
        tool = ListGPIOTool(gpio_reader)
        result = await tool.execute()

        assert result["mock_mode"] is True
        assert len(result["pins"]) == 2
        assert result["pins"][0]["name"] == "motion"
        assert result["pins"][1]["name"] == "door"

    @pytest.mark.asyncio
    async def test_read_gpio_by_number(self, gpio_reader):
        tool = ReadGPIOTool(gpio_reader)
        result = await tool.execute(pin="17")

        assert result["pin"] == 17
        assert result["name"] == "motion"
        assert result["value"] in (0, 1)

    @pytest.mark.asyncio
    async def test_read_gpio_by_name(self, gpio_reader):
        tool = ReadGPIOTool(gpio_reader)
        result = await tool.execute(pin="door")

        assert result["pin"] == 27
        assert result["name"] == "door"

    @pytest.mark.asyncio
    async def test_read_gpio_unknown(self, gpio_reader):
        tool = ReadGPIOTool(gpio_reader)
        result = await tool.execute(pin="unknown")

        assert "error" in result

    def test_ollama_format(self, gpio_reader):
        tool = ReadGPIOTool(gpio_reader)
        fmt = tool.to_ollama_format()

        assert fmt["type"] == "function"
        assert fmt["function"]["name"] == "read_gpio"
        assert "pin" in fmt["function"]["parameters"]["properties"]


class TestMQTTTools:
    """Tests for MQTT tools."""

    @pytest.fixture
    def mqtt_client(self):
        config = MQTTConfig(broker="localhost", port=1883)
        return MQTTClient(config)

    def test_publish_ollama_format(self, mqtt_client):
        tool = PublishTool(mqtt_client)
        fmt = tool.to_ollama_format()

        assert fmt["function"]["name"] == "publish"
        params = fmt["function"]["parameters"]["properties"]
        assert "topic" in params
        assert "message" in params

    def test_get_recent_messages_format(self, mqtt_client):
        tool = GetRecentMessagesTool(mqtt_client)
        fmt = tool.to_ollama_format()

        assert fmt["function"]["name"] == "get_recent_messages"
        params = fmt["function"]["parameters"]["properties"]
        assert "topic" in params
        assert "count" in params

    @pytest.mark.asyncio
    async def test_get_recent_messages_empty(self, mqtt_client):
        tool = GetRecentMessagesTool(mqtt_client)
        result = await tool.execute()

        assert result["count"] == 0
        assert result["messages"] == []
