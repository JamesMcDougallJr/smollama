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

## Installation

```bash
# Clone the repository
git clone <repo-url>
cd smollama

# Basic install
pip install -e .

# With Raspberry Pi GPIO support
pip install -e ".[pi]"

# With memory/vector search
pip install -e ".[memory]"

# With web dashboard
pip install -e ".[dashboard]"

# Full install (all features)
pip install -e ".[all]"

# Development (includes testing tools)
pip install -e ".[dev]"
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
