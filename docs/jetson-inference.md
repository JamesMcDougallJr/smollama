# Jetson Nano vision bridge (`jetson_inference` plugin)

This is how a Jetson Nano runs [jetson-inference](https://github.com/dusty-nv/jetson-inference)
deep-vision primitives (object detection, pose estimation, classification, …) and feeds the
results into smollama as live readings — **without** smollama ever importing jetson-inference
or touching a camera frame.

It is designed so you can **swap or add models freely**: the smollama side is
model-agnostic and almost never changes.

---

## Why two processes

jetson-inference's compiled Python bindings exist **only for the system Python 3.6**
(JetPack 4.6 / TensorRT / numpy 1.x). smollama requires **Python ≥ 3.10**. The two cannot
share a process, and rebuilding the jetson bindings for 3.10 fights the platform (numpy 2.x
C-ABI break) — don't.

So there are always two independent processes, joined by a file:

```
 ┌─────────────────────────────┐         ┌──────────────────────────────┐
 │ jetson_infer.py  (Py 3.6)   │  writes │ jetson_inference plugin       │
 │ camera + detectNet/poseNet  │ ──────▶ │ (smollama, Py 3.10)           │
 │ ~/jetson-inference/python/  │  JSON   │ reads file → Reading objects  │
 │   examples/jetson_infer.py  │ contract│ → dashboard / observations    │
 └─────────────────────────────┘         │ → MQTT edge-publish           │
   owns csi://0, never sends images      └──────────────────────────────┘
```

Only **one** process can own the CSI camera, so all enabled networks run sequentially in the
one writer. Images are never written to disk or transmitted — only inference metadata.

---

## The JSON contract (the swap interface)

The writer writes the latest result to `~/.smollama/jetson_inference.json` as a normalized,
self-describing list of readings. The plugin relays each entry verbatim. **This contract is
the reason the plugin is model-agnostic** — change what the writer emits and the readings
change automatically.

```json
{
  "ts": 1739999999.0,
  "model": "detect:ssd-mobilenet-v2,pose:resnet18-body",
  "fps": 8.4,
  "readings": [
    {"name": "object_count", "value": 3,             "unit": "count"},
    {"name": "person_count", "value": 1,             "unit": "count"},
    {"name": "top_object",   "value": "person",      "unit": null,    "metadata": {"objects": [...]}},
    {"name": "pose_count",   "value": 1,             "unit": "count"},
    {"name": "activity",     "value": "arms_raised", "unit": null,    "metadata": {"people": [...]}},
    {"name": "network_fps",  "value": 8.4,           "unit": "fps"}
  ]
}
```

- `ts` (epoch seconds) drives stale-drop: the plugin ignores the file once it is older than
  `max_age_seconds` (default 30) so the dashboard doesn't show frozen data.
- Each `readings[]` entry becomes one smollama reading
  `jetson_inference:<name>` (the `source_type` is configurable).

---

## Running the writer

```bash
cd ~/jetson-inference/python/examples
python3 jetson_infer.py csi://0 --headless --nets detect,pose
```

The **first** run of each model builds a TensorRT `.engine` and can take several minutes;
subsequent runs are fast. Run it under the system `python3` (3.6), not `uv`.

Key flags:

| Flag | Default | Purpose |
|---|---|---|
| `input_URI` | `csi://0` | camera/file/stream (`csi://0`, `/dev/video0`, `file.mp4`, …) |
| `--nets` | `detect,pose` | comma list of primitives to run (`detect`, `pose`, `imagenet`, …) |
| `--<key>-model` | per-net | weights for a primitive, e.g. `--detect-model ssd-mobilenet-v2` |
| `--threshold` | `0.3` | minimum detection/pose confidence |
| `--interval` | `0.0` | min seconds between file writes (`0.0` = every frame) |
| `--out` | `~/.smollama/jetson_inference.json` | contract file path |

Productionize as a **systemd unit** (separate from `smollama run`), like the rest of the
edge setup.

---

## Swapping a model (no code)

Same primitive, different weights — just change the flag:

```bash
python3 jetson_infer.py csi://0 --headless \
    --nets detect --detect-model ssd-inception-v2
```

The readings keep the same names; nothing in smollama changes. Fewer `--nets` = higher FPS.

---

## Adding a new algorithm (one small edit)

When you **update jetson-inference** and a new primitive becomes available (e.g. `actionNet`
for true action recognition, `segNet` for segmentation), add it to the writer's runner
registry. The smollama plugin, config, and dashboard need **no changes** — the contract
absorbs it.

In `jetson_infer.py`, add a `Runner` subclass and register it:

```python
class ActionRunner(Runner):
    key = "action"
    default_model = "resnet18"          # Action-ResNet18-Kinetics, after rebuild

    def __init__(self, model, threshold, argv):
        super().__init__(model, threshold, argv)
        self.net = jetson.inference.actionNet(model, argv)

    def process(self, img):
        class_id, confidence = self.net.Classify(img)
        return [
            {"name": "action", "value": self.net.GetClassDesc(class_id), "unit": None},
            {"name": "action_confidence", "value": round(float(confidence), 4),
             "unit": "probability"},
        ]

RUNNERS = {r.key: r for r in (DetectRunner, PoseRunner, ImagenetRunner, ActionRunner)}
```

Then run with `--nets detect,pose,action`. The plugin will start exposing
`jetson_inference:action` and `jetson_inference:action_confidence` on its own.

### Which primitives are available on this board

The installed build (JetPack 4.6, jetson-inference from 2021) exposes **`imageNet`,
`detectNet`, `poseNet`, `segNet`** — these work today. **`actionNet` is NOT in this build**;
it requires rebuilding jetson-inference from source on the Nano (slow, some dependency risk)
plus downloading the Kinetics models. Check with:

```python
python3 -c "import jetson.inference as ji; print('actionNet' if hasattr(ji,'actionNet') else 'no actionNet')"
```

---

## Enabling the plugin in smollama

In `config.yaml` (or the device's `config.local.yaml`):

```yaml
plugins:
  builtin:
    jetson_inference:
      enabled: true
      config:
        file_path: "~/.smollama/jetson_inference.json"
        max_age_seconds: 30
        # source_type: jetson_inference   # override to run multiple cameras
```

The plugin auto-discovers (`PluginLoader`) — see [plugin-development.md](plugin-development.md)
for the general plugin model. Source: `smollama/plugins/builtin/jetson_inference_plugin.py`.

---

## Verify end-to-end

1. **Writer:** `python3 jetson_infer.py csi://0 --headless` → `cat ~/.smollama/jetson_inference.json`
   shows an advancing `ts` and a `readings[]` list; move / raise your arms to change
   `activity`.
2. **Swap test (proves generality):** rerun with `--nets detect` only — the pose readings
   disappear from the file, and after restarting `uv run smollama run` the plugin exposes
   exactly the sources present, with no code change.
3. **smollama:** `uv run smollama plugin list` shows `jetson_inference`; the dashboard at
   http://localhost:8080 lists `jetson_inference:*` sources updating live and dropping out
   within `max_age_seconds` when the writer stops.
4. **Edge → master MQTT:** on an edge node, confirm forwarding:
   `mosquitto_sub -h <master> -t 'smollama/<node>/readings' -v`.
   ⚠️ The edge node's `config.local.yaml` must set `mqtt.topics.publish_prefix:
   smollama/<node>` explicitly, or the master treats the readings as its own echo.
