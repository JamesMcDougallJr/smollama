# mDNS Auto-Discovery for Pi Clusters

Zero-config discovery of Smollama nodes using mDNS/Avahi.

- **Status**: ✅ Complete
- **Completed**: 2026-02-16
- **Effort**: Small (completed in ~6 hours)
- **Priority**: High
- **Dependencies**: Sync client ✅, Config ✅

## What Was Implemented

### Phase 0: Sync Infrastructure (Prerequisites)
- **Phase 0A**: Added `/api/sync/push` and `/api/sync/pull` endpoints to Dashboard
  - Enables remote nodes to sync CRDT log entries via HTTP
  - Initializes CRDTLog in dashboard when sync is enabled
- **Phase 0B**: Integrated SyncClient into Agent lifecycle
  - Initializes SyncClient in `Agent.__init__()`
  - Starts `sync_loop()` in `Agent.start()`
  - Stops sync loop gracefully in `Agent.stop()`

### Phase 1: Core mDNS Discovery Module
- Created `smollama/discovery/mdns.py` with three classes:
  - **ServiceAnnouncer**: Publishes this node via mDNS
    - Service type: `_smollama._tcp.local.`
    - TXT records: `node_type` (llama/alpaca), `version`
    - Announces on dashboard port (default 8080)
  - **ServiceBrowser**: Discovers other Smollama nodes
    - Maintains registry with URL, type, last_seen timestamp
    - Cache TTL: 5 minutes (configurable)
    - Thread-safe access to discovered nodes
  - **DiscoveryManager**: High-level coordinator
    - Starts/stops announcer and browser
    - Provides `get_discovered_nodes()` and `wait_for_discovery()` APIs

### Phase 2: Configuration System
- Added `DiscoveryConfig` dataclass to `smollama/config.py`:
  ```yaml
  discovery:
    enabled: true                    # Enable mDNS discovery
    service_type: _smollama._tcp     # mDNS service type
    announce: true                   # Announce this node
    browse: true                     # Browse for other nodes
    cache_ttl_seconds: 300           # 5 minutes
    discovery_timeout_seconds: 10    # Wait up to 10s on startup
  ```
- Added YAML parsing and environment variable overrides (`SMOLLAMA_DISCOVERY_*`)

### Phase 3-4: Agent Integration
- Initializes DiscoveryManager in `Agent.__init__()`
  - Determines node type: "llama" if `mem0.bridge_enabled`, else "alpaca"
- Starts discovery in `Agent.start()`:
  - If sync enabled but no URL configured, waits for discovery
  - Looks for Llama node and updates SyncClient URL dynamically
  - Starts sync_loop once URL is discovered
- Stops discovery gracefully in `Agent.stop()`

### Phase 5: Dashboard Integration
- Updated `create_app()` to accept `discovery_manager` parameter
- Added `/api/discovery/nodes` endpoint to list discovered nodes
- Updated `cmd_dashboard()` to:
  - Initialize DiscoveryManager with dashboard port
  - Start discovery on startup
  - Stop discovery on shutdown

### Phase 6: CLI Integration
- Added `smollama discovery list` command:
  - Browses for nodes on local network
  - Displays node name, type, URL, and last seen timestamp
  - Useful for debugging and network verification

### Phase 7: Dependencies
- Added `zeroconf>=0.132.0` to dashboard dependencies in `pyproject.toml`

## Overview

Currently, multi-node setups require manual configuration of `sync.llama_url` for each
Alpaca node. mDNS enables nodes to announce themselves on the local network and discover
peers automatically.

## Goals

- Nodes announce themselves via mDNS service: `_smollama._tcp`
- Llama node discovers all Alpaca nodes automatically
- Alpaca nodes discover Llama node without config
- Handle nodes joining/leaving network gracefully

## Implementation

### Service Announcement
- Publish on startup: `<node-name>._smollama._tcp.local`
- TXT records: `node_type=llama|alpaca`, `version=0.1.0`, `capabilities=gpio,system`
- Port: Dashboard port (default 8080)
- Update on shutdown (graceful deregistration)

### Discovery
- Llama node browses for `_smollama._tcp` services
- Build registry: `{node_name: {url, type, capabilities}}`
- Cache for 5 minutes (refresh periodically)
- Sync client uses discovered URLs instead of config

### Configuration
```yaml
discovery:
  enabled: true           # Enable mDNS discovery
  service_type: _smollama._tcp
  announce: true          # Announce this node
  browse: true            # Browse for other nodes
  cache_ttl_seconds: 300  # 5 minutes
```

## Files to Modify

- `smollama/discovery/mdns.py` - New module for mDNS logic
- `smollama/config.py` - Add DiscoveryConfig
- `smollama/__main__.py` - Start mDNS announcer on startup
- `smollama/sync/sync_client.py` - Use discovered URLs

## Dependencies

- PyPI: `zeroconf>=0.132.0` (pure Python, cross-platform)

## Usage Guide

### Zero-Config Setup (Recommended)

**Llama Node** (central node with Mem0):
```yaml
# config-llama.yaml
node:
  name: pi-llama

mem0:
  enabled: true
  bridge_enabled: true  # This makes it a Llama node

sync:
  enabled: true
  # llama_url: ""  # Leave empty - no need to configure!

discovery:
  enabled: true
  announce: true
  browse: false  # Llama doesn't need to browse (yet)
```

**Alpaca Node** (worker nodes):
```yaml
# config-alpaca.yaml
node:
  name: pi-kitchen

mem0:
  enabled: false
  bridge_enabled: false  # This makes it an Alpaca node

sync:
  enabled: true
  # llama_url: ""  # Leave empty - will be discovered automatically!

discovery:
  enabled: true
  announce: true
  browse: true  # Browse to find Llama node
```

### Starting Nodes

**On Llama Node:**
```bash
# Install with dashboard dependencies
pip install -e ".[dashboard]"

# Start dashboard (required for mDNS announcement)
smollama dashboard --config config-llama.yaml
```

**On Alpaca Node:**
```bash
# Install with dashboard dependencies
pip install -e ".[dashboard]"

# Start dashboard (required for discovery)
smollama dashboard --config config-alpaca.yaml
```

The Alpaca node will automatically:
1. Browse for mDNS services on startup
2. Discover the Llama node's URL
3. Start syncing data automatically

### Manual Override

If you need to explicitly configure the Llama URL (e.g., for debugging):
```yaml
sync:
  enabled: true
  llama_url: "http://192.168.1.100:8080"  # Explicit URL takes precedence
```

### Troubleshooting

**Check discovered nodes:**
```bash
smollama discovery list --config config-alpaca.yaml
```

**Verify mDNS announcements (macOS/Linux):**
```bash
# Browse for services
dns-sd -B _smollama._tcp

# Lookup specific node
dns-sd -L pi-llama _smollama._tcp
```

**Check logs:**
```bash
smollama dashboard --config config.yaml --verbose
```

Look for:
- `"Announcing service: pi-llama._smollama._tcp.local. at 192.168.1.100:8080 (type=llama)"`
- `"Discovered node: pi-llama (llama) at http://192.168.1.100:8080"`
- `"Sync loop started"`

## Testing

1. Start two nodes on same network
2. Verify mDNS announcements: `dns-sd -B _smollama._tcp`
3. Check Llama discovers Alpaca automatically
4. Stop Alpaca node, verify deregistration
5. Restart Alpaca, verify rediscovery
