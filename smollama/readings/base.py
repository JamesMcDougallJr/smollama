"""Base classes for the unified reading abstraction."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from smollama.plugins.loader import PluginLoader


@dataclass
class Reading:
    """A single reading from any input source."""

    source_type: str  # "gpio", "system", "mqtt", "i2c"
    source_id: str  # Identifier within type: "17", "cpu_temp", "kitchen/temp"
    value: Any  # The reading value (int, float, str, dict)
    timestamp: datetime  # When the reading was taken
    unit: str | None = None  # Optional unit: "celsius", "percent", "boolean"
    metadata: dict | None = None  # Additional context

    @property
    def full_id(self) -> str:
        """Full identifier: 'gpio:17' or 'system:cpu_temp'."""
        return f"{self.source_type}:{self.source_id}"

    def to_log_dict(self) -> dict:
        """Convert to dict for storage."""
        return {
            "source_type": self.source_type,
            "source_id": self.source_id,
            "full_id": self.full_id,
            "value": self.value,
            "timestamp": self.timestamp.isoformat(),
            "unit": self.unit,
            "metadata": self.metadata,
        }


class ReadingProvider(ABC):
    """Abstract base for all reading sources."""

    @property
    @abstractmethod
    def source_type(self) -> str:
        """The type of readings this provider generates."""
        pass

    @property
    @abstractmethod
    def available_sources(self) -> list[str]:
        """List available source IDs."""
        pass

    @abstractmethod
    async def read(self, source_id: str) -> Reading | None:
        """Get a single reading.

        Args:
            source_id: The identifier within this source type.

        Returns:
            A Reading object or None if the source doesn't exist.
        """
        pass

    @abstractmethod
    async def read_all(self) -> list[Reading]:
        """Get readings from all available sources.

        Returns:
            List of Reading objects from all sources.
        """
        pass


class ReadingManager:
    """Manages multiple ReadingProviders for unified access."""

    def __init__(self, plugin_loader: "PluginLoader | None" = None):
        """Initialize the reading manager.

        Args:
            plugin_loader: Optional PluginLoader to automatically register
                         sensor plugins. If provided, sensor plugins will be
                         loaded and registered automatically.
        """
        self._providers: dict[str, ReadingProvider] = {}
        self._plugin_loader = plugin_loader

        if plugin_loader is not None:
            self._register_plugins_from_loader()

    def register(self, provider: ReadingProvider) -> None:
        """Register a reading provider.

        Args:
            provider: The ReadingProvider to register.
        """
        self._providers[provider.source_type] = provider

    def unregister(self, source_type: str) -> None:
        """Unregister a provider by source type.

        Args:
            source_type: The source type to unregister.
        """
        self._providers.pop(source_type, None)

    async def read(self, full_id: str) -> Reading | None:
        """Read by full ID like 'gpio:17'.

        Args:
            full_id: Full identifier in format "source_type:source_id".

        Returns:
            A Reading object or None if not found.
        """
        if ":" not in full_id:
            return None

        source_type, source_id = full_id.split(":", 1)
        if provider := self._providers.get(source_type):
            return await provider.read(source_id)
        return None

    async def read_all(self) -> list[Reading]:
        """Get readings from all registered providers.

        Returns:
            List of Reading objects from all providers.
        """
        readings = []
        for provider in self._providers.values():
            readings.extend(await provider.read_all())
        return readings

    def list_sources(self, source_type: str | None = None) -> list[str]:
        """List all available full IDs.

        Args:
            source_type: Optional filter by source type.

        Returns:
            List of full IDs (e.g., ["gpio:17", "system:cpu_temp"]).
        """
        sources = []
        providers = self._providers.values()

        if source_type is not None:
            providers = [p for p in providers if p.source_type == source_type]

        for provider in providers:
            for sid in provider.available_sources:
                sources.append(f"{provider.source_type}:{sid}")
        return sources

    @property
    def source_types(self) -> list[str]:
        """Get list of registered source types."""
        return list(self._providers.keys())

    def _register_plugins_from_loader(self) -> None:
        """Register all loaded sensor plugins as reading providers.

        Called automatically during initialization if a plugin_loader is provided.
        """
        if self._plugin_loader is None:
            return

        # Get all loaded sensor plugins
        sensor_plugins = self._plugin_loader.get_sensor_plugins()

        for plugin in sensor_plugins:
            self.register(plugin)

    def reload_plugins(self) -> None:
        """Reload plugins from the plugin loader.

        Useful if plugins were loaded/unloaded after ReadingManager was created.
        Only works if a plugin_loader was provided during initialization.
        """
        if self._plugin_loader is None:
            raise RuntimeError(
                "Cannot reload plugins: ReadingManager was not initialized with a plugin_loader"
            )

        # Clear existing providers loaded from plugins
        # (keep manually registered ones? For now, clear all)
        self._providers.clear()

        # Re-register plugins
        self._register_plugins_from_loader()
