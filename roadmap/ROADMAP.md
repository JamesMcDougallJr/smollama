# Smollama Roadmap

## Overview

| Plan | Status | Effort | Description |
|------|--------|--------|-------------|
| [Quick Wins](quick-wins.md) | Not started | Trivial-Small | CLI flags, health endpoint, status improvements |
| [UV Migration](uv-migration.md) | ✅ Complete | Small | Migrate from pip to UV for faster installs and dependency resolution |
| [Install Scripts](install-scripts.md) | Not started | Medium | `install.sh`, `start.sh`, `setup-pi.sh` |
| [Improvements](improvements.md) | Not started | Medium | Dashboard, memory, agent, config, MQTT enhancements |
| [Plugin System](plugin-system.md) | Not started | Large | `SensorPlugin` / `ToolPlugin` interfaces, plugin loader, discovery |
| [Future Directions](future-directions.md) | Not started | Large | Multi-node aggregation, WebSocket, mDNS, graph memory |
| [OpenClaw Integration](OPENCLAW_INTEGRATION.md) | Not started | Medium-Large | Gateway client, bidirectional tools, memory bridge, messaging |

## Progress

- 1 / 7 plans started
- 1 / 7 plans completed

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

### [Future Directions](future-directions.md)
Longer-term ideas: multi-node dashboard aggregation, plugin marketplace, WebSocket real-time updates, mDNS auto-discovery, adaptive observation scheduling, Neo4j graph memory.

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
