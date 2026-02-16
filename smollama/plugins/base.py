"""Base classes for the plugin system."""

from abc import abstractmethod
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

    plugin_type: str = "sensor"
    """Type of plugin: 'sensor' or 'tool'"""


class SensorPlugin(ReadingProvider):
    """Extended interface for pluggable sensors.

    Sensor plugins extend ReadingProvider with lifecycle hooks,
    dependency checking, and config validation.

    Example:
        class MyTemperatureSensor(SensorPlugin):
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
                    plugin_type="sensor"
                )

            def check_dependencies(self) -> tuple[bool, str | None]:
                try:
                    import smbus2
                    return (True, None)
                except ImportError:
                    return (False, "smbus2 package not installed")

            def setup(self) -> None:
                # Initialize hardware connection
                pass

            def teardown(self) -> None:
                # Cleanup resources
                pass

            # ... implement ReadingProvider methods
    """

    @property
    @abstractmethod
    def metadata(self) -> PluginMetadata:
        """Plugin metadata including name, version, and dependencies.

        Returns:
            PluginMetadata instance with plugin information.
        """
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

        Example:
            {
                "type": "object",
                "properties": {
                    "bus": {"type": "integer", "minimum": 0},
                    "address": {"type": "integer"}
                },
                "required": ["bus", "address"]
            }
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

        Example:
            def check_dependencies(self) -> tuple[bool, str | None]:
                try:
                    import gpiozero
                    import RPi.GPIO
                    return (True, None)
                except ImportError as e:
                    return (False, f"Missing dependency: {e.name}")
        """
        pass


class ToolPlugin(Tool):
    """Extended interface for pluggable tools.

    Tool plugins extend Tool with lifecycle hooks, dependency checking,
    and config validation. A single ToolPlugin can provide multiple tools.

    Example:
        class MyAPIPlugin(ToolPlugin):
            @property
            def name(self) -> str:
                return "call_api"

            @property
            def metadata(self) -> PluginMetadata:
                return PluginMetadata(
                    name="api_tools",
                    version="1.0.0",
                    author="Your Name",
                    description="API integration tools",
                    dependencies=["httpx>=0.25.0"],
                    plugin_type="tool"
                )

            def check_dependencies(self) -> tuple[bool, str | None]:
                try:
                    import httpx
                    return (True, None)
                except ImportError:
                    return (False, "httpx package not installed")

            def setup(self) -> None:
                # Initialize API client
                pass

            def teardown(self) -> None:
                # Cleanup connections
                pass

            def get_tools(self) -> list[Tool]:
                # Return list of Tool instances this plugin provides
                return [self]

            # ... implement Tool methods
    """

    @property
    @abstractmethod
    def metadata(self) -> PluginMetadata:
        """Plugin metadata including name, version, and dependencies.

        Returns:
            PluginMetadata instance with plugin information.
        """
        pass

    @abstractmethod
    def setup(self) -> None:
        """Called once when the plugin is loaded.

        Use this to initialize connections, allocate resources,
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

        Example:
            {
                "type": "object",
                "properties": {
                    "api_key": {"type": "string"},
                    "base_url": {"type": "string", "format": "uri"}
                },
                "required": ["api_key"]
            }
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

        Example:
            def check_dependencies(self) -> tuple[bool, str | None]:
                try:
                    import httpx
                    return (True, None)
                except ImportError as e:
                    return (False, f"Missing dependency: {e.name}")
        """
        pass

    def get_tools(self) -> list[Tool]:
        """Get list of tools provided by this plugin.

        Override this if your plugin provides multiple tools.
        Default implementation returns [self].

        Returns:
            List of Tool instances this plugin provides.

        Example:
            def get_tools(self) -> list[Tool]:
                return [
                    GetTool(self.config),
                    PostTool(self.config),
                    DeleteTool(self.config)
                ]
        """
        return [self]
