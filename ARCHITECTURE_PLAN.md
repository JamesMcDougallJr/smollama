# Smollama Architecture Plan

## Vision

Smollama is a distributed LLM system for Raspberry Pi devices that:
- Bundles an Ollama server with small parameter models
- Provides memory for LLM coordination across nodes
- Learns about sensor/GPIO data from the physical world
- Writes continuously updating observations about its environment
- Syncs to shared services when online (may be offline for months)

### Node Types
- **Llama Node** (master): Central node running mem0 server, dashboard, aggregation
- **Alpaca Nodes** (replicas): Edge devices with local LLM, sensors, local memory

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

## Proposed Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         LLAMA NODE (Master)                         │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────────────────────┐│
│  │   Ollama    │  │  Mem0 Server │  │       Dashboard (Web)       ││
│  │ (phi3:mini) │  │  (Qdrant +   │  │  - Node status overview     ││
│  │             │  │   Neo4j)     │  │  - Memory exploration       ││
│  └─────────────┘  └──────────────┘  │  - Sensor data viz          ││
│         │                │          │  - LLM thought streams      ││
│         └────────────────┴──────────└─────────────────────────────┘│
│                          │                                          │
│              ┌───────────┴───────────┐                              │
│              │   MQTT Broker (local) │                              │
│              └───────────────────────┘                              │
└──────────────────────────┬──────────────────────────────────────────┘
                           │ (when online)
        ┌──────────────────┼──────────────────┐
        │                  │                  │
┌───────▼───────┐  ┌───────▼───────┐  ┌───────▼───────┐
│ ALPACA NODE 1 │  │ ALPACA NODE 2 │  │ ALPACA NODE N │
│  ┌──────────┐ │  │  ┌──────────┐ │  │  ┌──────────┐ │
│  │  Ollama  │ │  │  │  Ollama  │ │  │  │  Ollama  │ │
│  │(tinyllama)│ │  │  │(tinyllama)│ │  │  │(tinyllama)│ │
│  └──────────┘ │  │  └──────────┘ │  │  └──────────┘ │
│  ┌──────────┐ │  │  ┌──────────┐ │  │  ┌──────────┐ │
│  │ Local DB │ │  │  │ Local DB │ │  │  │ Local DB │ │
│  │(SQLite + │ │  │  │(SQLite + │ │  │  │(SQLite + │ │
│  │sqlite-vec)│ │  │  │sqlite-vec)│ │  │  │sqlite-vec)│ │
│  └──────────┘ │  │  └──────────┘ │  │  └──────────┘ │
│  ┌──────────┐ │  │  ┌──────────┐ │  │  ┌──────────┐ │
│  │  Sensors │ │  │  │  Sensors │ │  │  │  Sensors │ │
│  │  (GPIO)  │ │  │  │   (I2C)  │ │  │  │  (SPI)   │ │
│  └──────────┘ │  │  └──────────┘ │  │  └──────────┘ │
└───────────────┘  └───────────────┘  └───────────────┘
```

---

## Open Design Questions

### 1. Where Does Intelligence Live?

**Option A: Dumb Alpacas**
- Just log raw sensor data + timestamps
- No LLM on Alpacas
- Llama does all interpretation when synced
- Pro: Simpler, less power
- Con: No local intelligence while offline

**Option B: Observer Alpacas** (likely intent)
- Run tiny LLM to summarize/organize sensor data
- Write observations like "motion detected at 3am, unusual"
- Don't make decisions, just note patterns
- Pro: Local context preserved
- Con: More resources, need to define "minimal reasoning"

**Option C: Semi-Autonomous Alpacas**
- Can trigger simple actions based on patterns
- Pro: Can act while offline
- Con: Scope creep, harder to debug

### 2. Memory Architecture

**Local Memory (per Alpaca):**

1. **Sensor Log** (append-only, raw data)
   - Timestamp, sensor_id, value, metadata
   - Ground truth, never deleted (aged out by retention)

2. **Observation Log** (LLM-generated)
   - Timestamp, observation text, confidence, related sensors
   - Searchable semantically via embeddings

3. **Local Memory** (facts the LLM "knows")
   - "This room typically has motion 7-9am"
   - Persists across restarts

**Shared Memory (Llama node via mem0):**
- Alpacas push observations + local memories on sync
- Alpacas pull cross-node context

**Open question**: Mesh (Alpacas query each other) vs Hub (always through Llama)?

### 3. Offline-First Challenges

**Time drift**: Pis lack battery-backed RTC
- Options: GPS module, NTP on connect, relative timestamps

**Storage limits**: Months of data on SD card
- 1 reading/sec × 90 days = 7.7M readings (~770MB)
- Need retention policy or aggregation

**Sync after months offline**:
- Incremental sync with careful ordering
- What if Llama was also offline?

**Recommendation**: CRDT append-only log with Lamport timestamps

### 4. Dashboard Scope

**Phase 1 - CLI**:
- `smollama logs --tail` for recent observations
- `smollama sensors` for current readings

**Phase 2 - Local Web**:
- Each node serves dashboard on :8080
- Works offline

**Phase 3 - Centralized**:
- Llama aggregates all nodes
- Global view, historical exploration

---

## Implementation Phases

### Phase 1: Local Memory & Observation Log

New components:
- `smollama/memory/local_store.py` - SQLite + sqlite-vec
- `smollama/memory/observation_loop.py` - Continuous observation
- `smollama/tools/memory_tools.py` - recall, write, history tools

### Phase 2: Sync Infrastructure

- `smollama/sync/crdt_log.py` - CRDT-based event log
- `smollama/sync/sync_client.py` - Queue and sync when online

### Phase 3: Dashboard & Visualization

- `smollama/dashboard/` - FastAPI + HTMX web UI

### Phase 4: Llama Node Services

- `smollama/server/` - Mem0 integration, aggregated dashboard

---

## Recommended Dependencies

```toml
[project.optional-dependencies]
memory = [
    "sqlite-vec",
    "sentence-transformers",  # or use Ollama for embeddings
]
sync = [
    "sqlitecloud",  # or custom CRDT
]
dashboard = [
    "fastapi",
    "uvicorn",
    "jinja2",
    "htmx",
]
llama = [
    "mem0ai",
    "qdrant-client",
]
```

---

## Questions to Answer Before Implementing

1. What sensors are being connected? (affects data volume, observation types)
2. Physical setup? (one building vs distributed locations)
3. End goal for observations? (human reading, feeding another system, queryable memory)
4. Power situation? (always-on, solar, intermittent)
5. Embedding model: local per-node or centralized on Llama?
6. Observation frequency: periodic, on-change, or scheduled?

---

## References

- Mem0 Docs: https://docs.mem0.ai/open-source/overview
- Mem0 GitHub: https://github.com/mem0ai/mem0
- Ollama on Pi: https://towardsdatascience.com/running-local-llms-and-vlms-on-the-raspberry-pi-57bd0059c41a/
- Pi Benchmarks: https://blackdevice.com/installing-local-llms-raspberry-pi-cm5-benchmarking-performance/
- SQLite-Sync CRDT: https://github.com/sqliteai/sqlite-sync
- sqlite-vec: https://dev.to/aairom/embedded-intelligence-how-sqlite-vec-delivers-fast-local-vector-search-for-ai-3dpb
- Local-First 2025: https://debugg.ai/resources/local-first-apps-2025-crdts-replication-edge-storage-offline-sync
