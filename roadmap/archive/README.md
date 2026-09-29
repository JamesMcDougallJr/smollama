# Archived plans

Plans whose work is done and shipped. They stay here rather than being deleted
because several record *why* a decision was made, and that reasoning is often what
you want when revisiting the same area later.

A plan moves here when every phase is complete, or when the remaining phases have
been superseded by a different solution — in which case the plan says so.

| Plan | Completed | Notes |
|---|---|---|
| [Quick Wins](quick-wins.md) | 2026-03 | `--host`, `/api/health`, `--json` status, log level, source count |
| [UV Migration](uv-migration.md) | 2026-03 | pip → uv; `uv.lock`, `uv sync` workflow |
| [Install Scripts](install-scripts.md) | 2026-04 | `install.sh`, `start.sh`, `setup-pi.sh` |
| [Plugin System](plugin-system.md) | 2026-03 | Read/Write/ReadWrite plugin interfaces, loader, discovery |
| [mDNS Discovery](mdns-discovery.md) | 2026-03 | `_smollama._tcp` announce/browse, `smollama discovery list` |
| [Memory Utilization](memory-utilization.md) | 2026-09 | Phases 1–3 shipped. **Phase 4 (llama.cpp) superseded** — see below |

## Memory Utilization: why Phase 4 was dropped

Phase 4 proposed migrating from Ollama to llama.cpp to reduce RAM pressure. That
pressure was instead removed by replacing the model: `gemma4:e2b` (5.1B, 3.6 GB
resident) held enough memory to force ~4.9 GB into swap on an 8 GB Pi. Swapping it
for `qwen2.5:1.5b` (1.1 GB resident) took swap to 1.0 GB and freed 4 GB of RAM,
while cutting an observation cycle from 104s to 35s.

A runtime migration would have been a much larger change for the same goal. Phase 4
is therefore not "todo later" — it is no longer the cheapest way to solve the
problem it was written for. Reopen it only if a model that genuinely needs more
throughput than Ollama provides becomes the right choice.
