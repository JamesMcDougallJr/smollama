#!/usr/bin/env python3
"""Export CLIP image + text encoders to ONNX for smollama frame search.

Run on a desktop (NOT the Nano) with modern Python:

    uv run --with torch --with open_clip_torch --with onnx \
        python scripts/jetson/export_clip.py --out ~/clip-export

Defaults to MobileCLIP-S1 (512-d, edge-sized, available directly in open_clip).
The TRT-8.2-safe fallback is the classic ResNet CLIP:

    ... export_clip.py --model RN50 --pretrained openai --out ~/clip-export-rn50

Produces in --out:
    image_encoder.onnx            → scp to the Jetson (used by clip_frames.py)
    text_encoder.onnx             → stays on the master (frames.clip_text_model)
    bpe_simple_vocab_16e6.txt.gz  → stays on the master (frames.clip_tokenizer)
    meta.json                     → preprocessing constants for both sides
    reference.json                → deterministic test-image embedding, used by
                                    clip_spike.py to verify Nano↔desktop parity

Both exported graphs output L2-normalized embeddings (opset 13, TensorRT 8.2
compatible), so cosine similarity is a plain dot product everywhere.
"""

import argparse
import json
import shutil
from pathlib import Path


def build_test_image(size: int):
    """Deterministic RGB gradient test image, identical on every machine."""
    import numpy as np

    y = np.linspace(0, 255, size, dtype=np.float32)
    x = np.linspace(0, 255, size, dtype=np.float32)
    xx, yy = np.meshgrid(x, y)
    img = np.stack([xx, yy, (xx + yy) / 2], axis=-1)
    return img.astype(np.uint8)


def preprocess(img, size: int, mean, std):
    """Match clip_frames.py's preprocessing: resize+crop already done, normalize to NCHW."""
    import numpy as np

    arr = img.astype(np.float32) / 255.0
    arr = (arr - np.asarray(mean, dtype=np.float32)) / np.asarray(std, dtype=np.float32)
    return arr.transpose(2, 0, 1)[None, ...]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default="MobileCLIP-S1", help="open_clip model name")
    parser.add_argument("--pretrained", default="datacompdr", help="open_clip pretrained tag")
    parser.add_argument("--out", required=True, help="output directory")
    parser.add_argument("--opset", type=int, default=13, help="ONNX opset (13 = TRT 8.2 safe)")
    args = parser.parse_args()

    import numpy as np
    import open_clip
    import torch

    out_dir = Path(args.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loading {args.model} ({args.pretrained})...")
    model, _, _ = open_clip.create_model_and_transforms(
        args.model, pretrained=args.pretrained
    )
    model.eval()

    image_size = model.visual.image_size
    if isinstance(image_size, (tuple, list)):
        image_size = image_size[0]
    mean = list(getattr(model.visual, "image_mean", (0.48145466, 0.4578275, 0.40821073)))
    std = list(getattr(model.visual, "image_std", (0.26862954, 0.26130258, 0.27577711)))
    context_length = int(getattr(model, "context_length", 77))

    class ImageEncoder(torch.nn.Module):
        def __init__(self, clip):
            super().__init__()
            self.clip = clip

        def forward(self, image):
            feats = self.clip.encode_image(image)
            return feats / feats.norm(dim=-1, keepdim=True)

    class TextEncoder(torch.nn.Module):
        def __init__(self, clip):
            super().__init__()
            self.clip = clip

        def forward(self, tokens):
            feats = self.clip.encode_text(tokens)
            return feats / feats.norm(dim=-1, keepdim=True)

    image_input = torch.zeros(1, 3, image_size, image_size)
    text_input = torch.zeros(1, context_length, dtype=torch.int64)

    with torch.no_grad():
        dim = int(ImageEncoder(model)(image_input).shape[-1])

    print(f"Exporting image encoder ({image_size}px → {dim}-d, opset {args.opset})...")
    torch.onnx.export(
        ImageEncoder(model),
        image_input,
        str(out_dir / "image_encoder.onnx"),
        input_names=["image"],
        output_names=["embedding"],
        opset_version=args.opset,
        dynamic_axes={"image": {0: "batch"}, "embedding": {0: "batch"}},
    )

    print("Exporting text encoder...")
    torch.onnx.export(
        TextEncoder(model),
        text_input,
        str(out_dir / "text_encoder.onnx"),
        input_names=["tokens"],
        output_names=["embedding"],
        opset_version=args.opset,
        dynamic_axes={"tokens": {0: "batch"}, "embedding": {0: "batch"}},
    )

    # The master's vendored tokenizer needs CLIP's BPE vocab; open_clip bundles it
    vocab_src = Path(open_clip.__file__).parent / "bpe_simple_vocab_16e6.txt.gz"
    shutil.copy2(vocab_src, out_dir / "bpe_simple_vocab_16e6.txt.gz")

    meta = {
        "model": args.model,
        "pretrained": args.pretrained,
        "dim": dim,
        "image_size": image_size,
        "mean": mean,
        "std": std,
        "context_length": context_length,
        "opset": args.opset,
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2))

    # Reference embedding for the Nano parity check (clip_spike.py --parity)
    test_img = build_test_image(image_size)
    with torch.no_grad():
        ref = ImageEncoder(model)(torch.from_numpy(preprocess(test_img, image_size, mean, std)))
    ref_floats = ref.numpy().reshape(-1).astype(float).tolist()
    (out_dir / "reference.json").write_text(json.dumps({"embedding": ref_floats}))

    # Sanity: run both graphs under onnxruntime and compare to torch
    try:
        import onnxruntime as ort

        sess = ort.InferenceSession(str(out_dir / "image_encoder.onnx"),
                                    providers=["CPUExecutionProvider"])
        (out_ort,) = sess.run(None, {"image": preprocess(test_img, image_size, mean, std)})
        cos = float(np.dot(out_ort.reshape(-1), np.asarray(ref_floats, dtype=np.float32)))
        print(f"ONNX↔torch image parity (cosine): {cos:.6f}")
        if cos < 0.999:
            print("WARNING: parity below 0.999 — inspect the export before deploying")
    except ImportError:
        print("onnxruntime not installed — skipping local parity check")

    print(f"\nDone. Files in {out_dir}:")
    for f in sorted(out_dir.iterdir()):
        print(f"  {f.name}  ({f.stat().st_size / 1e6:.1f} MB)")
    print(
        "\nNext steps:\n"
        f"  scp {out_dir}/image_encoder.onnx {out_dir}/meta.json {out_dir}/reference.json nano:~/clip-export/\n"
        "  # on the Nano:  python3 clip_spike.py ~/clip-export --parity\n"
        "  # on the master: set frames.clip_text_model / frames.clip_tokenizer in config.yaml"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
