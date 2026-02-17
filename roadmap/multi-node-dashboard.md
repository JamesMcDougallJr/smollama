# Multi-Node Dashboard Aggregation

Unified dashboard view showing data from all Smollama nodes in a cluster.

- **Status**: Not started
- **Effort**: Medium (60-80 hours)
- **Priority**: High
- **Dependencies**: Sync ✅, API endpoints ✅, mDNS discovery (optional)

## Overview

Currently, each node's dashboard shows only its own readings/observations. In a multi-node
cluster, you must visit each node's dashboard separately. This adds an aggregation layer
where the Llama node pulls data from all Alpaca nodes and presents a unified view.

## Goals

- Llama dashboard shows readings from all nodes
- Observations and memories from all nodes in one view
- Per-node filtering (show only kitchen node, or all nodes)
- Health status of all nodes (last seen, reachability)
- Efficient caching to avoid hammering Alpaca nodes

## Implementation

### Node Registry
- Track discovered nodes: `{node_name: {url, type, last_seen, status}}`
- Use mDNS discovery (if available) or manual config
- Periodic health check (`/api/health` endpoint)

### Data Aggregation
- Parallel API calls to all nodes
- Endpoints: `/api/readings`, `/api/observations`, `/api/memories`, `/api/stats`
- Merge data: correlate by `source_type:source_id`, aggregate stats
- Cache for 30-60 seconds (configurable)
- Timeout after 5 seconds per node (don't block on slow nodes)

### Dashboard Updates
- New dropdown: "Node: [All | pi-kitchen | pi-living | pi-bedroom]"
- Readings table: Add "Node" column
- Observations: Show which node made the observation
- Stats cards: Sum across all nodes (or show per-node breakdown)

### API Endpoints (New)
- `GET /api/cluster/nodes` - List all discovered nodes
- `GET /api/cluster/readings` - Aggregated readings from all nodes
- `GET /api/cluster/observations` - Aggregated observations
- `GET /api/cluster/stats` - Cluster-wide statistics

## Files to Modify

- `smollama/cluster/aggregator.py` - New module for multi-node aggregation
- `smollama/dashboard/app.py` - Add cluster API endpoints
- `smollama/dashboard/templates/readings.html` - Add node column
- `smollama/dashboard/templates/observations.html` - Add node filter
- `smollama/dashboard/templates/index.html` - Show cluster stats

## Configuration

```yaml
cluster:
  enabled: true              # Enable cluster mode
  aggregation_interval: 60   # Cache aggregated data for 60s
  request_timeout: 5         # Timeout per node (seconds)
  discovery_method: mdns     # "mdns" or "manual"
  manual_nodes:              # If discovery_method=manual
    - name: pi-kitchen
      url: http://192.168.1.10:8080
    - name: pi-living
      url: http://192.168.1.11:8080
```

## Testing

1. Start 3 nodes (1 Llama, 2 Alpaca)
2. Navigate to Llama dashboard
3. Verify readings from all nodes shown
4. Filter by node, verify filtering works
5. Stop one Alpaca, verify marked as "unreachable"
6. Check stats aggregate correctly
