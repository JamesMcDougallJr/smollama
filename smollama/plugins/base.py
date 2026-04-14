"""Base classes for the plugin system."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from smollama.readings.base import ReadingProvider
from smollama.tools.base import Tool


@dataclass
class PluginMetadata:
    """Metadata describing a plugin."""

    name: str
    """Unique plugin identifier (e.g., 'gpio', 'bme280')"""

    version: str
    """Semantic version (e.g., '1.0.0')"""

    author: str
    """Plugin author or maintainer"""

    description: str
    """Human-readable description of what the plugin does"""

    dependencies: list[str] = field(default_factory=list)
    """List of Python package dependencies (e.g., ['gpiozero>=2.0', 'RPi.GPIO'])"""

    plugin_type: str = "read"
    """Type of plugin: 'read', 'write', or 'readwrite'. Legacy values 'sensor'/'tool' still accepted."""


class PluginLifecycleMixin(ABC):
    """Shared lifecycle hooks for all plugin types.

    Provides the common interface that all plugins (read, write, readwrite)
    must implement: metadata, dependency checking, configuration validation,
    and setup/teardown lifecycle.
    """

    @property
    @abstractmethod
    def metadata(self) -> PluginMetadata:
        """Plugin metadata including name, version, and dependencies."""
        pass

    @abstractmethod
    def setup(self) -> None:
        """Called once when the plugin is loaded.

        Use this to initialize hardware connections, allocate resources,
        or perform one-time setup tasks.

        Raises:
            Exception: If setup fails, plugin will be marked as failed.
        """
        pass

    @abstractmethod
    def teardown(self) -> None:
        """Called once when the plugin is unloaded or app shuts down.

        Use this to cleanup resources, close connections, or perform
        shutdown tasks. This is always called for successfully initialized
        plugins, even if other plugins fail.

        Note:
            Exceptions in teardown are logged but don't prevent other
            plugins from being cleaned up.
        """
        pass

    @property
    @abstractmethod
    def config_schema(self) -> dict[str, Any]:
        """JSON Schema for plugin-specific configuration validation.

        Returns:
            JSON Schema dict describing expected config structure.
        """
        pass

    @abstractmethod
    def check_dependencies(self) -> tuple[bool, str | None]:
        """Check if plugin dependencies are available.

        Called before setup() to verify runtime requirements.
        Plugins with unmet dependencies are skipped gracefully.

        Returns:
            Tuple of (success, error_message).
            - (True, None) if all dependencies met
            - (False, "reason") if dependencies missing
        """
        pass


class ObservationHook:
    """Optional mixin for plugins that participate in the observation cycle.

    Inherit this alongside ReadPlugin, WritePlugin, or ReadWritePlugin to
    receive callbacks at the start and end of each observation cycle.
    Both methods have default no-op implementations — only override what you need.
    """

    async def on_observation_begin(self) -> None:
        """Called at the start of each observation cycle, before readings are taken."""

    async def on_observation_end(self, success: bool) -> None:
        """Called at the end of each observation cycle.

        Args:
            success: True if the cycle completed normally; False if an exception occurred.
        """


class ReadPlugin(PluginLifecycleMixin, ReadingProvider):
    """Plugin that ingests data into smollama.

    Read plugins extend ReadingProvider with lifecycle hooks,
    dependency checking, and config validation. They provide
    sensor data, API responses, or any other input to the system.

    Example:
        class MyTemperatureSensor(ReadPlugin):
            @property
            def source_type(self) -> str:
                return "i2c_temp"

            @property
            def metadata(self) -> PluginMetadata:
                return PluginMetadata(
                    name="i2c_temp",
                    version="1.0.0",
                    author="Your Name",
                    description="I2C temperature sensor",
                    dependencies=["smbus2>=0.4.0"],
                    plugin_type="read"
                )

            def check_dependencies(self) -> tuple[bool, str | None]:
                try:
                    import smbus2
                    return (True, None)
                except ImportError:
                    return (False, "smbus2 package not installed")

            def setup(self) -> None:
                pass

            def teardown(self) -> None:
                pass

            # ... implement ReadingProvider methods
    """
    pass


class WritePlugin(PluginLifecycleMixin, Tool):
    """Plugin that takes actions on the world.

    Write plugins extend Tool with lifecycle hooks, dependency checking,
    and config validation. They control hardware (displays, actuators),
    send API requests, or perform any output action.

    A single WritePlugin can provide multiple tools.

    Example:
        class MyDisplayPlugin(WritePlugin):
            @property
            def name(self) -> str:
                return "display_value"

            @property
            def metadata(self) -> PluginMetadata:
                return PluginMetadata(
                    name="led_display",
                    version="1.0.0",
                    author="Your Name",
                    description="LED display controller",
                    dependencies=["RPi.GPIO>=0.7"],
                    plugin_type="write"
                )

            def check_dependencies(self) -> tuple[bool, str | None]:
                try:
                    import RPi.GPIO
                    return (True, None)
                except ImportError:
                    return (False, "RPi.GPIO not installed")

            def setup(self) -> None:
                pass

            def teardown(self) -> None:
                pass

            def get_tools(self) -> list[Tool]:
                return [self]

            # ... implement Tool methods
    """

    def get_tools(self) -> list[Tool]:
        """Get list of tools provided by this plugin.

        Override this if your plugin provides multiple tools.
        Default implementation returns [self].
        """
        return [self]


class ReadWritePlugin(PluginLifecycleMixin, ReadingProvider, Tool):
    """Hybrid plugin that both reads data and performs actions.

    ReadWrite plugins combine the ReadingProvider and Tool interfaces,
    allowing a single plugin to both ingest data and take actions.
    Useful for components like relays (toggle on/off AND read current state).

    Example:
        class RelayPlugin(ReadWritePlugin):
            @property
            def source_type(self) -> str:
                return "relay"

            @property
            def name(self) -> str:
                return "toggle_relay"

            # ... implement both ReadingProvider and Tool methods
    """

    def get_tools(self) -> list[Tool]:
        """Get list of tools provided by this plugin.

        Override this if your plugin provides multiple tools.
        Default implementation returns [self].
        """
        return [self]


# Backwards compatibility aliases
SensorPlugin = ReadPlugin
ToolPlugin = WritePlugin
