"""Smollama tools for agent interactions."""

from .base import Tool, ToolParameter, ToolRegistry
from .gpio_tools import ListGPIOTool, ReadGPIOTool
from .memory_tools import ObserveTool, RecallTool, RememberTool
from .mqtt_tools import GetRecentMessagesTool, PublishTool
from .reading_tools import GetReadingHistoryTool, ListSourcesTool, ReadSourceTool

__all__ = [
    # Base
    "Tool",
    "ToolParameter",
    "ToolRegistry",
    # GPIO (legacy, prefer reading tools)
    "ReadGPIOTool",
    "ListGPIOTool",
    # MQTT
    "PublishTool",
    "GetRecentMessagesTool",
    # Reading (unified interface)
    "ReadSourceTool",
    "ListSourcesTool",
    "GetReadingHistoryTool",
    # Memory
    "RecallTool",
    "RememberTool",
    "ObserveTool",
]
