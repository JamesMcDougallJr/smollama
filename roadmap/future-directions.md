# Future Directions

Larger initiatives and long-term ideas beyond the current implementation scope.

## Multi-node dashboard aggregation
- **Status**: Not started
- Llama node pulls stats from all Alpaca nodes, unified view
- Requires sync infrastructure to expose per-node stats via API

## Plugin marketplace / registry
- **Status**: Not started
- Share sensor plugins via a simple package index
- Depends on: [Plugin System](plugin-system.md)

## WebSocket support
- **Status**: Not started
- Real-time dashboard updates instead of HTMX polling
- Replace or supplement HTMX partial polling with WebSocket push

## Pi cluster auto-discovery
- **Status**: Not started
- mDNS/Avahi for zero-config node registration
- Nodes announce themselves on the local network, Llama node discovers them automatically

## Adaptive observation scheduling
- **Status**: Not started
- Increase observation frequency when sensor readings change rapidly
- Reduce frequency during stable periods to save resources

## Graph memory via Neo4j
- **Status**: Not started
- Leverage Mem0's graph memory for relationship tracking between observations
- Requires Neo4j deployment alongside Qdrant in Docker Compose
