# Future Directions

Long-term ideas and research directions not yet ready for implementation.

## Plugin Marketplace / Registry
- **Status**: Deferred (waiting for plugin ecosystem growth)
- Share sensor plugins via simple package index
- Plugin versioning, discovery, and installation
- Depends on: Plugin System ✅ (complete)
- **Decision point**: Implement if 10+ community plugins exist

## Neo4j Graph Memory
- **Status**: Research needed
- Leverage graph database for relationship tracking between observations
- Track correlations: "motion in kitchen correlates with living room temp"
- Query patterns: "What usually happens when X reading changes?"
- Requires Neo4j deployment alongside Qdrant in Docker Compose
- **Decision point**: Implement if clear use case emerges (current vector search + CRDT may be sufficient)

## Other Ideas
- Federated learning across nodes
- Edge ML model training
- Predictive maintenance
- Anomaly detection via unsupervised learning
