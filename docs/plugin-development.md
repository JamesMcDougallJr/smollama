# Plugin Development Guide

This guide explains how to create custom plugins for Smollama, enabling you to add new sensors and tools without modifying the core application.

## Table of Contents

- [Overview](#overview)
- [Plugin Types](#plugin-types)
- [Creating a Sensor Plugin](#creating-a-sensor-plugin)
- [Creating a Tool Plugin](#creating-a-tool-plugin)
- [Plugin Metadata](#plugin-metadata)
- [Configuration Schema](#configuration-schema)
- [Dependency Management](#dependency-management)
- [Installing Plugins](#installing-plugins)
- [Testing Your Plugin](#testing-your-plugin)
- [Examples](#examples)

## Overview

Smollama's plugin system allows you to:

- **Add custom sensors** - Read from I2C devices, SPI sensors, HTTP APIs, file watchers, etc.
- **Add custom tools** - Extend the agent with new capabilities
- **Isolate dependencies** - Plugin-specific libraries don't bloat the core application
- **Support diverse hardware** - Different Raspberry Pi models can load different plugins

### Key Features

- **Automatic discovery** - Plugins are discovered automatically from configured directories
- **Graceful degradation** - Plugins with missing dependencies are skipped silently
- **Configuration validation** - Use JSON Schema to validate plugin configuration
- **Lifecycle hooks** - `setup()` and `teardown()` for resource management

## Plugin Types

### SensorPlugin

Extends `ReadingProvider` to provide sensor readings through the unified reading system.

**Use cases:**
- I2C temperature sensors (BME280, DHT22)
- HTTP/REST API polling
- File monitoring
- Serial port communication
- Custom GPIO logic

### ToolPlugin

Extends `Tool` to add new capabilities to the Smollama agent.

**Use cases:**
- API integrations (weather, notifications, databases)
- System operations (file management, process control)
- External service control (home automation, IoT devices)
- Data processing tools

## Creating a Sensor Plugin

### Minimal Example

```python
from datetime import datetime
from typing import Any

from smollama.plugins.base import PluginMetadata, SensorPlugin
from smollama.readings.base import Reading


class MyTemperatureSensor(SensorPlugin):
    """Example temperature sensor plugin."""

    def __init__(self) -> None:
        self._sensor = None

    @property
    def metadata(self) -> PluginMetadata:
        """Plugin metadata."""
        return PluginMetadata(
            name="my_temp_sensor",
            version="1.0.0",
            author="Your Name",
            description="My custom temperature sensor",
            dependencies=["smbus2>=0.4.0"],  # Required packages
            plugin_type="sensor",
        )

    @property
    def source_type(self) -> str:
        """Source type for readings (e.g., 'i2c_temp')."""
        return "my_temp"

    @property
    def config_schema(self) -> dict[str, Any]:
        """JSON Schema for configuration validation."""
        return {
            "type": "object",
            "properties": {
                "bus": {"type": "integer", "minimum": 0, "default": 1},
                "address": {"type": "integer"},
                "poll_interval": {"type": "integer", "default": 60},
            },
            "required": ["address"],
        }

    def check_dependencies(self) -> tuple[bool, str | None]:
        """Check if required packages are available."""
        try:
            import smbus2  # noqa: F401
            return (True, None)
        except ImportError:
            return (False, "smbus2 package not installed")

    def setup(self) -> None:
        """Initialize the sensor (called once on load)."""
        import smbus2

        # Get config from somewhere (injected during initialization)
        # For now, use defaults
        bus = 1
        address = 0x76

        self._sensor = smbus2.SMBus(bus)
        # Additional sensor initialization here

    def teardown(self) -> None:
        """Cleanup resources (called on shutdown)."""
        if self._sensor is not None:
            self._sensor.close()
            self._sensor = None

    @property
    def available_sources(self) -> list[str]:
        """List of source IDs this plugin provides."""
        return ["temperature", "humidity"]

    async def read(self, source_id: str) -> Reading | None:
        """Read a single source."""
        if source_id == "temperature":
            # Read temperature from sensor
            temp = self._read_temperature()
            return Reading(
                source_type=self.source_type,
                source_id=source_id,
                value=temp,
                timestamp=datetime.now(),
                unit="celsius",
                metadata=None,
            )
        elif source_id == "humidity":
            # Read humidity from sensor
            humidity = self._read_humidity()
            return Reading(
                source_type=self.source_type,
                source_id=source_id,
                value=humidity,
                timestamp=datetime.now(),
                unit="percent",
                metadata=None,
            )
        return None

    async def read_all(self) -> list[Reading]:
        """Read all sources."""
        readings = []
        for source_id in self.available_sources:
            reading = await self.read(source_id)
            if reading:
                readings.append(reading)
        return readings

    def _read_temperature(self) -> float:
        """Internal method to read temperature."""
        # Your sensor-specific code here
        return 25.0

    def _read_humidity(self) -> float:
        """Internal method to read humidity."""
        # Your sensor-specific code here
        return 50.0
```

### Key Points

1. **Implement all abstract methods** - `metadata`, `source_type`, `config_schema`, `check_dependencies`, `setup`, `teardown`, `available_sources`, `read`, `read_all`

2. **Return proper metadata** - Name, version, dependencies are required

3. **Validate dependencies** - `check_dependencies()` should return `(True, None)` if deps are met, or `(False, "error message")` if not

4. **Initialize in setup()** - Don't initialize hardware in `__init__`, use `setup()` so dependency checking happens first

5. **Clean up in teardown()** - Always cleanup resources (close connections, release pins, etc.)

## Creating a Tool Plugin

### Minimal Example

```python
from typing import Any

from smollama.plugins.base import PluginMetadata, ToolPlugin
from smollama.tools.base import ToolParameter


class MyWeatherTool(ToolPlugin):
    """Example weather API tool plugin."""

    def __init__(self) -> None:
        self._api_key = None
        self._client = None

    @property
    def metadata(self) -> PluginMetadata:
        """Plugin metadata."""
        return PluginMetadata(
            name="weather_api",
            version="1.0.0",
            author="Your Name",
            description="Fetch weather data from external API",
            dependencies=["httpx>=0.25.0"],
            plugin_type="tool",
        )

    @property
    def name(self) -> str:
        """Tool name for agent invocation."""
        return "get_weather"

    @property
    def description(self) -> str:
        """Tool description for agent."""
        return "Get current weather for a location"

    @property
    def parameters(self) -> list[ToolParameter]:
        """Tool parameters."""
        return [
            ToolParameter(
                name="location",
                type="string",
                description="City name or coordinates",
                required=True,
            ),
            ToolParameter(
                name="units",
                type="string",
                description="Units (metric or imperial)",
                required=False,
                enum=["metric", "imperial"],
            ),
        ]

    @property
    def config_schema(self) -> dict[str, Any]:
        """Configuration schema."""
        return {
            "type": "object",
            "properties": {
                "api_key": {"type": "string"},
                "base_url": {"type": "string", "format": "uri"},
            },
            "required": ["api_key"],
        }

    def check_dependencies(self) -> tuple[bool, str | None]:
        """Check dependencies."""
        try:
            import httpx  # noqa: F401
            return (True, None)
        except ImportError:
            return (False, "httpx package not installed")

    def setup(self) -> None:
        """Initialize the tool."""
        import httpx

        # Config would be injected here
        self._api_key = "your-api-key"  # From config
        self._client = httpx.AsyncClient()

    def teardown(self) -> None:
        """Cleanup."""
        if self._client is not None:
            import asyncio

            asyncio.create_task(self._client.aclose())
            self._client = None

    async def execute(self, **kwargs: Any) -> Any:
        """Execute the tool."""
        location = kwargs.get("location")
        units = kwargs.get("units", "metric")

        # Call weather API
        response = await self._client.get(
            f"https://api.weather.example.com/current",
            params={"q": location, "units": units, "appid": self._api_key},
        )

        if response.status_code == 200:
            data = response.json()
            return {
                "success": True,
                "temperature": data["main"]["temp"],
                "description": data["weather"][0]["description"],
                "location": location,
                "units": units,
            }
        else:
            return {"success": False, "error": f"API error: {response.status_code}"}
```

### Multiple Tools Per Plugin

If your plugin provides multiple related tools, override `get_tools()`:

```python
class MyAPIPlugin(ToolPlugin):
    # ... metadata, config_schema, etc. ...

    def get_tools(self) -> list[Tool]:
        """Return multiple tools from this plugin."""
        return [
            GetDataTool(self._config),
            PostDataTool(self._config),
            DeleteDataTool(self._config),
        ]
```

## Plugin Metadata

The `PluginMetadata` dataclass contains:

```python
@dataclass
class PluginMetadata:
    name: str               # Unique plugin ID (e.g., "bme280")
    version: str            # Semantic version (e.g., "1.0.0")
    author: str             # Plugin author/maintainer
    description: str        # Human-readable description
    dependencies: list[str] # Python packages (e.g., ["smbus2>=0.4.0"])
    plugin_type: str        # "sensor" or "tool"
```

## Configuration Schema

Use [JSON Schema](https://json-schema.org/) to define your plugin's configuration:

### Simple Example

```python
{
    "type": "object",
    "properties": {
        "enabled": {"type": "boolean", "default": True},
        "poll_interval": {"type": "integer", "minimum": 1, "default": 60}
    }
}
```

### Advanced Example

```python
{
    "type": "object",
    "properties": {
        "bus": {
            "type": "integer",
            "minimum": 0,
            "maximum": 1,
            "description": "I2C bus number"
        },
        "address": {
            "type": "integer",
            "description": "I2C device address (hex)"
        },
        "sensors": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "type": {"type": "string", "enum": ["temperature", "humidity", "pressure"]}
                },
                "required": ["name", "type"]
            }
        }
    },
    "required": ["bus", "address"]
}
```

Invalid configurations will prevent the plugin from loading, with a clear error message.

## Dependency Management

### Declaring Dependencies

List all required Python packages in `metadata.dependencies`:

```python
dependencies=["smbus2>=0.4.0", "RPi.GPIO>=0.7.0"]
```

### Checking Dependencies

Implement `check_dependencies()` to verify packages are available:

```python
def check_dependencies(self) -> tuple[bool, str | None]:
    try:
        import smbus2  # noqa: F401
        import RPi.GPIO  # noqa: F401
        return (True, None)
    except ImportError as e:
        return (False, f"Missing dependency: {e.name}")
```

### Installing Dependencies

Create a `requirements.txt` in your plugin directory:

```txt
smbus2>=0.4.0
RPi.GPIO>=0.7.0
```

Users can install with:
```bash
pip install -r /path/to/plugin/requirements.txt
```

## Installing Plugins

### Using the CLI

```bash
# From Git repository
smollama plugin install https://github.com/user/my-plugin.git

# From local directory
smollama plugin install ./my-plugin

# From local file
smollama plugin install ./my_plugin.py
```

### Manual Installation

1. Copy plugin files to `~/.smollama/plugins/my_plugin/`
2. Install dependencies: `pip install -r ~/.smollama/plugins/my_plugin/requirements.txt`
3. Add to `config.yaml`:

```yaml
plugins:
  paths:
    - ~/.smollama/plugins
  custom:
    - name: my_plugin
      enabled: true
      config:
        # Plugin-specific config here
        bus: 1
        address: 0x76
```

## Testing Your Plugin

### Manual Testing

```python
# test_my_plugin.py
import asyncio
from my_plugin import MyTemperatureSensor


async def test():
    plugin = MyTemperatureSensor()

    # Check dependencies
    deps_ok, error = plugin.check_dependencies()
    if not deps_ok:
        print(f"Dependencies not met: {error}")
        return

    # Setup
    plugin.setup()

    # Read data
    readings = await plugin.read_all()
    for reading in readings:
        print(f"{reading.source_id}: {reading.value} {reading.unit}")

    # Cleanup
    plugin.teardown()


if __name__ == "__main__":
    asyncio.run(test())
```

### Integration Testing

```bash
# Add plugin to config and run Smollama
smollama plugin list  # Verify plugin is discovered and loaded
smollama run          # Start agent with plugin
```

### Checking Plugin Status

```bash
smollama plugin list
```

Output shows:
- ✓ LOADED (green) - Plugin loaded successfully
- ⊘ SKIPPED (yellow) - Dependencies not met
- ✗ FAILED (red) - Error during loading

## Examples

### Example 1: HTTP Polling Sensor

```python
class HTTPSensor(SensorPlugin):
    """Poll a REST API for sensor data."""

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="http_sensor",
            version="1.0.0",
            author="Example",
            description="Poll REST API for sensor readings",
            dependencies=["httpx>=0.25.0"],
            plugin_type="sensor",
        )

    @property
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "format": "uri"},
                "interval_seconds": {"type": "integer", "default": 60},
                "json_path": {"type": "string"},
            },
            "required": ["url"],
        }

    # ... implement remaining methods
```

### Example 2: File Watch Sensor

```python
class FileWatchSensor(SensorPlugin):
    """Watch a file for changes and emit readings."""

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="file_watch",
            version="1.0.0",
            author="Example",
            description="Monitor file for changes",
            dependencies=["watchdog>=3.0.0"],
            plugin_type="sensor",
        )

    # ... implement remaining methods
```

### Example 3: Notification Tool

```python
class NotificationTool(ToolPlugin):
    """Send notifications via multiple channels."""

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="message",
                type="string",
                description="Notification message",
                required=True,
            ),
            ToolParameter(
                name="channel",
                type="string",
                description="Notification channel",
                required=False,
                enum=["email", "sms", "slack"],
            ),
        ]

    # ... implement remaining methods
```

## Best Practices

### 1. Error Handling

Always wrap external operations in try/except:

```python
async def read(self, source_id: str) -> Reading | None:
    try:
        value = self._sensor.read()
        return Reading(...)
    except Exception as e:
        logger.error(f"Failed to read sensor: {e}")
        return None
```

### 2. Logging

Use Python's logging module:

```python
import logging

logger = logging.getLogger(__name__)

def setup(self) -> None:
    logger.info("Initializing sensor")
    # ...
```

### 3. Configuration

Store configuration in the plugin instance:

```python
def __init__(self, config: dict[str, Any] | None = None) -> None:
    self._config = config or {}

def setup(self) -> None:
    self._bus = self._config.get("bus", 1)
    self._address = self._config["address"]  # Required field
```

### 4. Resource Cleanup

Always cleanup in `teardown()`:

```python
def teardown(self) -> None:
    if self._connection is not None:
        try:
            self._connection.close()
        except Exception as e:
            logger.error(f"Error closing connection: {e}")
        finally:
            self._connection = None
```

### 5. Documentation

Add docstrings to all methods:

```python
def read(self, source_id: str) -> Reading | None:
    """Read a specific sensor source.

    Args:
        source_id: Sensor identifier (e.g., "temperature").

    Returns:
        Reading object with sensor data, or None if unavailable.
    """
```

## Troubleshooting

### Plugin Not Discovered

- Check plugin is in a directory listed in `config.yaml` under `plugins.paths`
- Verify plugin file doesn't start with underscore (e.g., not `_my_plugin.py`)
- Ensure plugin class is not abstract (doesn't have `@abstractmethod`)

### Plugin Skipped

- Run `smollama plugin list` to see the reason
- Install missing dependencies: `pip install <package>`
- Check `check_dependencies()` implementation

### Plugin Failed

- Check logs for detailed error message
- Verify `config_schema` is valid JSON Schema
- Ensure `setup()` doesn't raise exceptions

### Configuration Invalid

- Validate config against your schema using online JSON Schema validators
- Check for required fields
- Verify data types match schema

## Resources

- [JSON Schema Documentation](https://json-schema.org/)
- [Example Plugins Repository](#) (coming soon)
- [Smollama Plugin API Reference](#) (coming soon)

## Contributing

Have a useful plugin? Consider sharing it!

1. Publish to GitHub
2. Add a clear README with installation and configuration instructions
3. Include example configuration
4. Submit to the plugin registry (coming soon)

---

**Questions or issues?** Open an issue on the [Smollama GitHub repository](https://github.com/yourusername/smollama).
