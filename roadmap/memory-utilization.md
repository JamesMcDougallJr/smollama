# Memory Utilization Improvements

Smollama runs on Raspberry Pi with constrained RAM. Three interrelated issues degrade system performance:

1. **Ollama cold-loads the model on every observation loop** — the default 5-minute eviction window means the model is always evicted by the time the 15-minute loop fires.
2. **Readings and observations accumulate indefinitely** — `cleanup_old_readings(days=90)` exists but is never called automatically; observations have no cleanup at all.
3. **No compaction** — when the Pi is memory-constrained, old observations pile up without being summarized.

---

## Phase 1: Ollama keep_alive

**Status**: ✅ Complete  
**Effort**: Trivial  
**Outcome**: Model stays in VRAM/RAM between observation loop ticks — no cold-load penalty.

### Background

The Ollama API `chat()` and `embed()` calls accept a `keep_alive` parameter. The default is 5 minutes. Setting it to `-1` keeps the model loaded indefinitely. With a 15-minute observation loop, the model currently cold-loads every single tick.

### Changes

**`smollama/config.py`** — add `keep_alive` to `OllamaConfig`:
```python
@dataclass
class OllamaConfig:
    host: str = "localhost"
    port: int = 11434
    model: str = "gemma4:e2b"
    keep_alive: str = "-1"   # -1 = keep forever; "30m", "1h", "0" also valid
```

**`smollama/ollama_client.py`** — pass `keep_alive` in `chat()` (line ~59):
```python
self._client.chat(
    model=self.config.model,
    messages=messages,
    tools=tools or [],
    keep_alive=self.config.keep_alive,
)
```

**`smollama/memory/embeddings.py`** — thread `keep_alive` through `OllamaEmbeddings.__init__` from `OllamaConfig`, pass in both `embed()` call sites (~lines 168, 206).

**`config.example.yaml`** — document the new field under `ollama:`.

### Verification

```bash
# After starting agent, wait 6+ minutes, then run an observation
uv run smollama run &
sleep 400
ollama ps   # model should still show as resident
```

---

## Phase 2: Automatic Age-off

**Status**: ✅ Complete  
**Effort**: Small  
**Outcome**: Readings and observations are automatically pruned on each loop tick, keeping the database small.

### Background

- `LocalStore.cleanup_old_readings(days=90)` exists but nothing calls it.
- No equivalent cleanup exists for observations.
- On a Pi running 24/7, unchecked accumulation will slow down vector searches and bloat the SQLite file.

### Config additions (`smollama/config.py`)

Add to `MemoryConfig`:
```python
observation_max_age_days: int = 3   # delete observations older than this
readings_max_age_days: int = 7      # delete readings older than this
```

### LocalStore additions (`smollama/memory/local_store.py`)

Add `cleanup_old_observations(days: int) -> int` parallel to the existing `cleanup_old_readings`:

```python
def cleanup_old_observations(self, days: int = 3) -> int:
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    with self._connect() as conn:
        if self._vec_enabled:
            # delete from vector table first (join to get rowids)
            conn.execute("""
                DELETE FROM observations_vec
                WHERE rowid IN (
                    SELECT id FROM observations WHERE timestamp < ?
                )
            """, (cutoff,))
        cursor = conn.execute(
            "DELETE FROM observations WHERE timestamp < ? AND observation_type != 'summary'",
            (cutoff,),
        )
        return cursor.rowcount
```

Note: `observation_type != 'summary'` prevents compacted summaries (Phase 3) from being age-offed too aggressively.

### Observation loop (`smollama/memory/observation_loop.py`)

Wire cleanup into `_do_generate_observation()` before reading sensors:
```python
async def _do_generate_observation(self):
    # Step 0: prune stale data
    self._store.cleanup_old_readings(days=self._readings_max_age_days)
    self._store.cleanup_old_observations(days=self._obs_max_age_days)
    # ... existing sensor read code ...
```

### Verification

```bash
# Insert a stale observation directly
sqlite3 ~/.smollama/memory.db \
  "INSERT INTO observations (timestamp, text) VALUES (datetime('now', '-5 days'), 'old test obs')"

# Run one observation loop tick and confirm deletion
uv run smollama run --skip-preflight &
sleep 35   # past the 30s initial delay
sqlite3 ~/.smollama/memory.db "SELECT count(*) FROM observations WHERE text='old test obs'"
# Should return 0
```

---

## Phase 3: Compaction

**Status**: ✅ Complete  
**Effort**: Small-Medium  
**Outcome**: When free RAM drops below a threshold, the oldest batch of observations is summarized by the LLM into a single `summary`-type observation, freeing context space while preserving meaning.

### Background

Age-off (Phase 2) handles the passage of time. Compaction handles the other axis: the system is low on RAM right now and needs to reduce the working set immediately. The trigger is system memory, not observation age.

`psutil` is already used in `system_plugin.py` — no new dependency needed.

### Config additions (`smollama/config.py`)

Add to `MemoryConfig`:
```python
compact_memory_threshold_mb: int = 200   # compact when free RAM < this
compact_batch_size: int = 20             # observations to summarize per compaction run
```

### LocalStore additions (`smollama/memory/local_store.py`)

```python
def get_oldest_observations(self, limit: int = 20, exclude_type: str = "summary") -> list[dict]:
    """Return the oldest non-summary observations for compaction."""
    with self._connect() as conn:
        rows = conn.execute("""
            SELECT id, timestamp, text, observation_type, confidence
            FROM observations
            WHERE observation_type != ?
            ORDER BY timestamp ASC
            LIMIT ?
        """, (exclude_type, limit)).fetchall()
    return [dict(r) for r in rows]

def delete_observations(self, ids: list[int]) -> None:
    """Hard-delete observations by ID (used after compaction)."""
    if not ids:
        return
    placeholders = ",".join("?" * len(ids))
    with self._connect() as conn:
        if self._vec_enabled:
            conn.execute(f"DELETE FROM observations_vec WHERE rowid IN ({placeholders})", ids)
        conn.execute(f"DELETE FROM observations WHERE id IN ({placeholders})", ids)
```

### Observation loop (`smollama/memory/observation_loop.py`)

Add `_maybe_compact()` and `_summarize_observations()`:

```python
async def _maybe_compact(self) -> None:
    import psutil
    free_mb = psutil.virtual_memory().available / 1024 / 1024
    if free_mb >= self._compact_threshold_mb:
        return

    oldest = self._store.get_oldest_observations(limit=self._compact_batch_size)
    if len(oldest) < 5:
        return  # not enough to bother

    logger.info("Free RAM %.0f MB below threshold — compacting %d observations", free_mb, len(oldest))
    summary = await self._summarize_observations(oldest)
    if summary:
        self._store.add_observation(summary, observation_type="summary", confidence=0.9)
        self._store.delete_observations([o["id"] for o in oldest])
        logger.info("Compacted %d observations into 1 summary", len(oldest))

async def _summarize_observations(self, observations: list[dict]) -> str | None:
    numbered = "\n".join(
        f"{i+1}. [{o['timestamp'][:16]}] {o['text']}"
        for i, o in enumerate(observations)
    )
    prompt = (
        f"Summarize the following {len(observations)} sensor observations from a Raspberry Pi "
        f"into 2-3 sentences capturing the key patterns, trends, and anomalies. "
        f"Be concise.\n\nObservations:\n{numbered}\n\nSummary:"
    )
    try:
        response = await self._agent.query(prompt)
        return response.strip() if response else None
    except Exception as e:
        logger.warning("Compaction summarization failed: %s", e)
        return None
```

Wire into the loop tick after age-off:
```python
async def _do_generate_observation(self):
    self._store.cleanup_old_readings(days=self._readings_max_age_days)
    self._store.cleanup_old_observations(days=self._obs_max_age_days)
    await self._maybe_compact()
    # ... existing code ...
```

### Verification

```bash
# Lower the threshold artificially to force compaction
# In config.yaml:
#   memory:
#     compact_memory_threshold_mb: 99999   # always triggers

# Seed 10+ observations
sqlite3 ~/.smollama/memory.db < seed_observations.sql

# Run one loop tick, observe logs for "Compacting N observations"
# Then verify:
sqlite3 ~/.smollama/memory.db \
  "SELECT observation_type, count(*) FROM observations GROUP BY observation_type"
# Should show at least one row with observation_type='summary'
```

---

## Phase 4: llama.cpp Migration (Future)

**Status**: Not started  
**Effort**: Large  
**Decision point**: Revisit after Phases 1–3 are complete.

### Why consider it

- No separate Ollama process (saves ~100–200 MB RSS)
- Native 4-bit quantization (smaller model footprint)
- No 5-min eviction at all — model is in-process
- Faster first-token latency on Pi 5

### Why defer it

- Phase 1 (keep_alive = -1) eliminates the cold-load penalty at zero cost
- llama.cpp tool calling requires either `llama-server` (OpenAI-compat API) or manual function-call parsing
- Model format change required (GGUF instead of whatever Ollama pulls)

### Clean swap point

If pursued, `smollama/ollama_client.py` is the only file that needs to change. The `OllamaClient.chat() -> ChatResponse` interface is already backend-agnostic. A `LlamaServerClient` using the OpenAI-compatible `/v1/chat/completions` endpoint would be a drop-in replacement — no changes needed to agent, observation loop, or dashboard.
