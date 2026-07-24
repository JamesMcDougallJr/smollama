"""CLIP frame embedder for the Jetson camera writer (Python 3.6 compatible).

Runs the exported CLIP image encoder (see export_clip.py) inside the Py3.6
jetson_infer.py process, change-gated off detectNet's scene state plus a slow
heartbeat, and drops {json, jpg} spool entries that the smollama edge agent
relays to the master. See docs/frame-search.md.

Integration into jetson_infer.py (the only writer-side change needed):

    import jetson.utils
    from clip_frames import FrameEmbedder

    embedder = FrameEmbedder(
        model_dir=os.path.expanduser("~/clip-export"),
        spool_dir=os.path.expanduser("~/.smollama/frames_spool"),
    )

    # ... in the main loop, after the detect/pose runners produced `readings`:
    rgb = jetson.utils.cudaToNumpy(img)[:, :, :3]  # HWC, uint8 (drop alpha)
    embedder.maybe_capture(
        rgb,
        state={
            "person_count": person_count,
            "labels": sorted(set(detected_classes)),
            "activity": activity,
        },
    )

Requires on the Nano: numpy, Pillow, and onnxruntime (the JetPack 4.6 GPU
wheel from NVIDIA if available, else `pip3 install onnxruntime` for CPU —
change-gating makes even CPU speed acceptable).
"""

import json
import logging
import os
import time
from datetime import datetime, timezone

logger = logging.getLogger("clip_frames")


class WindowAggregator(object):
    """Overlapping mean-pooled embedding windows over sampled frames.

    Pure numpy, no onnxruntime/PIL dependency, so it's importable and
    testable without the ONNX runtime installed. FrameEmbedder feeds it
    samples (reusing a keyframe's embedding when one was just computed, or
    embedding at a fixed low rate otherwise) and drains completed windows
    once per elapsed step boundary.
    """

    def __init__(
        self,
        np_module,
        window_seconds=4.0,
        step_seconds=2.0,
        sample_interval=1.0,
        min_samples=2,
        idle_grace_seconds=2.0,
    ):
        self._np = np_module
        self.window_seconds = window_seconds
        self.step_seconds = step_seconds
        self.sample_interval = sample_interval
        self.min_samples = min_samples
        self.idle_grace_seconds = idle_grace_seconds

        self._samples = []  # dicts: ts, ts_iso, embedding, person_count, labels, jpeg
        self._last_sample_time = None
        self._last_active_time = None
        self._next_step_time = None

        self.windows_emitted = 0
        self.windows_skipped_short = 0
        self.samples_taken = 0
        self.samples_gated = 0

    def is_active(self, now, person_count):
        """True if currently active (person present) or within the idle grace period."""
        if person_count and person_count > 0:
            self._last_active_time = now
            return True
        return (
            self._last_active_time is not None
            and (now - self._last_active_time) <= self.idle_grace_seconds
        )

    def should_sample(self, now, person_count):
        """True if active and enough time has passed since the last sample."""
        if not self.is_active(now, person_count):
            return False
        if self._last_sample_time is not None and (now - self._last_sample_time) < self.sample_interval:
            self.samples_gated += 1
            return False
        return True

    def add_sample(self, now, ts_iso, embedding, person_count, labels, jpeg_bytes=None):
        vec = self._np.asarray(embedding, dtype=self._np.float32)
        self._samples.append({
            "ts": now,
            "ts_iso": ts_iso,
            "embedding": vec,
            "person_count": person_count or 0,
            "labels": list(labels or []),
            "jpeg": jpeg_bytes,
        })
        self._last_sample_time = now
        self.samples_taken += 1
        if self._next_step_time is None:
            self._next_step_time = now + self.step_seconds

    def pop_ready(self, now):
        """Emit every window whose step boundary has elapsed, oldest first."""
        windows = []
        if self._next_step_time is None:
            return windows

        while self._next_step_time <= now:
            end = self._next_step_time
            start = end - self.window_seconds
            in_window = [s for s in self._samples if start <= s["ts"] <= end]
            if len(in_window) >= self.min_samples:
                windows.append(self._build_window(start, end, in_window))
                self.windows_emitted += 1
            else:
                self.windows_skipped_short += 1
            self._next_step_time += self.step_seconds

        cutoff = now - self.window_seconds - self.step_seconds
        self._samples = [s for s in self._samples if s["ts"] >= cutoff]
        return windows

    def _build_window(self, start, end, samples):
        np = self._np
        vecs = np.stack([s["embedding"] for s in samples], axis=0)
        mean = vecs.mean(axis=0)
        norm = float(np.linalg.norm(mean))
        if norm > 0:
            mean = mean / norm

        labels = sorted(set(label for s in samples for label in s["labels"]))
        person_counts = [s["person_count"] for s in samples]
        mid_ts = (start + end) / 2.0
        mid_sample = min(samples, key=lambda s: abs(s["ts"] - mid_ts))

        return {
            "embedding": [float(x) for x in mean],
            "start_ts": start,
            "end_ts": end,
            "start_iso": _iso(start),
            "end_iso": _iso(end),
            "samples": len(samples),
            "person_max": max(person_counts),
            "person_mean": sum(person_counts) / float(len(person_counts)),
            "labels": labels,
            "jpeg": mid_sample["jpeg"],
        }


def _iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().isoformat()


class FrameEmbedder(object):
    """Change-gated CLIP embedding + spool writer for camera frames."""

    def __init__(
        self,
        model_dir,
        spool_dir,
        heartbeat_seconds=300,
        max_spool=500,
        thumb_width=640,
        jpeg_quality=80,
        providers=None,
        windows_enabled=False,
        window_seconds=4.0,
        window_step_seconds=2.0,
        window_sample_interval=1.0,
        window_min_samples=2,
        window_idle_grace_seconds=2.0,
        activity_only=False,
    ):
        import numpy as np

        self._np = np
        self.spool_dir = spool_dir
        self.heartbeat_seconds = heartbeat_seconds
        self.max_spool = max_spool
        self.thumb_width = thumb_width
        self.jpeg_quality = jpeg_quality

        self.activity_only = activity_only

        self._windows = None
        if windows_enabled:
            self._windows = WindowAggregator(
                np,
                window_seconds=window_seconds,
                step_seconds=window_step_seconds,
                sample_interval=window_sample_interval,
                min_samples=window_min_samples,
                idle_grace_seconds=window_idle_grace_seconds,
            )

        with open(os.path.join(model_dir, "meta.json")) as f:
            self.meta = json.load(f)
        self.image_size = self.meta["image_size"]
        self._mean = np.asarray(self.meta["mean"], dtype=np.float32)
        self._std = np.asarray(self.meta["std"], dtype=np.float32)

        onnx_path = os.path.join(model_dir, "image_encoder.onnx")

        # Try onnxruntime first (NVIDIA box.com wheel for JetPack 4.6),
        # fall back to TensorRT via trt_session.TRTSession (already in JetPack).
        try:
            import onnxruntime as ort
            if providers is None:
                wanted = [
                    "TensorrtExecutionProvider",
                    "CUDAExecutionProvider",
                    "CPUExecutionProvider",
                ]
                available = ort.get_available_providers()
                providers = [p for p in wanted if p in available]
            self._session = ort.InferenceSession(onnx_path, providers=providers)
        except ImportError:
            logger.warning("onnxruntime not found — trying TensorRT fallback")
            from trt_session import TRTSession
            self._session = TRTSession(onnx_path)

        self._input_name = self._session.get_inputs()[0].name
        logger.info(
            "CLIP image encoder loaded (%s, %d-d, providers=%s)",
            self.meta.get("model"), self.meta.get("dim"), self._session.get_providers(),
        )

        if not os.path.isdir(spool_dir):
            os.makedirs(spool_dir)

        self._last_state = None
        self._last_capture = 0.0

    # ==================== Gating ====================

    def _should_capture(self, state):
        """Returns 'change', 'heartbeat', or None."""
        now = time.time()
        if self._last_state is not None and state != self._last_state:
            return "change"
        if not self.activity_only and now - self._last_capture >= self.heartbeat_seconds:
            return "heartbeat"
        return None

    # ==================== Capture ====================

    def maybe_capture(self, rgb_image, state):
        """Embed + spool the frame if the scene changed or the heartbeat is due.

        Also feeds the (optional) temporal window aggregator: reuses this
        call's keyframe embedding as a window sample when one was computed,
        otherwise embeds separately at the aggregator's own sample interval
        while the scene is active. Window/keyframe failures are independent —
        one never blocks the other.

        Args:
            rgb_image: HWC uint8 numpy array (RGB, no alpha).
            state: JSON-comparable dict of detectNet scene state; any change
                   vs the previous call triggers a keyframe capture.

        Returns:
            The trigger ('change'/'heartbeat') if a keyframe was captured, else None.
        """
        trigger = self._should_capture(state)
        self._last_state = state
        now = time.time()

        embedding = None
        if trigger is not None:
            try:
                embedding = self.embed(rgb_image)
                # In activity_only + windows mode, keyframes feed the window
                # aggregator but are not spooled — only window entries are sent.
                if not (self.activity_only and self._windows is not None):
                    self._write_spool_entry(rgb_image, embedding, state, trigger)
                self._last_capture = now
            except Exception as e:
                logger.error("Frame capture failed: %s", e)
                trigger = None

        if self._windows is not None:
            try:
                self._maybe_window_sample(rgb_image, state, now, embedding)
            except Exception as e:
                logger.error("Window aggregation failed: %s", e)
            if trigger == "heartbeat":
                logger.info(
                    "Window stats: emitted=%d skipped_short=%d samples=%d gated=%d",
                    self._windows.windows_emitted,
                    self._windows.windows_skipped_short,
                    self._windows.samples_taken,
                    self._windows.samples_gated,
                )

        return trigger

    def _maybe_window_sample(self, rgb_image, state, now, existing_embedding):
        person_count = state.get("person_count", 0) if isinstance(state, dict) else 0
        labels = state.get("labels", []) if isinstance(state, dict) else []

        sample_embedding = existing_embedding
        take_sample = False
        if sample_embedding is not None:
            take_sample = self._windows.is_active(now, person_count)
        elif self._windows.should_sample(now, person_count):
            take_sample = True
            sample_embedding = self.embed(rgb_image)

        if take_sample:
            jpeg_bytes = self._encode_thumb_bytes(rgb_image)
            ts_iso = datetime.now(timezone.utc).astimezone().isoformat()
            self._windows.add_sample(now, ts_iso, sample_embedding, person_count, labels, jpeg_bytes)

        for window in self._windows.pop_ready(now):
            self._write_window_entry(window)

    def embed(self, rgb_image):
        """Run the CLIP image encoder; returns an L2-normalized float list."""
        np = self._np
        arr = self._preprocess(rgb_image)
        outputs = self._session.run(None, {self._input_name: arr})
        vec = np.asarray(outputs[0], dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(vec))
        if norm > 0:
            vec = vec / norm
        return [float(x) for x in vec]

    def _preprocess(self, rgb_image):
        """Resize shorter side to image_size, center crop, normalize, NCHW."""
        from PIL import Image

        np = self._np
        img = Image.fromarray(rgb_image)
        w, h = img.size
        scale = float(self.image_size) / min(w, h)
        img = img.resize((int(round(w * scale)), int(round(h * scale))), Image.BICUBIC)
        w, h = img.size
        left = (w - self.image_size) // 2
        top = (h - self.image_size) // 2
        img = img.crop((left, top, left + self.image_size, top + self.image_size))

        arr = np.asarray(img, dtype=np.float32) / 255.0
        arr = (arr - self._mean) / self._std
        return arr.transpose(2, 0, 1)[None, ...].astype(np.float32)

    # ==================== Spool ====================

    def _encode_thumb_bytes(self, rgb_image):
        """Downscale + JPEG-encode a frame to bytes (shared by keyframes and window samples)."""
        import io

        from PIL import Image

        img = Image.fromarray(rgb_image)
        if img.size[0] > self.thumb_width:
            ratio = float(self.thumb_width) / img.size[0]
            img = img.resize(
                (self.thumb_width, int(img.size[1] * ratio)), Image.BICUBIC
            )
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=self.jpeg_quality)
        return buf.getvalue()

    def _write_spool_entry(self, rgb_image, embedding, state, trigger):
        """Write the {jpg, json} pair; .jpg first so .json marks completeness."""
        self._enforce_cap()

        ts_ms = int(time.time() * 1000)
        base = os.path.join(self.spool_dir, "frame_%d" % ts_ms)

        with open(base + ".jpg", "wb") as f:
            f.write(self._encode_thumb_bytes(rgb_image))

        entry = {
            "schema_version": 2,
            "kind": "keyframe",
            "ts": datetime.now(timezone.utc).astimezone().isoformat(),
            "trigger": trigger,
            "model": self.meta.get("model"),
            "dim": len(embedding),
            "embedding": embedding,
            "labels": state.get("labels", []),
        }
        tmp = base + ".json.tmp"
        with open(tmp, "w") as f:
            json.dump(entry, f)
        os.rename(tmp, base + ".json")

    def _write_window_entry(self, window):
        """Write a temporal window's {jpg, json} pair (same jpg-first discipline)."""
        self._enforce_cap()

        ts_ms = int(window["end_ts"] * 1000)
        base = os.path.join(self.spool_dir, "frame_%d" % ts_ms)

        if window.get("jpeg"):
            with open(base + ".jpg", "wb") as f:
                f.write(window["jpeg"])

        entry = {
            "schema_version": 2,
            "kind": "window",
            "ts": window["end_iso"],
            "trigger": "window",
            "model": self.meta.get("model"),
            "dim": len(window["embedding"]),
            "embedding": window["embedding"],
            "labels": window["labels"],
            "window": {
                "start": window["start_iso"],
                "end": window["end_iso"],
                "samples": window["samples"],
                "person_max": window["person_max"],
                "person_mean": window["person_mean"],
                "actionness": window["person_mean"],
            },
        }
        tmp = base + ".json.tmp"
        with open(tmp, "w") as f:
            json.dump(entry, f)
        os.rename(tmp, base + ".json")

    def _enforce_cap(self):
        """Drop oldest spool entries beyond max_spool (offline master, etc.)."""
        entries = sorted(
            f for f in os.listdir(self.spool_dir)
            if f.startswith("frame_") and f.endswith(".json")
        )
        overflow = len(entries) - self.max_spool + 1
        for name in entries[:max(0, overflow)]:
            logger.warning("Frame spool over cap, dropping %s", name)
            for path in (
                os.path.join(self.spool_dir, name),
                os.path.join(self.spool_dir, name[:-5] + ".jpg"),
            ):
                try:
                    os.remove(path)
                except OSError:
                    pass
