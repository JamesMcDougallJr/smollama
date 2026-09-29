# Custom Sensor Plugin System (Refactor)

A structured plugin system to make adding new sensor types straightforward.

- **Status**: ✅ Complete
- **Effort**: Large (multi-session)
- **Completed**: 2026-02-16
- **Implementation**: See `smollama/plugins/` and `docs/plugin-development.md`

## Plugin Directory
Create `smollama/plugins/` with a plugin loader that scans for `SensorPlugin` subclasses.

## `SensorPlugin` Interface
Extends `ReadingProvider` (`smollama/readings/base.py`) with lifecycle hooks:

```python
class SensorPlugin(ReadingProvider):
    """Extended interface for pluggable sensors."""

    @abstractmethod
    def setup(self) -> None:
        """Called once when the plugin is loaded."""

    @abstractmethod
    def teardown(self) -> None:
        """Called once when the plugin is unloaded."""

    @property
    @abstractmethod
    def config_schema(self) -> dict:
        """JSON Schema for plugin-specific config validation."""

    @property
    @abstractmethod
    def metadata(self) -> PluginMetadata:
        """Plugin name, version, author, description."""
```

## Plugin Discovery
- Scan `smollama/plugins/` directory for Python modules with `SensorPlugin` subclasses
- Support additional paths via config: `plugins.paths: ["./my_plugins"]`
- Auto-register discovered plugins with `ReadingManager`

## Built-in Plugins
Move existing providers into `plugins/builtin/`:
- `gpio_plugin.py` - Wraps current `GPIOReadingProvider`
- `system_plugin.py` - Wraps current `SystemReadingProvider`

## Example Plugins
- I2C temperature sensor (BME280, DHT22)
- HTTP/webhook sensor (poll a URL, parse JSON response)
- File-watch sensor (monitor a file for changes, emit readings)

## Plugin Config
```yaml
plugins:
  paths: ["./my_plugins"]
  builtin:
    gpio: { enabled: true }
    system: { enabled: true }
  custom:
    - name: bme280
      module: i2c_temp_plugin
      config:
        bus: 1
        address: 0x76
```

## `ToolPlugin` Interface
Corresponding plugin system for custom agent tools, extending `Tool` (`smollama/tools/base.py`) with the same lifecycle hooks and metadata.
