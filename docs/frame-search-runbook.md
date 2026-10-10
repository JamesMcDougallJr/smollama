# Frame search — deployment runbook (for LLM agents on the Pi and Jetson)

You are an LLM assistant running on one of the smollama machines. This runbook
tells you exactly how to bring up **frame search** (natural-language search over
camera keyframes) on the device you're on. Architecture background is in
[frame-search.md](frame-search.md); this file is the operational checklist.

**First: figure out which machine you're on**, then follow only that section.

| You are on… | How to tell | Your role |
|---|---|---|
| **Desktop/Mac** | macOS, has this repo + fast CPU | Export the CLIP models (one-time) |
| **Jetson Nano** | `cat /etc/nv_tegra_release` exists; `python3 --version` = 3.6 | Camera node: embed frames, write spool |
| **Raspberry Pi master** | `config.yaml` has `node.name: llama-master`, no `agent.mode: edge` | Index frames, serve search |
| **Pi/other edge node with the camera writer's spool** | `config.local.yaml` has `agent.mode: edge` | Relay the spool over MQTT |

The Jetson runs **two** processes: the Py3.6 camera writer (`jetson_infer.py`,
owns the camera, does the CLIP embedding) and the Py3.10 smollama edge agent
(relays the spool). If the Jetson is your machine, you own both.

Ground rules for all machines:
- Never `pip install` into the Jetson's system Python beyond the listed deps —
  JetPack 4.6's Python 3.6 environment is fragile (numpy 1.x C-ABI).
- Use `uv run` for all smollama commands (never bare `python`), **except** the
  Jetson writer side, which must use the system `python3` (3.6).
- If a step's verification fails, stop and report — don't improvise around it.

---

## Stage 0 — Desktop: export the models (prerequisite for everything)

Run in the smollama repo on the desktop:

```bash
uv run --with torch --with open_clip_torch --with onnx --with onnxruntime --with onnxscript \
    python scripts/jetson/export_clip.py --out ~/clip-export
```

**Verify:** the command prints `ONNX<->torch text parity ... 1.000000` and
`ONNX↔torch image parity (cosine): 0.99…` (it exits non-zero and writes no
`text_encoder.onnx` if the text check fails — the exporter can emit a graph that
onnxruntime rejects without raising, and `ClipTextEncoder` swallows load errors),
and
`~/clip-export/` contains `image_encoder.onnx`, `text_encoder.onnx`,
`bpe_simple_vocab_16e6.txt.gz`, `meta.json`, `reference.json`.

Distribute (adjust hostnames):

```bash
ssh nano 'mkdir -p ~/clip-export'
scp ~/clip-export/{image_encoder.onnx,meta.json,reference.json} nano:~/clip-export/
scp scripts/jetson/clip_frames.py scripts/jetson/clip_spike.py \
    nano:~/jetson-inference/python/examples/
ssh llama-master 'mkdir -p ~/clip-export'
scp ~/clip-export/{text_encoder.onnx,bpe_simple_vocab_16e6.txt.gz} llama-master:~/clip-export/
```

⚠️ Every machine must use files from the **same export run** — or, if you re-export
only the text tower later, confirm the new `reference.json` matches the Jetson's
(cosine 1.0 on the same image). Same model + pretrained tag gives the same
embedding space; the check proves it rather than assuming it. The July Jetson image
encoder and the October text encoder were paired this way. Mixing exports
(or models) silently breaks search relevance.

---

## Stage 1 — Jetson: prove the encoder runs (go/no-go gate)

```bash
# GPU wheel is optional but preferred; CPU is acceptable at our frame rates.
# NVIDIA's Jetson onnxruntime-gpu wheel for JetPack 4.6 / Py3.6, if not installed:
#   https://elinux.org/Jetson_Zoo#ONNX_Runtime  → pip3 install <wheel>.whl
# Fallback: pip3 install onnxruntime pillow

cd ~/jetson-inference/python/examples
python3 clip_spike.py ~/clip-export --runs 10 --parity
```

**Go criteria (all three, report the numbers):**
1. `Parity: cosine ≥ 0.99 [OK]` — same embedding space as the desktop
2. Median latency < ~2000 ms/frame
3. Peak RSS leaves room next to detectNet (the writer's total must stay well
   under the Nano's 4 GB shared CPU/GPU memory)

**No-go:** if the model fails to load or parity fails, report it. The fallback
is re-exporting on the desktop with `--model RN50 --pretrained openai`
(Stage 0) and repeating this stage. Do not proceed on a failed gate.

---

## Stage 2 — Jetson: wire the writer

Edit `~/jetson-inference/python/examples/jetson_infer.py` (this file lives on
the Jetson only, not in the repo). The full integration snippet is in the
docstring of `clip_frames.py` sitting next to it. Summary of the three edits:

1. Import + construct once at startup:
   ```python
   from clip_frames import FrameEmbedder
   embedder = FrameEmbedder(
       model_dir=os.path.expanduser("~/clip-export"),
       spool_dir=os.path.expanduser("~/.smollama/frames_spool"),
   )
   ```
2. In the main loop, after the runners produce their readings, get an RGB
   numpy view of the frame (`jetson.utils.cudaToNumpy(img)[:, :, :3]`).
3. Call `embedder.maybe_capture(rgb, state={...})` where `state` holds the
   detectNet-derived scene state (person_count, sorted label list, activity).
   Any change in `state` between calls triggers a capture; otherwise a
   heartbeat fires every 5 minutes. Do NOT call `embedder.embed()` per frame.

Restart the writer (systemd unit if productionized, else the manual command
from [jetson-inference.md](jetson-inference.md)).

**Verify:** writer log shows `CLIP image encoder loaded`. Wave at the camera:

```bash
ls ~/.smollama/frames_spool/    # → frame_<ms>.json + frame_<ms>.jpg appear
python3 -c "import json;d=json.load(open(sorted(__import__('glob').glob('/home/*/.smollama/frames_spool/*.json'))[-1]));print(d['trigger'],d['dim'],d['labels'])"
```

---

## Stage 3 — Edge agent (the smollama process on the camera node)

In the node's `config.local.yaml` (NOT the shared config.yaml):

```yaml
frames:
  enabled: true
  spool_dir: "~/.smollama/frames_spool"
```

Restart the agent (`sudo systemctl restart smollama` or rerun
`uv run smollama run`).

**Verify** from any machine on the LAN (frames flow, then spool drains):

```bash
mosquitto_sub -h <master-ip> -t 'smollama/+/frames' -v -C 1   # blocks until a frame relays
ssh nano 'ls ~/.smollama/frames_spool/'                        # empties within ~30s of each capture
```

If nothing arrives: check the known gotcha — the edge node's
`mqtt.topics.publish_prefix` must be `smollama/<node-name>` explicitly, or the
master drops the messages as its own echo (see jetson-inference.md).

---

## Stage 4 — Master (Raspberry Pi `llama-master`): index + search

```bash
uv sync --extra frames   # onnxruntime + regex: text encoder + tokenizer deps
```

In `config.yaml` (or `config.local.yaml`):

```yaml
frames:
  enabled: true
  clip_text_model: "~/clip-export/text_encoder.onnx"
  clip_tokenizer: "~/clip-export/bpe_simple_vocab_16e6.txt.gz"
  retention_days: 30
  # Optional one-way cloud archival before pruning (must never be a hard
  # dependency — if it fails, frames are kept, not lost):
  # archive_dir: "~/frame-archive"
  # archive_command: "rclone move ~/frame-archive remote:smollama-frames"
```

Restart the agent and dashboard (`./scripts/start.sh`).

**Verify, in order:**
1. Agent log shows `Frame store connected`.
2. After a capture on the Jetson:
   `sqlite3 ~/.smollama/frames.db 'SELECT COUNT(*) FROM frames;'` increments,
   and a `.jpg` appears in `~/.smollama/frames/`.
3. Open `http://<master-ip>:8080/frames` — search for the object you waved.
   The thumbnail should be the top hit. If the page shows a
   **"label search only"** badge, the text encoder didn't load — check the
   two `clip_*` paths and that `onnxruntime`+`regex` imported (agent log has
   the reason as a `CLIP text encoder unavailable` warning).
4. Ask the agent LLM "when did you last see a person?" — it should call the
   `search_frames` tool.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Spike: model won't load on Nano | Opset/EP unsupported by TRT 8.2 / old ORT | Re-export with `--model RN50 --pretrained openai`; rerun Stage 1 |
| Parity < 0.99 | Preprocessing drift or wrong meta.json | Re-copy `meta.json` + `reference.json` from the same export as the .onnx |
| `Rejecting frame … dim X != index dim Y` in master log | CLIP model changed after frames were indexed | Deliberate guard. To switch models: stop agent, delete `~/.smollama/frames.db` + `~/.smollama/frames/*`, restart (reindexes from now on) |
| Frames on MQTT but count not rising | `frames.enabled` false on master, or malformed payload warnings in log | Enable on master; check log |
| Spool grows, nothing publishes | Edge agent down, MQTT broker down, or echo-drop gotcha | Spool is capped (oldest dropped at 500); fix connectivity, backlog then drains automatically |
| "label search only" badge | onnxruntime/regex missing, or bad `clip_*` paths | Stage 4 step; warning in agent log names the exact cause |
| Nano hot / detectNet fps drops | Captures too frequent (busy scene) | Raise `heartbeat_seconds`, coarsen `state` (e.g. drop activity), check cooling |

## Report format

When you finish (or stop at a failed gate), report: machine + stage reached,
the three Stage-1 numbers if you ran it, verification outputs, and any
deviation from this runbook.
