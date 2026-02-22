# Multi-Machine Setup Guide

This guide explains how to set up smollama as a distributed system with:
- **Llama Master Node** (MacBook) - Central aggregator with semantic memory
- **Alpaca Edge Nodes** (Raspberry Pis) - Sensor collectors that sync to master

## Architecture Overview

```
┌──────────────────┐
│   MacBook        │
│  (Llama Master)  │
│  • Mem0 server   │
│  • Dashboard     │
│  • Aggregation   │
└────────┬─────────┘
         │ MQTT (pub/sub)
         │ CRDT Sync
         │
┌────────┴──────────┬─────────────┬──────────┐
▼                   ▼             ▼          ▼
Pi (Alpaca)    Pi (Alpaca)   Pi (Alpaca)  ...
Motion          Door          Temp
Sensors         Sensors       Sensors
```

## Step 1: Set Up MQTT Broker

All nodes need a central MQTT broker. You can run this on:
- Your MacBook (recommended for local testing)
- A Raspberry Pi
- A cloud service

**On MacBook using Homebrew:**
```bash
brew install mosquitto
brew services start mosquitto
```

Find your Mac's IP:
```bash
ifconfig | grep "inet " | grep -v 127.0.0.1
# Example output: 192.168.1.50
```

## Step 2: Configure MacBook (Llama Master)

1. Copy the configuration file:
```bash
cp config.llama-master.example.yaml config.yaml
```

2. Edit `config.yaml`:
   - Update `mqtt.broker` to your Mac's IP (e.g., `192.168.1.50`)
   - Update `mqtt.topics.subscribe` if you're adding more Alpaca nodes

3. Start the master node:
```bash
python -m smollama
```

The Llama master will start:
- Ollama server (port 11434)
- Dashboard (port 8080)
- Mem0 server (port 8050) for semantic memory
- MQTT subscriber listening for syncs from Alpacas

## Step 3: Configure Raspberry Pi (Alpaca Node)

1. Copy the configuration file:
```bash
cp config.alpaca-pi.example.yaml config.yaml
```

2. Edit `config.yaml`:
   - Update `node.name` to a unique identifier (e.g., `pi-living-room`)
   - Update `mqtt.broker` to your Mac's IP
   - Update `sync.llama_url` to your Mac's IP (e.g., `http://192.168.1.50:8000`)
   - Configure `plugins.builtin.gpio.config.pins` with your actual sensors

3. Start the Alpaca node:
```bash
python -m smollama
```

The Alpaca node will:
- Read GPIO sensors locally
- Generate observations periodically
- Sync observations to the Llama master every 5 minutes

## Step 4: Verify Communication

Check that nodes are connected:

**On MacBook (master node):**
- Open dashboard: http://localhost:8080
- Should see observations flowing in from Pi nodes
- Monitor MQTT messages in dashboard

**On Pi (Alpaca node):**
```bash
# Check sync status
python -m smollama.tools list_gpio  # See local sensors
```

## Configuration Reference

### Key Settings for Multi-Machine Setup

| Setting | Purpose | Master | Alpaca |
|---------|---------|--------|--------|
| `node.name` | Unique identifier | `llama-master` | Unique per Pi |
| `mqtt.broker` | Central message bus | Any machine | Same as master |
| `sync.enabled` | Send data to master | `false` | `true` |
| `sync.llama_url` | Where to send data | N/A | Master IP:8000 |
| `mem0.enabled` | Aggregate observations | `true` | `false` |
| `ollama.model` | LLM model | Large (llama2:7b) | Small (llama3.2:1b) |

### MQTT Topic Structure

```
smollama/broadcast              # All nodes listen (broadcast announcements)
smollama/llama-master/#         # Master node topics
smollama/pi-living-room/#       # First Alpaca node topics
smollama/pi-bedroom/#           # Second Alpaca node topics
smollama/pi-basement/#          # Third Alpaca node topics
```

## Adding More Alpaca Nodes

1. Copy `config.alpaca-pi.example.yaml` to each new Pi
2. Update:
   - `node.name` (unique per Pi)
   - `mqtt.broker` (point to your broker)
   - `sync.llama_url` (point to MacBook)
   - GPIO pins for that Pi's sensors
3. Start the node with `python -m smollama`
4. Update MacBook's `mqtt.topics.subscribe` if you want it to listen to the new node's topics

## Monitoring

### Dashboard (MacBook)
Open http://localhost:8080 to see:
- All connected nodes
- Observation history
- Memory search across all nodes
- Sensor readings from Alpacas

### MQTT Monitor
```bash
# On Mac: install and monitor MQTT traffic
brew install mosquitto
mosquitto_sub -h localhost -t "smollama/#"
```

### Logs
```bash
# On Pi: tail the agent logs
tail -f ~/.smollama/agent.log
```

## Troubleshooting

### Pi can't connect to MacBook
- Check Mac's IP: `ifconfig | grep "inet "`
- Verify firewall allows connections to port 1883 (MQTT)
- Test connectivity: `ping <mac-ip>` from Pi

### No observations syncing
- Check `sync.enabled: true` on Pi
- Verify `sync.llama_url` points to correct Mac IP
- Check MacBook dashboard for sync status

### MQTT broker not responding
- Verify broker is running: `brew services list` (Mac)
- Check broker IP in both configs
- Ensure Pi and Mac are on same network

## Next Steps

- Add more sensors to Alpaca nodes
- Implement custom plugins in `plugins.paths`
- Set up authentication on MQTT broker
- Deploy Mem0 and Qdrant with docker-compose for production
