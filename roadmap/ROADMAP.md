# Smollama Roadmap

Completed plans live in [`archive/`](archive/README.md). This file lists only what is
active or unstarted.

## Next up

**[Wire the detector layer into the observation loop](detector-loop-integration.md)**
— the detectors and rules are shipped but nothing calls them; the live loop still
asks the model to scan raw readings, which the evaluation harness measured as
producing 0.00 detection on the model in production. Highest value per unit of work
on this list, and it makes six phases of existing machinery actually do something.

## Active

| Plan | Status | Effort | Description |
|------|--------|--------|-------------|
| [Detector → loop integration](detector-loop-integration.md) | Not started | Small-Medium | Replace LLM-as-scanner with detect-then-narrate; skip the model on quiet cycles |
| [Observation rules & closed loop](../docs/observation-rules.md) | Phases 1–6 ✅, 7 blocked | Large | Detectors, rule lifecycle, LLM authoring/review, dry-run actions. Phase 7 (live actuation) needs a human gate |
| [Model evaluation](../docs/model-evaluation.md) | ✅ Complete | Medium | Golden cases, deterministic gates, rubric + cloud judge, `smollama.evals` CLI |
| [Multi-Node Dashboard](multi-node-dashboard.md) | Partially complete | Medium | Node filter bar and `/nodes/{name}` drill-down shipped; cross-node API aggregation not |
| [Improvements](improvements.md) | Partially complete | Medium | Dashboard auto-refresh/search/sparklines done; memory export, structured logging, config validation, MQTT reconnect outstanding |
| [Adaptive Scheduling](adaptive-scheduling.md) | Not started | Small-Medium | Volatility-driven observation intervals. **Reconsider scope** — detector gating may make this redundant |
| [WebSocket Dashboard](websocket-dashboard.md) | Not started | Medium | Replace HTMX polling with push |
| [OpenClaw Integration](OPENCLAW_INTEGRATION.md) | Not started | Medium-Large | Gateway client, tool bridging, memory bridge, messaging |
| [Future Directions](future-directions.md) | Not started | Large | Plugin marketplace, Neo4j, federated learning |

## Known issues not big enough for a plan

| Issue | Where | Why it matters |
|---|---|---|
| `mqtt_bridge_cache.json` rewritten in full per ingest | `readings/mqtt_bridge.py` | O(N²) across N publishing nodes. Free at 11 sources; at ~1000 entries it is throughput and SD-card endurance death. A WAL table fixes it with no new dependency |
| Local readings stored naive-local, relayed ones tz-aware UTC | `readings/system.py`, `gpio.py`, plugins | Same instant appears 6h apart in `readings_log`. Detectors normalize defensively, but the source should emit tz-aware UTC |
| Constant-baseline `level_shift` noise | `detectors/core.py` | `load_avg` moving 0 → 0.27 fires. Unremarkable in practice; needs the `fit:`/feedback evidence from later phases to tune honestly |
| Correlated sources double-report | `detectors/core.py` | `mem_percent` and `mem_available_mb` are one event reported twice; needs cross-source dedup |
| `/activity` cannot score | master node | `onnxruntime` not installed and `~/clip-export/text_encoder.onnx` never exported, so CLIP text search silently falls back to LIKE-over-labels |
| Camera down | Jetson | `gstnvarguscamerasrc: No cameras available`. Needs `scripts/jetson/fix_camera.sh` run with interactive sudo on the device |
| `known_sources` bounded by retention | `detectors/source.py` | `readings_max_age_days` (7d) prunes the table, so a producer dead longer than that cannot be detected as stale at all |

## Progress

- 6 plans archived as complete
- 9 active or unstarted; 1 partially complete ×2, 1 blocked on a human gate
