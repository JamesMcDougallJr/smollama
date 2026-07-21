# Frame search — natural-language search over camera keyframes

Search past camera footage by describing it: "black cat", "person carrying a
box". Fully local: a CLIP image encoder runs **on the Jetson** next to
detectNet, embeddings + JPEG thumbnails relay over MQTT to the master, and the
master answers queries with the matching CLIP **text** encoder + sqlite-vec KNN.

> Deploying on the actual devices? Follow the step-by-step, per-machine
> checklist in **[frame-search-runbook.md](frame-search-runbook.md)** — it's
> written for the LLM assistants running on the Pi and Jetson.

> **Design note — images now cross the LAN.** The original jetson bridge never
> wrote or transmitted images. Frame search deliberately relaxes that: sampled
> keyframe thumbnails (~50–100 KB JPEGs) are stored on the Jetson's spool
> briefly and pushed to the master, so search hits can be verified by eye.
> Everything stays on the local network unless you configure cloud archival.

```
Jetson Nano (Py3.6 writer, jetson_infer.py)
  camera → detectNet/poseNet (existing)
         → change-gate + heartbeat → CLIP image encoder   [clip_frames.py]
         → (optional) overlapping temporal windows, mean-pooled embedding
         → spool: frame_<ms>.json (embedding+labels[+window]) + frame_<ms>.jpg
Jetson smollama agent (Py3.10, edge mode)
  drains spool → MQTT  smollama/<node>/frames  (embedding + base64 JPEG)
Master (llama-master)
  → FrameStore: frames.db (sqlite-vec, cosine) + thumbnails on disk
  → dashboard /frames page · search_frames LLM tool
  → (optional) ActivityMatcher: prompt-file categories vs. window embeddings
    → dashboard /activity page · recent_activity LLM tool
  → daily retention: archive (optional) then prune
```

Frames are **not** sampled at a fixed rate. `clip_frames.py` captures when
detectNet's scene state changes (person count, label set, activity) plus a
heartbeat every 5 minutes — typically hundreds of meaningful frames/day
instead of 86 400, which keeps the Nano cool and the index relevant.

**Why two CLIP encoders?** Text and image queries only match if both were
embedded by the *same* CLIP model. The master's `all-minilm` embeddings (used
for observations/memories) are a different space entirely and cannot search
frames. Both ONNX files must come from the same `export_clip.py` run.

---

## Setup

### 1. Export the model (desktop, not the Nano)

```bash
uv run --with torch --with open_clip_torch --with onnx \
    python scripts/jetson/export_clip.py --out ~/clip-export
```

Default is **MobileCLIP-S1** (512-d, edge-sized, ships in open_clip). If the
Nano can't load it (old TensorRT/onnxruntime), fall back to the battle-tested
ResNet CLIP: `--model RN50 --pretrained openai` (1024-d).

### 2. Spike it on the Nano (go/no-go gate)

```bash
scp ~/clip-export/{image_encoder.onnx,meta.json,reference.json} nano:~/clip-export/
scp scripts/jetson/{clip_frames.py,clip_spike.py} nano:~/jetson-inference/python/examples/

# on the Nano (system python3, same as the writer):
python3 clip_spike.py ~/clip-export --runs 10 --parity
```

Check three numbers before proceeding:
- **Parity** cosine ≥ 0.99 — Nano and desktop agree on the embedding space
- **Latency** — anything under ~2 s/frame is fine at change-gated rates
- **Peak RSS** — leave headroom next to detectNet on the 4 GB Nano

If the model fails to load, re-export with `--model RN50 --pretrained openai`
and repeat.

### 3. Wire the writer

`clip_frames.py`'s module docstring contains the exact integration snippet for
`jetson_infer.py` — construct a `FrameEmbedder` at startup and call
`embedder.maybe_capture(rgb, state)` once per loop after the runners. Nano
deps: `numpy`, `Pillow`, `onnxruntime` (NVIDIA's JetPack 4.6 GPU wheel if
available, else CPU).

### 4. Configure the edge node (Jetson's config.local.yaml)

```yaml
frames:
  enabled: true
  spool_dir: "~/.smollama/frames_spool"
```

The edge agent drains the spool each publish cycle and deletes entries only
after a successful MQTT publish, so frames survive master/broker outages (the
spool is capped at `spool_max_entries`, oldest dropped first).

### 5. Configure the master

```yaml
frames:
  enabled: true
  clip_text_model: "~/clip-export/text_encoder.onnx"
  clip_tokenizer: "~/clip-export/bpe_simple_vocab_16e6.txt.gz"
  retention_days: 30
  # optional cloud archival before pruning (one-way, skippable when offline):
  # archive_dir: "~/frame-archive"
  # archive_command: "rclone move ~/frame-archive remote:smollama-frames"
```

Master deps: `uv pip install onnxruntime regex` (text encoder + tokenizer).
Without them the /frames page still works, degraded to label-substring search
— the UI shows a "label search only" badge so degradation is never silent.

The index dimension is fixed by the **first frame ingested**; switching CLIP
models later means deleting `frames.db` and reindexing (mismatched frames are
rejected with an error, not silently mixed).

---

## Using it

- **Dashboard:** http://master:8080/frames — search box + thumbnail grid with
  timestamps, node, detectNet labels, and similarity scores.
- **Agent tool:** the LLM can call `search_frames` ("when did you last see a
  cat?") alongside `search_observations`.

## Zero-shot activity triage (optional, on top of frame search)

Categories are natural-language prompts, not a trained model — see
`config/activity_prompts.yaml`. Editing that file is the only step; the master
re-embeds and hot-reloads it on the next scored frame, no restart, and no
change ever needs to reach the edge fleet.

**Wire contract v2:** `clip_frames.py` can additionally emit overlapping
temporal windows (mean-pooled, renormalized embedding over ~4s of sampled
frames) alongside its existing change-gated keyframes. Both shapes flow
through the same spool/MQTT topic; a `kind` field (`"keyframe"` or `"window"`)
and `schema_version: 2` distinguish them. A payload with no `kind` is treated
as a keyframe (v1 compatibility), and a v2 edge talking to an unupgraded
master ingests windows as keyframes — degraded (no scoring) but harmless.
Window payloads add one field:

```json
"window": {
  "start": "<ISO>", "end": "<ISO>",
  "samples": 4, "person_max": 2, "person_mean": 1.5, "actionness": 1.5
}
```

Enable windows by passing `windows_enabled=True` (plus `window_seconds`,
`window_step_seconds`, etc.) to `FrameEmbedder` in `jetson_infer.py`, and set
`frames.activity_prompts` on the master. The master scores each incoming
window against the prompt file (`smollama/frames/activity_matcher.py`):
cosine similarity against every category **and** every `distractor: true`
background category, argmax decides, and a real category only "wins" if it
beats the best background explanation and clears its threshold — otherwise
the window is stored unmatched ("abstain"). Full per-category scores persist
in `activity_scores` for later threshold tuning; the dashboard/tool only
surface the top match.

- **Dashboard:** http://master:8080/activity — ranked review queue of matched
  windows with evidence (person count, detectNet labels, thumbnail).
- **Agent tool:** `recent_activity` ("has anything been flagged recently?").

This is a triage/review surface, not an autonomous alert: everything it
produces is a scored candidate for a human to look at, never an automated
action.

## Retention

Daily, the master deletes frames older than `retention_days` — row, vector,
and thumbnail together. If `archive_dir` is set, thumbnails + a
`metadata.jsonl` are staged there first (and `archive_command` runs, e.g.
rclone); **if archival fails, nothing is pruned**. Rough sizing at gated
rates: ~20–50 MB/day of thumbnails, vectors are negligible.

## Verify end-to-end

1. Writer logs `CLIP image encoder loaded`; wave something at the camera →
   a `frame_*.json`/`.jpg` pair appears in the spool.
2. `mosquitto_sub -h <master> -t 'smollama/<node>/frames' -v` shows payloads
   within one edge publish cycle, and the spool empties.
3. Search for the object on /frames — the thumbnail should be the top hit
   with a high similarity score; a nonsense query should score visibly lower.
4. Kill the master, wave again, restart: the spooled frame arrives with its
   original (capture-time) timestamp.
