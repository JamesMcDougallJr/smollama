# Smollama Roadmap

## Overview

| Plan | Status | Effort | Description |
|------|--------|--------|-------------|
| [Quick Wins](quick-wins.md) | ✅ Complete | Trivial-Small | CLI flags, health endpoint, status improvements |
| [UV Migration](uv-migration.md) | ✅ Complete | Small | Migrate from pip to UV for faster installs and dependency resolution |
| [Install Scripts](install-scripts.md) | ✅ Complete | Medium | `install.sh`, `start.sh`, `setup-pi.sh` |
| [Plugin System](plugin-system.md) | ✅ Complete | Large | `SensorPlugin` / `ToolPlugin` interfaces, plugin loader, discovery |
| [mDNS Discovery](mdns-discovery.md) | ✅ Complete | Small | Zero-config Pi cluster auto-discovery |
| [Improvements](improvements.md) | Not started | Medium | Dashboard, memory, agent, config, MQTT enhancements |
| [WebSocket Dashboard](websocket-dashboard.md) | Not started | Medium | Real-time dashboard updates via WebSocket |
| [Multi-Node Dashboard](multi-node-dashboard.md) | Not started | Medium | Unified view of all nodes |
| [Adaptive Scheduling](adaptive-scheduling.md) | Not started | Small-Medium | Smart observation intervals |
| [Future Directions](future-directions.md) | Not started | Large | Plugin marketplace, Neo4j, other long-term ideas |
| [OpenClaw Integration](OPENCLAW_INTEGRATION.md) | Not started | Medium-Large | Gateway client, bidirectional tools, memory bridge, messaging |

## Progress

- 5 / 11 plans started
- 5 / 11 plans completed (45%)

## Plan Details

### [Quick Wins](quick-wins.md)
Five self-contained items that can each be done in a single session: `--host` flag, `/api/health` endpoint, `--json` status output, configurable log level, reading source count in status.

### [UV Migration](uv-migration.md)
Migrate from pip to UV package manager for faster dependency installation and resolution. UV is 10-100x faster than pip and includes built-in virtual environment management. Seven tasks: verify pyproject.toml compatibility, generate uv.lock, update README installation docs, document developer workflow with `uv sync`, prepare for UV-based install scripts, optional Python version pinning, optional venv configuration.

### [Install Scripts](install-scripts.md)
Three shell scripts (`install.sh`, `start.sh`, `setup-pi.sh`) to simplify first-time setup and daily operation on desktop and Raspberry Pi.

### [Improvements](improvements.md)
Enhancements grouped by subsystem: dashboard (auto-refresh, search, sparklines), memory (retention, export), agent (structured logging, graceful degradation), config (validation), MQTT (reconnect, persistence).

### [Plugin System](plugin-system.md)
Refactor readings into a formal plugin system with `SensorPlugin` and `ToolPlugin` interfaces, plugin discovery, lifecycle hooks, and per-plugin config validation. Move existing GPIO and System providers into `plugins/builtin/`.

### [WebSocket Dashboard](websocket-dashboard.md)
Replace HTMX polling with WebSocket push for real-time dashboard updates. Enables instant sensor reading updates, live observation streams, and reduced server load. FastAPI natively supports WebSocket endpoints.

### [mDNS Discovery](mdns-discovery.md)
Zero-config discovery of Smollama nodes using mDNS/Zeroconf. Nodes announce themselves as `_smollama._tcp` services, enabling automatic cluster formation without manual configuration. Alpaca nodes automatically discover and sync with Llama nodes on the local network. Includes `smollama discovery list` CLI command for debugging.

### [Multi-Node Dashboard](multi-node-dashboard.md)
Unified dashboard view showing readings, observations, and stats from all nodes in a cluster. The Llama node aggregates data from all Alpaca nodes via parallel API calls with caching. Supports per-node filtering and health monitoring.

### [Adaptive Scheduling](adaptive-scheduling.md)
Dynamically adjust observation frequency based on sensor volatility. Increases frequency during rapid changes, decreases during stable periods to save resources. Uses coefficient of variation to measure volatility with configurable thresholds and hysteresis.

### [Future Directions](future-directions.md)
Long-term research directions and speculative features: plugin marketplace (deferred until plugin ecosystem grows), Neo4j graph memory (research needed), federated learning, edge ML training.

### [OpenClaw Integration](OPENCLAW_INTEGRATION.md)
Integration roadmap for connecting smollama (edge intelligence) with OpenClaw (cloud orchestration gateway). Nine integration approaches ranging from quick wins (REST API skill) to deep integration (bidirectional tool bridging, shared memory, session coordination). Recommended implementation order:
1. **Gateway WebSocket Client** - Foundation for bidirectional messaging
2. **Smollama as OpenClaw Skill** - Quick win using existing REST API
3. **Sensor Data Streaming** - Real-time awareness in OpenClaw sessions
4. **OpenClaw as Messaging Layer** - Human alerts via WhatsApp/Telegram
5. **Bidirectional Tool Bridging** - Unified tool ecosystem (smollama ↔ OpenClaw)
6. **Shared Memory Bridge** - Cross-node semantic search (follows Mem0Bridge pattern)
7. **Node Registration & Discovery** - Auto-discovery and capability-based routing
8. **Dashboard Integration** - Unified UI (lower priority)
9. **Session Coordination** - Remote supervision of smollama agents (complex, optional)
