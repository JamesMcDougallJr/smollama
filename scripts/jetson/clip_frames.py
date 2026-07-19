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
    ):
        import numpy as np
        import onnxruntime as ort

        self._np = np
        self.spool_dir = spool_dir
        self.heartbeat_seconds = heartbeat_seconds
        self.max_spool = max_spool
        self.thumb_width = thumb_width
        self.jpeg_quality = jpeg_quality

        with open(os.path.join(model_dir, "meta.json")) as f:
            self.meta = json.load(f)
        self.image_size = self.meta["image_size"]
        self._mean = np.asarray(self.meta["mean"], dtype=np.float32)
        self._std = np.asarray(self.meta["std"], dtype=np.float32)

        if providers is None:
            # Prefer GPU EPs when the JetPack onnxruntime-gpu wheel is installed
            wanted = [
                "TensorrtExecutionProvider",
                "CUDAExecutionProvider",
                "CPUExecutionProvider",
            ]
            available = ort.get_available_providers()
            providers = [p for p in wanted if p in available]

        self._session = ort.InferenceSession(
            os.path.join(model_dir, "image_encoder.onnx"), providers=providers
        )
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
        if now - self._last_capture >= self.heartbeat_seconds:
            return "heartbeat"
        return None

    # ==================== Capture ====================

    def maybe_capture(self, rgb_image, state):
        """Embed + spool the frame if the scene changed or the heartbeat is due.

        Args:
            rgb_image: HWC uint8 numpy array (RGB, no alpha).
            state: JSON-comparable dict of detectNet scene state; any change
                   vs the previous call triggers a capture.

        Returns:
            The trigger ('change'/'heartbeat') if captured, else None.
        """
        trigger = self._should_capture(state)
        self._last_state = state
        if trigger is None:
            return None

        try:
            embedding = self.embed(rgb_image)
            self._write_spool_entry(rgb_image, embedding, state, trigger)
            self._last_capture = time.time()
            return trigger
        except Exception as e:
            logger.error("Frame capture failed: %s", e)
            return None

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

    def _write_spool_entry(self, rgb_image, embedding, state, trigger):
        """Write the {jpg, json} pair; .jpg first so .json marks completeness."""
        from PIL import Image

        self._enforce_cap()

        ts_ms = int(time.time() * 1000)
        base = os.path.join(self.spool_dir, "frame_%d" % ts_ms)

        img = Image.fromarray(rgb_image)
        if img.size[0] > self.thumb_width:
            ratio = float(self.thumb_width) / img.size[0]
            img = img.resize(
                (self.thumb_width, int(img.size[1] * ratio)), Image.BICUBIC
            )
        img.save(base + ".jpg", "JPEG", quality=self.jpeg_quality)

        entry = {
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
