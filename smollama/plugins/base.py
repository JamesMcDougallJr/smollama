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


class ObservationDomain:
    """Mixin for plugins that provide domain-specific observation generation.

    A domain claims readings by full_id pattern (e.g. all jetson_inference
    sources regardless of which node relayed them), tracks deterministic state
    from reading history, and builds a focused LLM prompt for the observation
    loop. Claimed sources are excluded from the generic observation pass.

    All state derivation must be pure (history in, state out) so that both the
    agent process and the separate dashboard process can compute status from
    stored readings alone.
    """

    @property
    def domain_name(self) -> str:
        """Short domain identifier, e.g. 'vision' or 'audio'."""
        raise NotImplementedError

    def matches(self, full_id: str) -> bool:
        """Return True if this domain claims the given reading full_id.

        On a master node, relayed edge readings look like
        'pipi:jetson_inference:person_count', so matching must be
        pattern-based on the full_id, not on source_type.
        """
        raise NotImplementedError

    def describe_sources(self) -> dict[str, str]:
        """Semantic descriptions of known source names, for prompt context.

        Keys are bare source names (e.g. 'person_count'), matched against the
        tail of a full_id.
        """
        return {}

    def derive_state(self, history: list[dict], now: Any = None) -> dict[str, Any]:
        """Derive deterministic domain state from reading history.

        Args:
            history: Reading dicts (full_id, timestamp, value, unit) for
                     claimed sources, as returned by the store.
            now: Reference datetime for age computations (default: now).

        Returns:
            Domain-specific state dict fed into build_prompt/derive_status.
        """
        raise NotImplementedError

    def build_prompt(
        self,
        state: dict[str, Any],
        current_readings: list,
        history: list[dict],
        past_observations: list[dict],
        lookback_minutes: int,
    ) -> str:
        """Build the domain-focused LLM prompt for one observation pass."""
        raise NotImplementedError

    def derive_status(self, state: dict[str, Any]) -> str:
        """One-line human-readable status from derived state.

        E.g. 'No humans detected in the past 3h (last seen 14:32)'.
        """
        raise NotImplementedError

    @property
    def status_lookback_minutes(self) -> int:
        """How much reading history derive_state needs for a meaningful status."""
        return 180


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


class ObserverPlugin(PluginLifecycleMixin, ObservationDomain):
    """Plugin that specializes observation generation for a domain.

    Observer plugins neither read sensors nor expose tools — they teach the
    master's observation loop how to interpret a family of sources (claimed by
    full_id pattern) and how to prompt the LLM about them. They run on the
    master node, have no hardware dependencies, and are disabled by default.

    Example: a vision observer that claims '*jetson_inference*' sources and
    prompts for camera-centric summaries ('No humans detected in the past 3h')
    instead of generic numeric-trend analysis.
    """
    pass


# Backwards compatibility aliases
SensorPlugin = ReadPlugin
ToolPlugin = WritePlugin
