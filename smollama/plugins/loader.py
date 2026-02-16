"""Plugin loader and discovery system."""

import importlib
import importlib.util
import inspect
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from smollama.plugins.base import PluginMetadata, SensorPlugin, ToolPlugin
from smollama.plugins.config import validate_plugin_config

logger = logging.getLogger(__name__)


@dataclass
class DiscoveredPlugin:
    """Information about a discovered plugin."""

    plugin_class: type[SensorPlugin] | type[ToolPlugin]
    """The plugin class itself"""

    metadata: PluginMetadata
    """Plugin metadata from the class"""

    source_path: Path
    """Path to the module file where plugin was found"""

    module_name: str
    """Fully qualified module name"""


@dataclass
class PluginLoadResult:
    """Result of attempting to load a plugin."""

    plugin: SensorPlugin | ToolPlugin | None
    """The loaded plugin instance, or None if loading failed"""

    success: bool
    """Whether the plugin loaded successfully"""

    error: str | None = None
    """Error message if loading failed"""

    skipped: bool = False
    """Whether plugin was skipped due to unmet dependencies"""


class PluginLoader:
    """Discovers and loads plugins from multiple sources."""

    def __init__(self, additional_paths: list[str] | None = None) -> None:
        """Initialize the plugin loader.

        Args:
            additional_paths: Additional directories to scan for plugins.
        """
        self._additional_paths = additional_paths or []
        self._discovered_plugins: list[DiscoveredPlugin] = []
        self._loaded_plugins: dict[str, SensorPlugin | ToolPlugin] = {}
        self._failed_plugins: dict[str, str] = {}
        self._skipped_plugins: dict[str, str] = {}

    def discover_plugins(self) -> list[DiscoveredPlugin]:
        """Discover all available plugins.

        Scans the builtin plugins directory and any additional paths
        for SensorPlugin and ToolPlugin subclasses.

        Returns:
            List of discovered plugins with their metadata.
        """
        self._discovered_plugins = []

        # Scan builtin plugins
        builtin_path = Path(__file__).parent / "builtin"
        if builtin_path.exists():
            self._scan_directory(builtin_path, "smollama.plugins.builtin")

        # Scan additional paths
        for path_str in self._additional_paths:
            path = Path(path_str).expanduser().resolve()
            if path.exists():
                self._scan_directory(path, None)
            else:
                logger.warning(f"Plugin path does not exist: {path}")

        logger.info(f"Discovered {len(self._discovered_plugins)} plugins")
        return self._discovered_plugins

    def _scan_directory(
        self, directory: Path, package_prefix: str | None
    ) -> None:
        """Scan a directory for plugin modules.

        Args:
            directory: Directory to scan.
            package_prefix: Python package prefix (e.g., 'smollama.plugins.builtin')
                           or None for standalone modules.
        """
        for file_path in directory.glob("*.py"):
            if file_path.name.startswith("_"):
                continue  # Skip __init__.py and private modules

            try:
                self._load_module_and_discover(file_path, package_prefix)
            except Exception as e:
                logger.error(
                    f"Error scanning {file_path}: {e}", exc_info=True
                )

    def _load_module_and_discover(
        self, file_path: Path, package_prefix: str | None
    ) -> None:
        """Load a Python module and discover plugins within it.

        Args:
            file_path: Path to the Python file.
            package_prefix: Package prefix for imports, or None for standalone.
        """
        module_name = file_path.stem

        # Build full module name
        if package_prefix:
            full_module_name = f"{package_prefix}.{module_name}"
        else:
            full_module_name = f"plugin_{file_path.parent.name}_{module_name}"

        # Load the module
        try:
            if package_prefix:
                # Import from package
                module = importlib.import_module(full_module_name)
            else:
                # Load standalone module
                spec = importlib.util.spec_from_file_location(
                    full_module_name, file_path
                )
                if spec is None or spec.loader is None:
                    logger.warning(f"Cannot load module spec from {file_path}")
                    return

                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

        except Exception as e:
            logger.error(
                f"Failed to load module {full_module_name} from {file_path}: {e}",
                exc_info=True,
            )
            return

        # Discover plugin classes in the module
        self._discover_in_module(module, file_path, full_module_name)

    def _discover_in_module(
        self, module: Any, file_path: Path, module_name: str
    ) -> None:
        """Discover plugin classes within a loaded module.

        Args:
            module: The loaded Python module.
            file_path: Path to the module file.
            module_name: Fully qualified module name.
        """
        for name, obj in inspect.getmembers(module, inspect.isclass):
            # Check if it's a SensorPlugin or ToolPlugin subclass
            # (but not the base classes themselves)
            is_sensor = (
                issubclass(obj, SensorPlugin)
                and obj is not SensorPlugin
                and not inspect.isabstract(obj)
            )
            is_tool = (
                issubclass(obj, ToolPlugin)
                and obj is not ToolPlugin
                and not inspect.isabstract(obj)
            )

            if not (is_sensor or is_tool):
                continue

            # Validate plugin has required methods/properties
            try:
                # Instantiate temporarily to get metadata
                temp_instance = obj()
                metadata = temp_instance.metadata

                # Validate metadata
                if not metadata.name:
                    logger.warning(
                        f"Plugin {name} in {file_path} missing name in metadata"
                    )
                    continue
                if not metadata.version:
                    logger.warning(
                        f"Plugin {name} in {file_path} missing version in metadata"
                    )
                    continue

                # Add to discovered plugins
                discovered = DiscoveredPlugin(
                    plugin_class=obj,
                    metadata=metadata,
                    source_path=file_path,
                    module_name=module_name,
                )
                self._discovered_plugins.append(discovered)

                logger.debug(
                    f"Discovered plugin: {metadata.name} v{metadata.version} "
                    f"({metadata.plugin_type}) from {file_path}"
                )

            except Exception as e:
                logger.error(
                    f"Failed to validate plugin {name} in {file_path}: {e}",
                    exc_info=True,
                )

    def load_plugin(
        self, discovered: DiscoveredPlugin, config: dict[str, Any] | None = None
    ) -> PluginLoadResult:
        """Load and initialize a single plugin.

        Args:
            discovered: The discovered plugin to load.
            config: Plugin-specific configuration.

        Returns:
            PluginLoadResult with load status and plugin instance.
        """
        plugin_name = discovered.metadata.name

        try:
            # Instantiate the plugin
            plugin = discovered.plugin_class()

            # Check dependencies
            deps_ok, deps_error = plugin.check_dependencies()
            if not deps_ok:
                logger.debug(
                    f"Skipping plugin {plugin_name}: {deps_error or 'dependencies not met'}"
                )
                self._skipped_plugins[plugin_name] = (
                    deps_error or "dependencies not met"
                )
                return PluginLoadResult(
                    plugin=None,
                    success=False,
                    error=deps_error,
                    skipped=True,
                )

            # Validate configuration if provided
            if config is not None:
                config_valid, config_error = validate_plugin_config(
                    plugin_name, config, plugin.config_schema
                )
                if not config_valid:
                    error_msg = f"Invalid config for {plugin_name}: {config_error}"
                    logger.error(error_msg)
                    self._failed_plugins[plugin_name] = config_error or "Invalid config"
                    return PluginLoadResult(
                        plugin=None, success=False, error=config_error
                    )

            # Initialize the plugin
            plugin.setup()

            # Track loaded plugin
            self._loaded_plugins[plugin_name] = plugin
            logger.info(
                f"Loaded plugin: {plugin_name} v{discovered.metadata.version}"
            )

            return PluginLoadResult(plugin=plugin, success=True)

        except Exception as e:
            error_msg = f"Failed to load plugin {plugin_name}: {e}"
            logger.error(error_msg, exc_info=True)
            self._failed_plugins[plugin_name] = str(e)
            return PluginLoadResult(plugin=None, success=False, error=str(e))

    def load_all_plugins(
        self, plugin_configs: dict[str, dict[str, Any]] | None = None
    ) -> list[PluginLoadResult]:
        """Load all discovered plugins.

        Args:
            plugin_configs: Dict mapping plugin names to their configs.

        Returns:
            List of load results for each plugin.
        """
        plugin_configs = plugin_configs or {}
        results = []

        for discovered in self._discovered_plugins:
            plugin_name = discovered.metadata.name
            config = plugin_configs.get(plugin_name)
            result = self.load_plugin(discovered, config)
            results.append(result)

        return results

    def get_loaded_plugins(self) -> list[SensorPlugin | ToolPlugin]:
        """Get all successfully loaded plugins.

        Returns:
            List of loaded plugin instances.
        """
        return list(self._loaded_plugins.values())

    def get_sensor_plugins(self) -> list[SensorPlugin]:
        """Get all loaded sensor plugins.

        Returns:
            List of loaded SensorPlugin instances.
        """
        return [
            p for p in self._loaded_plugins.values() if isinstance(p, SensorPlugin)
        ]

    def get_tool_plugins(self) -> list[ToolPlugin]:
        """Get all loaded tool plugins.

        Returns:
            List of loaded ToolPlugin instances.
        """
        return [
            p for p in self._loaded_plugins.values() if isinstance(p, ToolPlugin)
        ]

    def shutdown_plugins(self) -> None:
        """Shutdown all loaded plugins.

        Calls teardown() on each loaded plugin. Exceptions are caught
        and logged but don't prevent other plugins from being cleaned up.
        """
        for plugin_name, plugin in self._loaded_plugins.items():
            try:
                plugin.teardown()
                logger.debug(f"Shutdown plugin: {plugin_name}")
            except Exception as e:
                logger.error(
                    f"Error during teardown of {plugin_name}: {e}",
                    exc_info=True,
                )

        self._loaded_plugins.clear()

    @property
    def discovered_count(self) -> int:
        """Number of discovered plugins."""
        return len(self._discovered_plugins)

    @property
    def loaded_count(self) -> int:
        """Number of successfully loaded plugins."""
        return len(self._loaded_plugins)

    @property
    def failed_count(self) -> int:
        """Number of failed plugins."""
        return len(self._failed_plugins)

    @property
    def skipped_count(self) -> int:
        """Number of skipped plugins (missing dependencies)."""
        return len(self._skipped_plugins)

    def get_status(self) -> dict[str, Any]:
        """Get current loader status.

        Returns:
            Dict with discovery and load statistics.
        """
        return {
            "discovered": self.discovered_count,
            "loaded": self.loaded_count,
            "failed": self.failed_count,
            "skipped": self.skipped_count,
            "loaded_plugins": list(self._loaded_plugins.keys()),
            "failed_plugins": self._failed_plugins,
            "skipped_plugins": self._skipped_plugins,
        }
