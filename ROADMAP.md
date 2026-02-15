# Smollama Roadmap

## Quick Wins

Small, self-contained improvements that can each be done in a single session.

- **`--host` flag for dashboard** - `smollama dashboard --host 0.0.0.0` is currently hardcoded in `__main__.py:cmd_dashboard`; add a CLI arg like `--port`
- **`/api/health` endpoint** - Return `{"status": "ok", "node": "...", "uptime": ...}` for monitoring and load balancer checks
- **`--json` flag for `smollama status`** - Output machine-readable JSON for scripting and CI
- **Configurable log level via CLI** - `smollama --log-level warning run` instead of only `-v` for debug
- **Reading source count in status** - Show registered `ReadingProvider` count and source IDs in `smollama status` output

## One-Line Install & Startup Scripts

### `scripts/install.sh`
- Detect OS (Debian/Ubuntu/macOS)
- Install Python 3.10+ if missing
- `pip install -e ".[all]"`
- Pull default Ollama model (`ollama pull llama3.2:1b`)
- Copy `config.example.yaml` to `config.yaml` if not present
- Print next-steps summary

### `scripts/start.sh`
- Check and start Ollama (`ollama serve` in background if not running)
- Check and start MQTT broker (`mosquitto -d` if not running)
- Launch `smollama run`

### `scripts/setup-pi.sh`
- Enable GPIO via `raspi-config` non-interactive
- Install system deps (`libgpiod2`, `mosquitto`)
- `pip install -e ".[all,pi]"`
- Create and enable systemd service (`smollama.service`)
- Configure log rotation

These should be documented in the README Quick Start section once implemented.

## Improvements

### Dashboard
- Auto-refresh toggle (currently requires manual page reload; add HTMX polling toggle)
- Search input on observations and memories pages (wired to `/htmx/observations?query=...`)
- Reading sparklines or mini-charts for recent sensor history
- Responsive layout for mobile/tablet viewing

### Memory
- Configurable retention policies (auto-prune sensor_log older than N days)
- Memory export/import (JSON dump of observations + memories for backup/migration)
- Observation deduplication (detect and merge near-duplicate observations)

### Agent
- Structured logging with JSON output option (for log aggregators)
- Configurable max tool iterations per message (currently hardcoded to 10 in `Agent._run_agent_loop`)
- Graceful degradation when Ollama is unreachable (queue messages, retry later)

### Config
- Validate config on load with clear error messages (currently silent defaults)
- Config diff command: `smollama config check` to show effective config with sources

### MQTT
- Reconnect with exponential backoff on broker disconnect
- Message persistence (store undelivered messages to disk, retry on reconnect)
- QoS level configuration per topic

## Custom Sensor Plugin System (Refactor)

A structured plugin system to make adding new sensor types straightforward.

### Plugin Directory
Create `smollama/plugins/` with a plugin loader that scans for `SensorPlugin` subclasses.

### `SensorPlugin` Interface
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

### Plugin Discovery
- Scan `smollama/plugins/` directory for Python modules with `SensorPlugin` subclasses
- Support additional paths via config: `plugins.paths: ["./my_plugins"]`
- Auto-register discovered plugins with `ReadingManager`

### Built-in Plugins
Move existing providers into `plugins/builtin/`:
- `gpio_plugin.py` - Wraps current `GPIOReadingProvider`
- `system_plugin.py` - Wraps current `SystemReadingProvider`

### Example Plugins
- I2C temperature sensor (BME280, DHT22)
- HTTP/webhook sensor (poll a URL, parse JSON response)
- File-watch sensor (monitor a file for changes, emit readings)

### Plugin Config
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

### `ToolPlugin` Interface
Corresponding plugin system for custom agent tools, extending `Tool` (`smollama/tools/base.py`) with the same lifecycle hooks and metadata.

## Future Directions

- **Multi-node dashboard aggregation** - Llama node pulls stats from all Alpaca nodes, unified view
- **Plugin marketplace / registry** - Share sensor plugins via a simple package index
- **WebSocket support** - Real-time dashboard updates instead of HTMX polling
- **Pi cluster auto-discovery** - mDNS/Avahi for zero-config node registration
- **Adaptive observation scheduling** - Increase observation frequency when sensor readings change rapidly
- **Graph memory via Neo4j** - Leverage Mem0's graph memory for relationship tracking between observations
