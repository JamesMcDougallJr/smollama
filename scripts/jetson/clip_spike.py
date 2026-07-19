#!/usr/bin/env python3
"""Phase-0 spike: benchmark the exported CLIP image encoder on the Jetson Nano.

Run under the system python3 (3.6) after copying image_encoder.onnx, meta.json
and reference.json from the desktop export (see export_clip.py):

    python3 clip_spike.py ~/clip-export --runs 10 --parity

Reports which onnxruntime provider actually ran (TensorRT / CUDA / CPU),
per-frame latency, process RSS, and — with --parity — cosine similarity
against the desktop's reference embedding of the same synthetic test image
(anything ≥ 0.99 means the Nano and master live in the same embedding space).

This is the go/no-go gate for the frame-search plan: if latency and RAM are
acceptable alongside detectNet, wire clip_frames.py into jetson_infer.py.
"""

import argparse
import json
import os
import resource
import time


def build_test_image(size):
    """Must match export_clip.py's build_test_image exactly."""
    import numpy as np

    y = np.linspace(0, 255, size, dtype=np.float32)
    x = np.linspace(0, 255, size, dtype=np.float32)
    xx, yy = np.meshgrid(x, y)
    img = np.stack([xx, yy, (xx + yy) / 2], axis=-1)
    return img.astype(np.uint8)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("model_dir", help="directory with image_encoder.onnx + meta.json")
    parser.add_argument("--runs", type=int, default=10, help="timed inference runs")
    parser.add_argument("--image", help="optional JPEG/PNG to embed instead of the synthetic image")
    parser.add_argument("--parity", action="store_true",
                        help="compare against reference.json from the export")
    args = parser.parse_args()

    import numpy as np

    from clip_frames import FrameEmbedder

    model_dir = os.path.expanduser(args.model_dir)
    spool_dir = "/tmp/clip_spike_spool"

    t0 = time.time()
    embedder = FrameEmbedder(model_dir=model_dir, spool_dir=spool_dir)
    load_s = time.time() - t0
    print("Model:      %s (%d-d)" % (embedder.meta.get("model"), embedder.meta.get("dim")))
    print("Providers:  %s" % embedder._session.get_providers())
    print("Load time:  %.1fs" % load_s)

    if args.image:
        from PIL import Image

        rgb = np.asarray(Image.open(args.image).convert("RGB"))
    else:
        rgb = build_test_image(embedder.image_size)

    # Warmup (first TRT/CUDA run compiles kernels and is not representative)
    embedding = embedder.embed(rgb)

    times = []
    for _ in range(args.runs):
        start = time.time()
        embedding = embedder.embed(rgb)
        times.append(time.time() - start)

    times.sort()
    print("Latency:    median %.0fms  min %.0fms  max %.0fms  (%d runs)"
          % (times[len(times) // 2] * 1000, times[0] * 1000, times[-1] * 1000, args.runs))

    rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    print("Peak RSS:   %.0f MB" % rss_mb)

    if args.parity:
        ref_path = os.path.join(model_dir, "reference.json")
        if args.image:
            print("Parity:     skipped (--image given; reference is for the synthetic image)")
        elif not os.path.exists(ref_path):
            print("Parity:     reference.json not found — copy it from the export dir")
        else:
            with open(ref_path) as f:
                ref = np.asarray(json.load(f)["embedding"], dtype=np.float32)
            cos = float(np.dot(np.asarray(embedding, dtype=np.float32), ref))
            verdict = "OK" if cos >= 0.99 else "FAIL — do not deploy, embedding spaces diverge"
            print("Parity:     cosine %.6f vs desktop reference  [%s]" % (cos, verdict))


if __name__ == "__main__":
    main()
