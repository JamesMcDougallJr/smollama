# Smollama Roadmap

## Overview

| Plan | Status | Effort | Description |
|------|--------|--------|-------------|
| [Quick Wins](quick-wins.md) | Not started | Trivial-Small | CLI flags, health endpoint, status improvements |
| [Install Scripts](install-scripts.md) | Not started | Medium | `install.sh`, `start.sh`, `setup-pi.sh` |
| [Improvements](improvements.md) | Not started | Medium | Dashboard, memory, agent, config, MQTT enhancements |
| [Plugin System](plugin-system.md) | Not started | Large | `SensorPlugin` / `ToolPlugin` interfaces, plugin loader, discovery |
| [Future Directions](future-directions.md) | Not started | Large | Multi-node aggregation, WebSocket, mDNS, graph memory |

## Progress

- 0 / 5 plans started
- 0 / 5 plans completed

## Plan Details

### [Quick Wins](quick-wins.md)
Five self-contained items that can each be done in a single session: `--host` flag, `/api/health` endpoint, `--json` status output, configurable log level, reading source count in status.

### [Install Scripts](install-scripts.md)
Three shell scripts (`install.sh`, `start.sh`, `setup-pi.sh`) to simplify first-time setup and daily operation on desktop and Raspberry Pi.

### [Improvements](improvements.md)
Enhancements grouped by subsystem: dashboard (auto-refresh, search, sparklines), memory (retention, export), agent (structured logging, graceful degradation), config (validation), MQTT (reconnect, persistence).

### [Plugin System](plugin-system.md)
Refactor readings into a formal plugin system with `SensorPlugin` and `ToolPlugin` interfaces, plugin discovery, lifecycle hooks, and per-plugin config validation. Move existing GPIO and System providers into `plugins/builtin/`.

### [Future Directions](future-directions.md)
Longer-term ideas: multi-node dashboard aggregation, plugin marketplace, WebSocket real-time updates, mDNS auto-discovery, adaptive observation scheduling, Neo4j graph memory.
