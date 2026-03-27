# Smollama — Developer Guide for Claude

This file documents how to set up, run, and work on smollama. Read this before starting any work in this repo.

## What This Project Is

Smollama is a distributed LLM coordination system for Raspberry Pi and small computers. It has two main processes:

- **Agent** (`smollama run`) — background process that connects to Ollama + MQTT, runs an observation loop, and handles inter-node messaging
- **Dashboard** (`smollama dashboard`) — web UI at http://localhost:8080 showing live sensor readings, observations, and memory

All Python commands use `uv run` (not bare `python` or `pip`) since the project uses a uv-managed venv.

---

## Full Setup Sequence (Raspberry Pi)

Run these steps in order. Don't skip ahead — each step has a dependency on the previous.

### 1. Install Python dependencies

```bash
uv sync --extra pi --extra dashboard --extra memory
```

The `pi` extra includes `gpiozero`, `lgpio`, and `RPi.GPIO`. The `memory` extra includes `sqlite-vec`. All three are needed for full functionality.

**sqlite-vec aarch64 gotcha:** piwheels serves a 32-bit ARM wheel that fails on Pi 5 (aarch64). Pin to `>=0.1.7` which has the correct 64-bit wheel. If you see `wrong ELF class: ELFCLASS32` in logs, fix it with:

```bash
pip download "sqlite-vec>=0.1.7" --no-deps -d /tmp/sv-wheels/
uv pip install /tmp/sv-wheels/sqlite_vec-*.whl
```

### 2. Install and start Mosquitto (MQTT broker)

The agent will immediately exit with `MQTT connection failed` if mosquitto isn't running.

```bash
sudo apt install -y mosquitto
sudo systemctl enable mosquitto
sudo systemctl start mosquitto
```

Verify: `sudo systemctl status mosquitto`

### 3. Ensure Ollama is running with a model

Check what models are already available before pulling anything:

```bash
ollama list
```

Update `config.yaml` to use a model that's actually installed:

```yaml
ollama:
  model: "ministral-3:3b"   # or whatever ollama list shows
```

If no models are available, pull one appropriate for the Pi's RAM:
- Pi 5 (8GB): `ollama pull ministral-3:3b` or `ollama pull qwen2.5:7b-instruct-q4_K_M`
- Pi 4 (4GB): `ollama pull llama3.2:1b` or `ollama pull tinyllama`

### 4. Pull the embedding model

The memory system uses a separate embedding model for semantic search. Pull it once:

```bash
ollama pull all-minilm:l6-v2
```

This is a small (~45MB) model used only for embeddings, not conversation.

### 5. Verify everything before starting

```bash
uv run smollama status
```

Expected output: Ollama connected, model available, MQTT reachable. Fix any errors shown before proceeding.

---

## Running the Servers

### Agent only

```bash
uv run smollama run
```

Logs go to stdout. The agent will:
- Connect to MQTT
- Initialize plugins (HC-SR04 if wired and enabled in config)
- Start the observation loop (first run fires after 30s)

### Dashboard only

```bash
uv run smollama dashboard
```

Available at http://localhost:8080. The dashboard independently initializes plugins and sensor hardware.

### Running both simultaneously

**The agent and dashboard cannot both run at the same time if GPIO-based sensors (e.g. HC-SR04) are enabled.** GPIO pins can only be claimed by one process. You'll see `lgpio.error: 'GPIO busy'` in the second process's logs — this is expected.

Typical workflow: run one or the other, not both. If you need both running, disable sensor plugins in config for the dashboard:

```yaml
plugins:
  builtin:
    hcsr04:
      enabled: false   # disable in whichever process shouldn't own the hardware
```

### Background processes (for monitoring)

```bash
uv run smollama run --skip-preflight > /tmp/smollama-agent.log 2>&1 &
uv run smollama dashboard > /tmp/smollama-dashboard.log 2>&1 &
```

---

## Configuration

Main config file: `config.yaml` (auto-detected at startup, falls back to `config.example.yaml`).

Key sections to check when setting up a new machine:

```yaml
ollama:
  model: "ministral-3:3b"       # must match an installed model from `ollama list`

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

Plugins live in `smollama/plugins/builtin/` and are auto-discovered by `PluginLoader`. They are wired into both the agent and dashboard via `ReadingManager(plugin_loader=plugin_loader)`.

To add a new sensor plugin:
1. Create `smollama/plugins/builtin/yourplugin.py` — subclass `SensorPlugin`
2. Add import + `__all__` entry to `smollama/plugins/builtin/__init__.py`
3. Enable in `config.yaml` under `plugins.builtin`

See `smollama/plugins/builtin/hcsr04_plugin.py` as the reference implementation. Key rules:
- Never import hardware libraries at module level — defer to `check_dependencies()` and `setup()`
- `check_dependencies()` must return `(False, "reason")` on unsupported platforms so the loader skips gracefully
- `teardown()` must release all hardware resources (call `.close()` on gpiozero devices)
- `partial=True` on gpiozero `DistanceSensor` — required so `sensor.distance` returns `None` instead of blocking when out of range

---

## Known Issues / Gotchas

| Symptom | Cause | Fix |
|---|---|---|
| Agent exits immediately with `MQTT connection failed` | Mosquitto not running | `sudo systemctl start mosquitto` |
| `model 'X' not found (404)` | Config references model not pulled | Run `ollama list`, update `config.yaml` |
| `wrong ELF class: ELFCLASS32` for sqlite-vec | piwheels served 32-bit wheel | See sqlite-vec aarch64 fix in setup step 1 |
| `lgpio.error: 'GPIO busy'` | Two processes claiming same GPIO pins | Only run agent OR dashboard, not both, when sensors are enabled |
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
    base.py            # SensorPlugin, ToolPlugin base classes
    loader.py          # PluginLoader — discovery, load, shutdown
    builtin/           # Built-in plugins (system, gpio, hcsr04, macos_temp)
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
```
