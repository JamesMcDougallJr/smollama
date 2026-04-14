# Smollama — Developer Guide for Claude

This file documents how to set up, run, and work on smollama. Read this before starting any work in this repo.

## What This Project Is

Smollama is a distributed LLM coordination system for Raspberry Pi and small computers. It has two main processes:

- **Agent** (`smollama run`) — background process that connects to Ollama + MQTT, runs an observation loop, and handles inter-node messaging
- **Dashboard** (`smollama dashboard`) — web UI at http://localhost:8080 showing live sensor readings, observations, and memory

All Python commands use `uv run` (not bare `python` or `pip`) since the project uses a uv-managed venv.

---

## Setup

Everything is handled by a single install script:

```bash
./scripts/install.sh
```

This auto-detects the platform (Raspberry Pi vs macOS vs other Linux), installs all dependencies (UV, Python packages, Mosquitto, Ollama), pulls the default models (`gemma4:e2b` + `all-minilm:l6-v2` for embeddings), and creates `config.yaml`. On Pi it also installs GPIO system libraries and enables SPI/I2C.

For development installs: `./scripts/install.sh --dev`. For minimal (core only): `./scripts/install.sh --minimal`.

**sqlite-vec aarch64 gotcha:** piwheels serves a 32-bit ARM wheel that fails on Pi 5 (aarch64). Pin to `>=0.1.7` which has the correct 64-bit wheel. If you see `wrong ELF class: ELFCLASS32` in logs, fix it with:

```bash
pip download "sqlite-vec>=0.1.7" --no-deps -d /tmp/sv-wheels/
uv pip install /tmp/sv-wheels/sqlite_vec-*.whl
```

Verify everything:

```bash
uv run smollama status
```

---

## Running the Servers

### Both agent and dashboard (recommended)

```bash
./scripts/start.sh
```

This starts Mosquitto and Ollama if needed, then launches the agent and dashboard together. The agent starts first and claims GPIO pins; the dashboard starts 2 seconds later and falls back to mock GPIO mode automatically.

Flags:
- `--no-dashboard` — agent only
- `--dashboard-port 3000` — custom dashboard port
- `-v` — verbose logging
- Pass extra args to the agent after `--`: `./scripts/start.sh -- --skip-preflight`

### Individual processes

```bash
uv run smollama run          # Agent only
uv run smollama dashboard    # Dashboard only (http://localhost:8080)
```

**GPIO conflict:** If both processes run independently and GPIO sensors are enabled, the second process will see `lgpio.error: 'GPIO busy'`. The start script handles this automatically (agent gets GPIO, dashboard uses mock). If running manually, disable sensors in one process:

```yaml
plugins:
  builtin:
    hcsr04:
      enabled: false   # disable in whichever process shouldn't own the hardware
```

---

## Configuration

Main config file: `config.yaml` (auto-detected at startup, falls back to `config.example.yaml`).

Key sections to check when setting up a new machine:

```yaml
ollama:
  model: "gemma4:e2b"       # must match an installed model from `ollama list`

plugins:
  builtin:
    hcsr04:
      enabled: true             # only if sensor is physically wired
      config:
        trig_pin: 23            # BCM 23 = Pi Pin 16
        echo_pin: 24            # BCM 24 = Pi Pin 18 (via voltage divider)
    macos_temp:
      enabled: false            # always false on Pi
```

HC-SR04 wiring (if sensor is connected):
- VCC → Pi Pin 2 (5V)
- GND → Pi Pin 6 (GND)
- Trig → Pi Pin 16 / BCM 23 (direct)
- Echo → Pi Pin 18 / BCM 24 (via 1kΩ + 2kΩ voltage divider)

---

## Plugin System

Plugins live in `smollama/plugins/builtin/` and are auto-discovered by `PluginLoader`. There are three plugin types:

- **ReadPlugin** — ingests data into smollama (sensors, metrics). Extends `ReadingProvider` so the `ReadingManager` can query them. Examples: HC-SR04, system metrics, GPIO inputs, macOS temp.
- **WritePlugin** — takes actions on the world. Exposes LLM-callable tools via `get_tools()`. Examples: 7-segment display plugins (SH5461AS, S5161AS).
- **ReadWritePlugin** — hybrid that both reads data and performs actions (e.g., a relay that can be read and toggled).

All three share a common lifecycle via `PluginLifecycleMixin`: `metadata`, `setup()`, `teardown()`, `config_schema`, `check_dependencies()`.

The old names `SensorPlugin` and `ToolPlugin` are aliased for backwards compatibility.

### GPIO backend

Display plugins (and any future GPIO write plugins) use `smollama/plugins/builtin/gpio_backend.py` which auto-detects Pi 4 (RPi.GPIO) vs Pi 5 (lgpio). The Pi5-specific plugin variants have been merged into the main plugins — `SH5461ASPi5Plugin` and `S5161ASPi5Plugin` are now aliases.

### Adding a new plugin

1. Create `smollama/plugins/builtin/yourplugin.py` — subclass `ReadPlugin`, `WritePlugin`, or `ReadWritePlugin`
2. Add import + `__all__` entry to `smollama/plugins/builtin/__init__.py`
3. Enable in `config.yaml` under `plugins.builtin`

See `smollama/plugins/builtin/hcsr04_plugin.py` (read) or `smollama/plugins/builtin/sh5461as_plugin.py` (write) as reference implementations. Key rules:
- Never import hardware libraries at module level — defer to `check_dependencies()` and `setup()`
- `check_dependencies()` must return `(False, "reason")` on unsupported platforms so the loader skips gracefully
- `teardown()` must release all hardware resources (call `.close()` on gpiozero devices, `backend.cleanup()` on GPIO backends)
- `partial=True` on gpiozero `DistanceSensor` — required so `sensor.distance` returns `None` instead of blocking when out of range

---

## Known Issues / Gotchas

| Symptom | Cause | Fix |
|---|---|---|
| Agent exits immediately with `MQTT connection failed` | Mosquitto not running | `sudo systemctl start mosquitto` |
| `model 'X' not found (404)` | Config references model not pulled | Run `ollama list`, update `config.yaml` |
| `wrong ELF class: ELFCLASS32` for sqlite-vec | piwheels served 32-bit wheel | See sqlite-vec aarch64 fix in setup step 1 |
| `lgpio.error: 'GPIO busy'` | Two processes claiming same GPIO pins | Use `./scripts/start.sh` (handles this automatically) or disable sensors in one process |
| `A LIMIT or 'k = ?' constraint is required` | sqlite-vec KNN query missing `k=?` | Fixed in `local_store.py` — if it recurs, KNN queries need `AND k = ?` in WHERE clause, not just `LIMIT ?` |
| `store_observation` AttributeError | Wrong method name | Fixed — the method is `add_observation` |
| Observation loop fires but does nothing | LLM model not available | Check `ollama list` and update config model |

---

## Project Structure

```
smollama/
  __main__.py          # CLI entry point — cmd_run, cmd_dashboard, etc.
  agent.py             # Main agent loop — MQTT + LLM + readings
  config.py            # Config dataclasses and YAML loader
  plugins/
    base.py            # ReadPlugin, WritePlugin, ReadWritePlugin base classes
    loader.py          # PluginLoader — discovery, load, shutdown
    builtin/
      gpio_backend.py  # GPIO abstraction (RPi.GPIO vs lgpio auto-detection)
      system_plugin.py # System metrics (ReadPlugin)
      gpio_plugin.py   # GPIO pin readings (ReadPlugin)
      hcsr04_plugin.py # HC-SR04 ultrasonic sensor (ReadPlugin)
      macos_temp_plugin.py  # macOS CPU temp (ReadPlugin)
      sh5461as_plugin.py    # SH5461AS 7-segment display (WritePlugin)
      s5161as_plugin.py     # S5161AS 7-segment display (WritePlugin)
  readings/
    base.py            # Reading dataclass, ReadingProvider, ReadingManager
  memory/
    local_store.py     # SQLite-backed memory with sqlite-vec embeddings
    observation_loop.py # Background LLM observation generation
  dashboard/
    app.py             # FastAPI dashboard app
demos/
  hc-sr04/             # Standalone HC-SR04 demo (no smollama dependency)
scripts/
  install.sh           # Full install script for fresh machines
  start.sh             # Start all services (Mosquitto, Ollama, agent, dashboard)
  setup-pi.sh          # Production Pi setup (systemd, logrotate)
```
