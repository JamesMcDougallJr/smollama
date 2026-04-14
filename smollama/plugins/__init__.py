"""Plugin system for extensible sensors and tools."""

from smollama.plugins.base import (
    ObservationHook,
    PluginMetadata,
    SensorPlugin,
    ToolPlugin,
)

__all__ = [
    "ObservationHook",
    "PluginMetadata",
    "SensorPlugin",
    "ToolPlugin",
]
