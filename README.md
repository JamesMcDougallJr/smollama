# Smollama

A distributed LLM coordination system for Raspberry Pi and small computers.

## Overview

Smollama lets you run AI agents on Raspberry Pi devices that can:
- Read GPIO sensors (motion detectors, switches, etc.)
- Communicate with other nodes via MQTT
- Process events using local LLMs via Ollama

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

# Install with pip
pip install -e .

# For Raspberry Pi GPIO support
pip install -e ".[pi]"

# For development
pip install -e ".[dev]"
```

## Configuration

Copy the example config and customize:

```bash
cp config.example.yaml config.yaml
```

Key configuration options:

```yaml
node:
  name: "pi-living-room"

ollama:
  host: "localhost"
  port: 11434
  model: "llama3.2:1b"

mqtt:
  broker: "192.168.1.100"
  port: 1883

gpio:
  mock: false  # Set true for development without Pi
  pins:
    - pin: 17
      name: "motion_sensor"
      mode: "input"
```

Environment variables can override config (prefix with `SMOLLAMA_`):
- `SMOLLAMA_NODE_NAME`
- `SMOLLAMA_OLLAMA_HOST`
- `SMOLLAMA_MQTT_BROKER`
- `SMOLLAMA_GPIO_MOCK`

## Usage

### Check Status

```bash
smollama status
```

This verifies connectivity to Ollama and the MQTT broker.

### Run the Agent

```bash
smollama run
```

Or with a specific config:

```bash
smollama -c /path/to/config.yaml run
```

### Verbose Mode

```bash
smollama -v run
```

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                    Smollama Node                    │
│  ┌───────────┐  ┌───────────┐  ┌───────────────┐   │
│  │  Ollama   │  │   MQTT    │  │  GPIO Reader  │   │
│  │  Client   │  │  Client   │  │               │   │
│  └─────┬─────┘  └─────┬─────┘  └───────┬───────┘   │
│        │              │                │           │
│        └──────────────┼────────────────┘           │
│                       │                            │
│               ┌───────┴───────┐                    │
│               │     Agent     │                    │
│               │  (Tool Loop)  │                    │
│               └───────────────┘                    │
└─────────────────────────────────────────────────────┘
```

The agent listens for MQTT messages and uses the LLM to:
1. Understand the message context
2. Decide which tools to use (if any)
3. Execute tools and gather results
4. Generate a response

## Available Tools

- `read_gpio(pin)` - Read current state of a GPIO pin
- `list_gpio()` - List all configured pins and their states
- `publish(topic, message)` - Publish an MQTT message
- `get_recent_messages(topic, count)` - Get message history

## Testing

```bash
# Run tests
pytest tests/

# Run with coverage
pytest tests/ --cov=smollama
```

## Development

For development without a Raspberry Pi, enable mock GPIO mode:

```yaml
gpio:
  mock: true
```

Or via environment:

```bash
export SMOLLAMA_GPIO_MOCK=true
```

## License

MIT
