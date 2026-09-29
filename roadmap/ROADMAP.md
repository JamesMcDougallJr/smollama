# Smollama Roadmap

Completed plans live in [`archive/`](archive/README.md). This file lists only what is
active or unstarted.

## Next up

**Watch the new detector-driven loop for a day, then decide.** The detectors are now
wired into the live observation loop (archived
[plan](archive/detector-loop-integration.md)), so for the first time the system has
opinions of its own about what is anomalous. Nothing on this list is worth starting
before a day of real cycles says whether those opinions are any good — the
constant-baseline noise below is the most likely thing to need attention, and
`observation_maintenance_every: 10` means the first lifecycle pass lands ~2.5h in.

After that, the cheapest real work is **finishing the `/activity` path** (install
`onnxruntime`, export `text_encoder.onnx`) — it is two missing artifacts, not a
design problem, and it silently degrades to LIKE-over-labels today.

## Active

| Plan | Status | Effort | Description |
|------|--------|--------|-------------|
| [Observation rules & closed loop](../docs/observation-rules.md) | Phases 1–6 ✅ and live, 7 blocked | Large | Detectors, rule lifecycle, LLM authoring/review, dry-run actions. Phase 7 (live actuation) needs a human gate |
| [Model evaluation](../docs/model-evaluation.md) | ✅ Complete | Medium | Golden cases, deterministic gates, rubric + cloud judge, `smollama.evals` CLI. `--path detectors\|scan` compares both loop architectures |
| [Multi-Node Dashboard](multi-node-dashboard.md) | Partially complete | Medium | Node filter bar and `/nodes/{name}` drill-down shipped; cross-node API aggregation not |
| [Improvements](improvements.md) | Partially complete | Medium | Dashboard auto-refresh/search/sparklines done; memory export, structured logging, config validation, MQTT reconnect outstanding |
| [Adaptive Scheduling](adaptive-scheduling.md) | Not started | Small-Medium | Volatility-driven observation intervals. **Largely superseded** — detector gating now skips the model on quiet cycles, which was most of the value |
| [WebSocket Dashboard](websocket-dashboard.md) | Not started | Medium | Replace HTMX polling with push |
| [OpenClaw Integration](OPENCLAW_INTEGRATION.md) | Not started | Medium-Large | Gateway client, tool bridging, memory bridge, messaging |
| [Future Directions](future-directions.md) | Not started | Large | Plugin marketplace, Neo4j, federated learning |

## Known issues not big enough for a plan

| Issue | Where | Why it matters |
|---|---|---|
| `mqtt_bridge_cache.json` rewritten in full per ingest | `readings/mqtt_bridge.py` | O(N²) across N publishing nodes. Free at 11 sources; at ~1000 entries it is throughput and SD-card endurance death. A WAL table fixes it with no new dependency |
| Local readings stored naive-local, relayed ones tz-aware UTC | `readings/system.py`, `gpio.py`, plugins | Same instant appears 6h apart in `readings_log`. Detectors normalize defensively, but the source should emit tz-aware UTC |
| Constant-baseline `level_shift` noise | `detectors/core.py` | `load_avg` moving 0 → 0.27 fires. Now **reaches the model and the store** rather than sitting in unused code, so it costs an inference and an observation each time. First thing to check after a day of live cycles |
| Correlated sources double-report | `detectors/core.py` | `mem_percent` and `mem_available_mb` are one event reported twice; needs cross-source dedup |
| `/activity` cannot score | master node | `onnxruntime` not installed and `~/clip-export/text_encoder.onnx` never exported, so CLIP text search silently falls back to LIKE-over-labels |
| Camera down | Jetson | `gstnvarguscamerasrc: No cameras available`. Needs `scripts/jetson/fix_camera.sh` run with interactive sudo on the device |
| `known_sources` bounded by retention | `detectors/source.py` | `readings_max_age_days` (7d) prunes the table, so a producer dead longer than that cannot be detected as stale at all |

## Progress

- 7 plans archived as complete
- 8 active or unstarted; 1 partially complete ×2, 1 blocked on a human gate
