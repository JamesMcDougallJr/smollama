# Subsystem Improvements

Enhancements to existing components, grouped by subsystem.

## Dashboard
- **Status**: Not started
- Auto-refresh toggle (currently requires manual page reload; add HTMX polling toggle)
- Search input on observations and memories pages (wired to `/htmx/observations?query=...`)
- Reading sparklines or mini-charts for recent sensor history
- Responsive layout for mobile/tablet viewing

## Memory
- **Status**: Not started
- Configurable retention policies (auto-prune sensor_log older than N days)
- Memory export/import (JSON dump of observations + memories for backup/migration)
- Observation deduplication (detect and merge near-duplicate observations)

## Agent
- **Status**: Not started
- Structured logging with JSON output option (for log aggregators)
- Configurable max tool iterations per message (currently hardcoded to 10 in `Agent._run_agent_loop`)
- Graceful degradation when Ollama is unreachable (queue messages, retry later)

## Config
- **Status**: Not started
- Validate config on load with clear error messages (currently silent defaults)
- Config diff command: `smollama config check` to show effective config with sources

## MQTT
- **Status**: Not started
- Reconnect with exponential backoff on broker disconnect
- Message persistence (store undelivered messages to disk, retry on reconnect)
- QoS level configuration per topic
