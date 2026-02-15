# Smollama Architecture

## Vision

Smollama is a distributed LLM system for Raspberry Pi devices that:
- Bundles an Ollama server with small parameter models
- Provides memory for LLM coordination across nodes
- Learns about sensor/GPIO data from the physical world
- Writes continuously updating observations about its environment
- Syncs to shared services when online (may be offline for months)

### Node Types
- **Llama Node** (master): Central node running Mem0 server, dashboard, aggregation
- **Alpaca Nodes** (replicas): Edge devices with local LLM, sensors, local memory

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           Smollama Node                                 │
│                                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────┐  │
│  │  Readings    │  │   Memory     │  │    Sync      │  │ Dashboard  │  │
│  │  Manager     │  │   Store      │  │   Client     │  │ (FastAPI)  │  │
│  │ ──────────── │  │ ──────────── │  │ ──────────── │  │ ────────── │  │
│  │ • GPIO       │  │ • SQLite     │  │ • CRDT Log   │  │ • HTMX     │  │
│  │   Provider   │  │ • sqlite-vec │  │ • Lamport    │  │ • REST API │  │
│  │ • System     │  │ • Embeddings │  │ • Batch sync │  │ • Partials │  │
│  │   Provider   │  │ • Search     │  │ • Offline OK │  │ • 4 pages  │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └─────┬──────┘  │
│         │                 │                 │                 │         │
│         └─────────────────┼─────────────────┼─────────────────┘         │
│                           │                 │                           │
│   ┌───────────────────────┴──────┐   ┌──────┴──────┐                    │
│   │         Agent                │   │   Ollama    │                    │
│   │  (Tool loop, 10 tools,      │───│   Client    │                    │
│   │   system prompt, agentic)   │   └─────────────┘                    │
│   └───────────────┬──────────────┘                                      │
│                   │                                                     │
│   ┌───────────────┴──────┐   ┌──────────────────┐                       │
│   │     MQTT Client      │   │  Mem0 Bridge     │ (Llama node only)     │
│   │  (pub/sub, events)   │   │  (CRDT → Mem0)   │                       │
│   └──────────────────────┘   └──────────────────┘                       │
└─────────────────────────────────────────────────────────────────────────┘
                    │                        │
       ┌────────────┼────────────┐    ┌──────┴───────┐
       ▼            ▼            ▼    ▼              ▼
  ┌────────┐  ┌────────┐  ┌────────┐  ┌──────┐  ┌──────┐
  │ Node 2 │  │ Node 3 │  │ Node N │  │Qdrant│  │ Mem0 │
  └────────┘  └────────┘  └────────┘  └──────┘  └──────┘
```

### Data Flow: Sensor to Memory

```
GPIO/System Sensor
    │
    ▼
ReadingProvider.read_all()     ─── Reading dataclass (source_type, value, unit, timestamp)
    │
    ├──▶ Dashboard (live display via HTMX polling)
    │
    ├──▶ LocalStore.log_reading()  ─── Persisted to SQLite sensor_log table
    │
    └──▶ ObservationLoop (periodic)
              │
              ▼
         Agent.query("Summarize recent readings...")
              │
              ▼
         LLM generates observation text
              │
              ├──▶ LocalStore.add_observation()  ─── SQLite + vector embedding
              │
              └──▶ CRDTLog.append()  ─── Lamport-timestamped for sync
                        │
                        ▼
                   SyncClient.push()  ─── When online, batch to Llama node
                        │
                        ▼
                   Mem0Bridge._index_new_entries()  ─── Qdrant semantic index
```

---

## Implemented Components

### Readings System

Plugin-based sensor abstraction with a unified `ReadingManager`.

| File | Component | Purpose |
|------|-----------|---------|
| `smollama/readings/base.py` | `Reading` | Dataclass: source_type, source_id, value, unit, timestamp |
| `smollama/readings/base.py` | `ReadingProvider` | ABC: `source_type`, `available_sources`, `read()`, `read_all()` |
| `smollama/readings/base.py` | `ReadingManager` | Registry of providers, unified `read(full_id)` and `read_all()` |
| `smollama/readings/gpio.py` | `GPIOReadingProvider` | Wraps `GPIOReader`, exposes pins as readings |
| `smollama/readings/system.py` | `SystemReadingProvider` | CPU temp, CPU usage, memory, disk, uptime |

### Memory System

SQLite-backed local store with vector search via sqlite-vec.

| File | Component | Purpose |
|------|-----------|---------|
| `smollama/memory/local_store.py` | `LocalStore` | SQLite DB with sensor_log, observations, memories tables |
| `smollama/memory/embeddings.py` | `OllamaEmbeddings` | Embedding provider using Ollama's embedding endpoint |
| `smollama/memory/embeddings.py` | `MockEmbeddings` | Testing stub (random vectors) |
| `smollama/memory/observation_loop.py` | `ObservationLoop` | Background task: reads sensors, prompts LLM, stores observations |

### Sync System

CRDT append-only log with Lamport timestamps for offline-first sync.

| File | Component | Purpose |
|------|-----------|---------|
| `smollama/sync/crdt_log.py` | `CRDTLog` | Append-only event log with Lamport timestamps in SQLite |
| `smollama/sync/crdt_log.py` | `LogEntry` | Dataclass: id, lamport_ts, node_id, event_type, payload |
| `smollama/sync/sync_client.py` | `SyncClient` | HTTP client for pushing/pulling entries to Llama node |

### Tools System

ABC-based tool definitions registered in a `ToolRegistry`, serialized to Ollama's function-calling format.

| File | Tools | Purpose |
|------|-------|---------|
| `smollama/tools/base.py` | `Tool`, `ToolRegistry` | ABC with `name`, `description`, `parameters`, `execute()` |
| `smollama/tools/reading_tools.py` | `ReadSourceTool`, `ListSourcesTool`, `GetReadingHistoryTool` | Sensor access |
| `smollama/tools/memory_tools.py` | `RecallTool`, `RememberTool`, `ObserveTool` | Memory read/write |
| `smollama/tools/gpio_tools.py` | GPIO read/write/list | Direct GPIO control |
| `smollama/tools/mqtt_tools.py` | `PublishTool`, `GetRecentMessagesTool` | MQTT messaging |
| `smollama/mem0/tools.py` | `CrossNodeRecallTool` | Cross-node semantic search via Mem0 |

### Dashboard

FastAPI + HTMX web interface served per-node on port 8080.

| Route | Type | Purpose |
|-------|------|---------|
| `GET /` | HTML | Home page with node name and stats summary |
| `GET /readings` | HTML | Live sensor readings with GPIO mock toggle |
| `GET /observations` | HTML | Observation history browser |
| `GET /memories` | HTML | Memory browser |
| `GET /api/readings` | JSON | Current readings |
| `GET /api/observations` | JSON | Search observations (query, limit, obs_type params) |
| `GET /api/memories` | JSON | Search memories (query, limit params) |
| `GET /api/stats` | JSON | Node stats (name, counts, source types) |
| `GET /htmx/readings` | HTML partial | Live readings list fragment |
| `GET /htmx/observations` | HTML partial | Observations list fragment |
| `GET /htmx/stats` | HTML partial | Stats fragment |
| `GET /htmx/gpio-toggle` | HTML partial | GPIO mode toggle fragment |
| `POST /api/gpio/mode` | HTML partial | Toggle GPIO mock/real mode |

### Mem0 Integration

Bridge that indexes CRDT entries to a self-hosted Mem0 instance for cross-node semantic search.

| File | Component | Purpose |
|------|-----------|---------|
| `smollama/mem0/client.py` | `Mem0Client` | HTTP client for Mem0 server (add, search, health) |
| `smollama/mem0/bridge.py` | `Mem0Bridge` | Background poller: CRDT log entries to Mem0 via Qdrant |
| `smollama/mem0/tools.py` | `CrossNodeRecallTool` | Agent tool for cross-node memory search |
| `deploy/mem0/docker-compose.yml` | Docker Compose | Qdrant + Mem0 server deployment |

### Agent

The `Agent` class (`smollama/agent.py`) ties everything together:
- Initializes all subsystems (Ollama, MQTT, GPIO, Readings, Memory, Sync, Mem0)
- Registers 9-10 tools in the `ToolRegistry`
- Runs an agentic tool loop (up to 10 iterations per message)
- Processes MQTT messages by prompting the LLM with context
- Manages lifecycle: `start()` connects services, `stop()` tears down cleanly

---

## Research Findings

### Small Models for Raspberry Pi

| Model | Size | Speed | Best For |
|-------|------|-------|----------|
| phi3:mini | 1.9GB | ~3.2 tok/s | Best reasoning density |
| tinyllama:1.1b | 0.7GB | ~5.1 tok/s | Fastest, minimal reasoning |
| qwen2:0.5b | 0.5GB | Fast | Smallest footprint |

Pi 5 (8GB) draws under 7W at peak - ideal for always-on deployment.

### Memory Layer: Mem0

Self-hosted via Docker with:
- Qdrant (vector DB) for semantic search
- Optional Neo4j for graph memory (relationships)
- Supports Ollama as LLM backend
- Works fully offline

### Offline-First Sync: CRDTs

- **SQLite-Sync**: CRDT-based SQLite extension for conflict-free merging
- **sqlite-vec**: Embeds vector search directly in SQLite
- Design for offline as default, online as bonus

---

## Design Decisions

### Intelligence: Observer Alpacas (Resolved)

Alpaca nodes run a small LLM to summarize and organize sensor data. They write observations ("motion detected at 3am, unusual") but don't make autonomous decisions. This preserves local context while keeping scope manageable.

### Memory Architecture (Resolved)

Each node maintains three local stores in SQLite:
1. **Sensor Log** - append-only raw readings (source_id, value, timestamp, unit)
2. **Observation Log** - LLM-generated summaries with vector embeddings
3. **Memories** - persistent facts with confidence scores

Shared memory uses a hub model: Alpacas sync to the Llama node via CRDT log, and the Mem0 Bridge indexes entries for cross-node semantic search.

### Sync Strategy (Resolved)

CRDT append-only log with Lamport timestamps. Each entry gets a monotonically increasing Lamport clock value. Entries merge deterministically regardless of arrival order. Batch sync via HTTP when connectivity returns.

### Open Questions

- **Time drift**: Pis lack battery-backed RTC. Options: GPS module, NTP on connect, relative timestamps (Lamport clocks partially address this)
- **Storage retention**: 1 reading/sec for 90 days = ~770MB. Need configurable retention/aggregation policies
- **Embedding model placement**: Currently per-node via Ollama. Centralized embeddings on Llama node could improve consistency at the cost of offline capability
- **Observation frequency tuning**: Currently configurable interval (default 15min). Adaptive scheduling based on sensor change rate is not yet implemented

---

## References

- Mem0 Docs: https://docs.mem0.ai/open-source/overview
- Mem0 GitHub: https://github.com/mem0ai/mem0
- Ollama on Pi: https://towardsdatascience.com/running-local-llms-and-vlms-on-the-raspberry-pi-57bd0059c41a/
- Pi Benchmarks: https://blackdevice.com/installing-local-llms-raspberry-pi-cm5-benchmarking-performance/
- SQLite-Sync CRDT: https://github.com/sqliteai/sqlite-sync
- sqlite-vec: https://dev.to/aairom/embedded-intelligence-how-sqlite-vec-delivers-fast-local-vector-search-for-ai-3dpb
- Local-First 2025: https://debugg.ai/resources/local-first-apps-2025-crdts-replication-edge-storage-offline-sync
