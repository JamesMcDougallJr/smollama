"""Jetson Nano camera writer for smollama (Python 3.6, system interpreter).

Runs jetson-inference primitives (detectNet, poseNet, imageNet) sequentially
on the camera stream, writes a model-agnostic JSON contract to
~/.smollama/jetson_inference.json that the smollama edge agent relays to
the master, and optionally feeds the CLIP frame embedder to produce activity
window embeddings.

Usage (system python3, NOT uv):
    python3 jetson_infer.py csi://0 --headless --nets detect,pose

Flags:
    input_URI   Camera / file / stream (csi://0, /dev/video0, file.mp4 …)
    --nets      Comma list of primitives: detect, pose, imagenet
    --out       Contract file path (default ~/.smollama/jetson_inference.json)
    --interval  Min seconds between writes (default 0 = every frame)
    --headless  Disable DISPLAY output

Writer config (optional, written by `smollama deploy`):
    ~/.smollama/writer_config.json  — controls FrameEmbedder settings.
    See cluster.example.yaml for all keys.

Model-swapping and adding new algorithms: see docs/jetson-inference.md.
"""

from __future__ import print_function

import argparse
import json
import os
import sys
import time


# ── Writer config ────────────────────────────────────────────────────────────

_WRITER_CFG_PATH = os.path.expanduser("~/.smollama/writer_config.json")


def _load_writer_cfg():
    try:
        with open(_WRITER_CFG_PATH) as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        print("[jetson_infer] writer_config.json not found or invalid: %s" % e,
              file=sys.stderr)
        return {}


# ── Runner protocol ──────────────────────────────────────────────────────────

class Runner(object):
    key = None
    default_model = None

    def __init__(self, model, threshold, argv):
        self.model = model or self.default_model
        self.threshold = threshold

    def process(self, img):
        """Run inference; return list of {name, value, unit} dicts."""
        raise NotImplementedError


class DetectRunner(Runner):
    key = "detect"
    default_model = "ssd-mobilenet-v2"

    def __init__(self, model, threshold, argv):
        super(DetectRunner, self).__init__(model, threshold, argv)
        import jetson.inference
        self.net = jetson.inference.detectNet(self.model, argv, threshold)

    def process(self, img):
        detections = self.net.Detect(img, overlay="none")
        counts = {}
        for d in detections:
            label = self.net.GetClassDesc(d.ClassID)
            counts[label] = counts.get(label, 0) + 1

        top = max(counts, key=counts.get) if counts else None
        return [
            {"name": "object_count", "value": len(detections), "unit": "count"},
            {"name": "person_count",
             "value": counts.get("person", 0), "unit": "count"},
            {"name": "top_object",
             "value": top, "unit": None,
             "metadata": {"counts": counts}},
        ]

    def state_for_embedder(self, img):
        """Returns (person_count, sorted_labels, activity) for the frame embedder."""
        detections = self.net.Detect(img, overlay="none")
        person_count = 0
        labels = set()
        for d in detections:
            label = self.net.GetClassDesc(d.ClassID)
            labels.add(label)
            if label == "person":
                person_count += 1
        return person_count, sorted(labels), None


class PoseRunner(Runner):
    key = "pose"
    default_model = "resnet18-body"

    def __init__(self, model, threshold, argv):
        super(PoseRunner, self).__init__(model, threshold, argv)
        import jetson.inference
        self.net = jetson.inference.poseNet(self.model, argv, threshold)
        self._last_activities = []

    def _classify_pose(self, pose):
        kp = {self.net.GetCategory(k.ID): k for k in pose.Keypoints}
        activities = []
        lw = kp.get("left_wrist")
        rw = kp.get("right_wrist")
        ls = kp.get("left_shoulder")
        rs = kp.get("right_shoulder")
        if lw and ls and lw.y < ls.y:
            activities.append("left_arm_raised")
        if rw and rs and rw.y < rs.y:
            activities.append("right_arm_raised")
        if lw and rw and ls and rs:
            if lw.y < ls.y and rw.y < rs.y:
                activities.append("arms_raised")
        return activities[0] if activities else None

    def process(self, img):
        poses = self.net.Process(img, overlay="none")
        activities = [a for p in poses for a in [self._classify_pose(p)] if a]
        self._last_activities = activities
        return [
            {"name": "pose_count", "value": len(poses), "unit": "count"},
            {"name": "activity",
             "value": activities[0] if activities else None, "unit": None,
             "metadata": {"activities": activities}},
        ]


class ImagenetRunner(Runner):
    key = "imagenet"
    default_model = "googlenet"

    def __init__(self, model, threshold, argv):
        super(ImagenetRunner, self).__init__(model, threshold, argv)
        import jetson.inference
        self.net = jetson.inference.imageNet(self.model, argv)

    def process(self, img):
        class_id, confidence = self.net.Classify(img)
        return [
            {"name": "imagenet_class",
             "value": self.net.GetClassDesc(class_id), "unit": None},
            {"name": "imagenet_confidence",
             "value": round(float(confidence), 4), "unit": "probability"},
        ]


RUNNERS = {r.key: r for r in (DetectRunner, PoseRunner, ImagenetRunner)}


# ── Contract writer ──────────────────────────────────────────────────────────

def write_contract(path, model_str, fps, readings):
    tmp = path + ".tmp"
    payload = {
        "ts": time.time(),
        "model": model_str,
        "fps": round(fps, 2),
        "readings": readings,
    }
    with open(tmp, "w") as f:
        json.dump(payload, f)
    os.rename(tmp, path)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input_URI", default="csi://0", nargs="?")
    parser.add_argument("--nets", default="detect,pose")
    parser.add_argument("--out",
                        default=os.path.expanduser("~/.smollama/jetson_inference.json"))
    parser.add_argument("--interval", type=float, default=0.0)
    parser.add_argument("--threshold", type=float, default=0.3)
    parser.add_argument("--headless", action="store_true")
    # Per-net model overrides, e.g. --detect-model ssd-inception-v2
    for key in RUNNERS:
        parser.add_argument("--%s-model" % key, default=None)

    args, remaining_argv = parser.parse_known_args()

    import jetson.utils

    print("[jetson_infer] Loading networks: %s" % args.nets)
    net_keys = [k.strip() for k in args.nets.split(",") if k.strip()]
    runners = []
    for key in net_keys:
        if key not in RUNNERS:
            print("[jetson_infer] Unknown net %r, skipping" % key, file=sys.stderr)
            continue
        model = getattr(args, "%s_model" % key, None)
        runners.append(RUNNERS[key](model, args.threshold, remaining_argv))

    if not runners:
        print("[jetson_infer] No valid nets, exiting", file=sys.stderr)
        sys.exit(1)

    model_str = ",".join("%s:%s" % (r.key, r.model) for r in runners)

    # Optional CLIP frame embedder (reads writer_config.json)
    embedder = None
    detect_runner = next((r for r in runners if isinstance(r, DetectRunner)), None)
    try:
        sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
        from clip_frames import FrameEmbedder
    except ImportError:
        print("[jetson_infer] clip_frames not found — frame embedder disabled",
              file=sys.stderr)
        FrameEmbedder = None

    if FrameEmbedder is not None:
        try:
            cfg = _load_writer_cfg()
            embedder = FrameEmbedder(
                model_dir=os.path.expanduser(
                    cfg.get("model_dir", "~/clip-export")),
                spool_dir=os.path.expanduser(
                    cfg.get("spool_dir", "~/.smollama/frames_spool")),
                heartbeat_seconds=cfg.get("heartbeat_seconds", 300),
                activity_only=cfg.get("activity_only", False),
                windows_enabled=cfg.get("windows_enabled", False),
                window_seconds=cfg.get("window_seconds", 4.0),
                window_step_seconds=cfg.get("window_step_seconds", 2.0),
                window_sample_interval=cfg.get("window_sample_interval", 1.0),
            )
            print("[jetson_infer] CLIP embedder ready (activity_only=%s, windows=%s)"
                  % (cfg.get("activity_only"), cfg.get("windows_enabled")))
        except Exception as e:
            print("[jetson_infer] CLIP embedder init failed: %s" % e, file=sys.stderr)

    out_dir = os.path.dirname(args.out)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir)

    camera = jetson.utils.videoSource(args.input_URI, argv=remaining_argv)
    if not args.headless:
        display = jetson.utils.videoOutput("display://0", argv=remaining_argv)
    else:
        display = None

    print("[jetson_infer] Running. Output → %s" % args.out)
    frame_times = []
    last_write = 0.0

    while True:
        img = camera.Capture()
        if img is None:
            break

        now = time.time()
        frame_times.append(now)
        frame_times = [t for t in frame_times if now - t < 1.0]
        fps = len(frame_times)

        readings = []
        activity = None
        for runner in runners:
            try:
                readings.extend(runner.process(img))
                if isinstance(runner, PoseRunner) and runner._last_activities:
                    activity = runner._last_activities[0]
            except Exception as e:
                print("[jetson_infer] %s.process error: %s" % (runner.key, e),
                      file=sys.stderr)

        if now - last_write >= args.interval:
            write_contract(args.out, model_str, fps, readings)
            last_write = now

        if embedder is not None:
            try:
                rgb = jetson.utils.cudaToNumpy(img)[:, :, :3]
                if detect_runner is not None:
                    person_count, labels, _ = detect_runner.state_for_embedder(img)
                else:
                    person_count, labels = 0, []
                embedder.maybe_capture(rgb, state={
                    "person_count": person_count,
                    "labels": labels,
                    "activity": activity,
                })
            except Exception as e:
                print("[jetson_infer] embedder error: %s" % e, file=sys.stderr)

        if display is not None:
            display.Render(img)
            display.SetStatus("smollama | %d FPS" % fps)
            if not display.IsStreaming():
                break


if __name__ == "__main__":
    main()
