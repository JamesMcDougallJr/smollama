# Smollama

A distributed LLM coordination system for Raspberry Pi and small computers with offline-first memory and sync capabilities.

## Overview

Smollama enables AI agents on resource-constrained devices that can:
- **Sense**: Read GPIO sensors, system metrics, and environmental data
- **Remember**: Store observations and memories with semantic search
- **Think**: Process events using local LLMs via Ollama
- **Communicate**: Share state with other nodes via MQTT
- **Sync**: Operate offline and merge data when connectivity returns

Designed for distributed home automation, environmental monitoring, and edge AI applications.

## Requirements

- Python 3.10+
- Ollama running locally (or accessible on network)
- MQTT broker (e.g., Mosquitto)
- Optional: Raspberry Pi with GPIO pins

## Quick Start

### Automated Installation

The fastest way to get started is with the installation script:

```bash
# Clone repository
git clone <repo-url>
cd smollama

# One-line install (recommended)
./scripts/install.sh

# Or for development
./scripts/install.sh --dev

# Or for minimal installation (core only)
./scripts/install.sh --minimal

# Non-interactive mode (for CI/automation)
./scripts/install.sh --non-interactive
```

The install script will:
- Detect your platform (macOS, Debian, Ubuntu)
- Install Python 3.10+ if needed
- Install UV (fast package manager) or use pip as fallback
- Install smollama and dependencies
- Optionally install Ollama and Mosquitto
- Pull the default LLM model (llama3.2:1b)
- Create a default config.yaml

### Start Services

After installation, use the start script to ensure all services are running:

```bash
# Start Ollama, MQTT, and smollama agent
./scripts/start.sh

# With verbose logging
./scripts/start.sh -v

# With custom options (passed to smollama run)
./scripts/start.sh --host 0.0.0.0 --log-level debug
```

The start script will:
- Check if Ollama is running (auto-start if needed)
- Check if MQTT broker is running (auto-start if needed)
- Verify connectivity with `smollama status`
- Launch the smollama agent

### Raspberry Pi Setup

For production deployment on Raspberry Pi:

```bash
# One-time Pi configuration (requires sudo)
sudo ./scripts/setup-pi.sh

# Manage the service
sudo systemctl status smollama
sudo systemctl start smollama
sudo systemctl stop smollama
sudo systemctl restart smollama

# View logs
journalctl -u smollama -f                # Follow logs in real-time
journalctl -u smollama -n 100            # Last 100 lines
journalctl -u smollama --since today     # Today's logs
```

The Pi setup script will:
- Verify Raspberry Pi hardware
- Enable GPIO interfaces (SPI, I2C)
- Install system dependencies (GPIO libraries, MQTT broker)
- Install smollama with Pi-specific extras
- Create systemd service for auto-start on boot
- Configure log rotation (journal + logrotate)
- Display network info and dashboard URL

## Installation

First, install UV (recommended) or ensure you have pip available:

```bash
# Install UV (recommended - 10-100x faster than pip)
curl -LsSf https://astral.sh/uv/install.sh | sh

# OR install UV via pip
pip install uv
```

### Install with UV (Recommended)

UV provides faster dependency installation and reproducible builds via lockfile.

```bash
# Clone the repository
git clone <repo-url>
cd smollama

# Basic install
uv sync

# Development (includes testing tools)
uv sync --extra dev

# With memory/vector search
uv sync --extra memory

# With web dashboard
uv sync --extra dashboard

# With Raspberry Pi GPIO support (Raspberry Pi only)
uv sync --extra pi

# Full install (all features)
uv sync --all-extras
```

### Install with pip (Alternative)

Traditional pip installation is also supported:

```bash
# Clone the repository
git clone <repo-url>
cd smollama

# Basic install
pip install -e .

# Development (includes testing tools)
pip install -e ".[dev]"

# With memory/vector search
pip install -e ".[memory]"

# With web dashboard
pip install -e ".[dashboard]"

# With Raspberry Pi GPIO support
pip install -e ".[pi]"

# Full install (all features)
pip install -e ".[all]"
```

## Quick Start

```bash
# 1. Copy and edit configuration
cp config.example.yaml config.yaml

# 2. Check connectivity
smollama status

# 3. Start the agent
smollama run

# 4. (Optional) Start the web dashboard
smollama dashboard
```

## Configuration

### Basic Configuration

```yaml
node:
  name: "pi-living-room"

ollama:
  base_url: "http://localhost:11434"
  model: "llama3.2:1b"

mqtt:
  broker: "192.168.1.100"
  port: 1883
  topics:
    subscribe:
      - "home/+/events"
    publish_prefix: "home/living-room"

gpio:
  mock: false  # Set true for development without Pi
  pins:
    - pin: 17
      name: "motion_sensor"
      mode: "input"
    - pin: 18
      name: "led_status"
      mode: "output"
```

### Memory Configuration

```yaml
memory:
  db_path: "~/.smollama/memory.db"
  embedding_provider: "ollama"  # or "mock" for testing
  embedding_model: "all-minilm:l6-v2"
  observation_enabled: true
  observation_interval_minutes: 15
  observation_lookback_minutes: 60
  sensor_log_retention_days: 90
```

### Sync Configuration

```yaml
sync:
  enabled: true
  llama_url: "http://llama-node:8080"  # Central sync server
  sync_interval_minutes: 5
  retry_max_attempts: 3
  batch_size: 100
  crdt_db_path: "~/.smollama/sync.db"
```

### Environment Variables

All config options can be overridden with environment variables (prefix `SMOLLAMA_`):

```bash
export SMOLLAMA_NODE_NAME="pi-kitchen"
export SMOLLAMA_OLLAMA_BASE_URL="http://192.168.1.50:11434"
export SMOLLAMA_MQTT_BROKER="mqtt.local"
export SMOLLAMA_GPIO_MOCK=true
export SMOLLAMA_MEMORY_OBSERVATION_ENABLED=true
```

## CLI Commands

### Check Status

```bash
smollama status
```

Verifies connectivity to Ollama and MQTT broker, lists available models and configured GPIO pins.

### Run the Agent

```bash
# With default config (config.yaml)
smollama run

# With specific config
smollama -c /path/to/config.yaml run

# Verbose mode
smollama -v run
```

### Web Dashboard

```bash
# Start on default port (8080)
smollama dashboard

# Custom port
smollama dashboard -p 3000
```

Access at `http://localhost:8080` to view:
- Live sensor readings
- Observation history
- Memory browser
- System statistics

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                           Smollama Node                             │
│                                                                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌───────────┐  │
│  │   Readings  │  │   Memory    │  │    Sync     │  │ Dashboard │  │
│  │   Manager   │  │   Store     │  │   Client    │  │  (FastAPI)│  │
│  │ ─────────── │  │ ─────────── │  │ ─────────── │  │ ───────── │  │
│  │ • GPIO      │  │ • SQLite    │  │ • CRDT Log  │  │ • HTMX    │  │
│  │ • System    │  │ • Vectors   │  │ • Lamport   │  │ • REST    │  │
│  │ • MQTT      │  │ • Search    │  │ • Offline   │  │ • Live    │  │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └─────┬─────┘  │
│         │                │                │                │        │
│         └────────────────┼────────────────┼────────────────┘        │
│                          │                │                         │
│                   ┌──────┴──────┐  ┌──────┴──────┐                  │
│                   │    Agent    │  │   Ollama    │                  │
│                   │ (Tool Loop) │──│   Client    │                  │
│                   └──────┬──────┘  └─────────────┘                  │
│                          │                                          │
│                   ┌──────┴──────┐                                   │
│                   │    MQTT     │                                   │
│                   │   Client    │                                   │
│                   └─────────────┘                                   │
└─────────────────────────────────────────────────────────────────────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
         ┌────────┐  ┌────────┐  ┌────────┐
         │ Node 2 │  │ Node 3 │  │ Llama  │
         │        │  │        │  │ Server │
         └────────┘  └────────┘  └────────┘
```

## Available Tools

The agent has access to these tools for interacting with the environment:

### Reading Tools
- `read_source(source_id)` - Read a specific sensor (e.g., "system:cpu_temp", "gpio:motion")
- `list_sources(type?)` - List available reading sources
- `get_reading_history(source_id, hours?)` - Get historical readings

### Memory Tools
- `recall(query, limit?)` - Search memories and observations semantically
- `remember(fact, confidence?)` - Store a persistent memory
- `observe(text, type, confidence?, sources?)` - Record a timestamped observation

### GPIO Tools
- `read_gpio(pin)` - Read current state of a GPIO pin
- `list_gpio()` - List all configured pins and states
- `write_gpio(pin, value)` - Set output pin state

### MQTT Tools
- `publish(topic, message)` - Publish an MQTT message
- `get_recent_messages(topic?, count?)` - Get message history

## Memory System

Smollama maintains two types of long-term storage:

### Observations
Timestamped records of what the system noticed:
- Patterns ("Temperature rises every afternoon")
- Anomalies ("Motion detected at unusual hour")
- Status updates ("All sensors nominal")

### Memories
Persistent facts that remain relevant:
- "Normal CPU temperature range is 45-55°C"
- "Motion sensor in hallway triggers frequently due to cat"
- "Node was installed on 2024-01-15"

Both support semantic search via vector embeddings (using Ollama's embedding models).

## Sync Infrastructure

Smollama uses a CRDT (Conflict-free Replicated Data Type) approach for distributed sync:

- **Offline-first**: Nodes can operate independently for extended periods
- **Lamport timestamps**: Establish causal ordering of events
- **Merge without conflicts**: Append-only logs merge deterministically
- **Batch sync**: Efficient transfer when connectivity returns

## Testing

With UV (recommended):

```bash
# Run all tests
uv run pytest tests/ -v

# Run with coverage
uv run pytest tests/ --cov=smollama --cov-report=term-missing

# Run specific test file
uv run pytest tests/test_local_store.py -v

# Run tests matching pattern
uv run pytest tests/ -k "memory" -v
```

Alternatively with pytest directly:

```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=smollama --cov-report=term-missing

# Run specific test file
pytest tests/test_local_store.py -v

# Run tests matching pattern
pytest tests/ -k "memory" -v
```

Current coverage: ~70% across 183 tests.

## Troubleshooting

### UV Installation Issues

If UV is not installed:

```bash
# Install via curl (recommended)
curl -LsSf https://astral.sh/uv/install.sh | sh

# OR install via pip
pip install uv

# Verify installation
uv --version
```

### Python Version Issues

UV requires Python 3.10+. To install a specific Python version:

```bash
# Install Python 3.10
uv python install 3.10

# Verify Python version
uv run python --version
```

### Raspberry Pi GPIO Dependencies

On non-Pi systems (macOS, Linux x86), GPIO dependencies may fail to install. This is expected behavior:

- **During development**: Use `uv sync --extra dev` (excludes pi extras)
- **On Raspberry Pi**: Use `uv sync --extra pi` to install GPIO support
- **Mock GPIO**: Set `gpio.mock: true` in config.yaml for development

### Lockfile Out of Sync

If you see "lockfile out of sync" warnings:

```bash
# Regenerate lockfile
uv lock

# Sync dependencies
uv sync
```

### Force Reinstall

To completely reset your environment:

```bash
# Remove virtual environment and lockfile
rm -rf .venv uv.lock

# Reinstall from scratch
uv sync --extra dev
```

### Dependency Conflicts

If UV reports dependency conflicts:

1. Check that `pyproject.toml` has correct version constraints
2. Try updating dependencies: `uv lock --upgrade`
3. Report issues with specific dependency versions to project maintainers

## Development

### Mock Mode

For development without hardware:

```yaml
gpio:
  mock: true
```

Or via environment:

```bash
export SMOLLAMA_GPIO_MOCK=true
```

### Running Locally

```bash
# Terminal 1: Start Ollama
ollama serve

# Terminal 2: Start MQTT broker (if not already running)
mosquitto

# Terminal 3: Run smollama
smollama -v run
```

### Developer Workflow

#### With UV (Recommended)

```bash
# Sync dependencies after pulling changes
uv sync --extra dev

# Add a new dependency
uv add <package-name>

# Add a development dependency
uv add --dev <package-name>

# Update all dependencies
uv lock --upgrade
uv sync

# Run tests
uv run pytest tests/ -v

# Run smollama commands
uv run smollama status
uv run smollama run
```

#### With pip (Alternative)

```bash
# Install/update dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Run smollama commands
smollama status
smollama run
```

### Project Structure

```
smollama/
├── __init__.py
├── __main__.py          # CLI entry point
├── agent.py             # Main agent loop
├── config.py            # Configuration management
├── ollama_client.py     # Ollama API wrapper
├── mqtt_client.py       # MQTT client
├── gpio_reader.py       # GPIO abstraction
├── readings/            # Unified reading sources
│   ├── base.py          # Reading dataclass & manager
│   ├── system.py        # System metrics provider
│   └── gpio.py          # GPIO reading provider
├── memory/              # Memory subsystem
│   ├── local_store.py   # SQLite + vector storage
│   ├── embeddings.py    # Embedding providers
│   └── observation_loop.py  # Background analysis
├── sync/                # Distributed sync
│   ├── crdt_log.py      # CRDT event log
│   └── sync_client.py   # HTTP sync client
├── dashboard/           # Web interface
│   ├── app.py           # FastAPI application
│   └── templates/       # HTMX templates
└── tools/               # Agent tools
    ├── base.py          # Tool base class
    ├── gpio_tools.py
    ├── mqtt_tools.py
    ├── reading_tools.py
    └── memory_tools.py
```

## License

MIT
