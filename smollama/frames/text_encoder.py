"""CLIP text-query encoder for frame search.

Runs the exported CLIP *text* tower as ONNX on CPU (fast — it's tiny), so
queries land in the same embedding space as the frames embedded on the edge.
The master node needs no torch: only ``onnxruntime``, ``numpy`` and ``regex``,
all optional — if any is missing, FrameStore falls back to label text search.
"""

import logging
import struct
from pathlib import Path

logger = logging.getLogger(__name__)


class ClipTextEncoder:
    """Encodes text queries into the CLIP embedding space via ONNX."""

    def __init__(self, model_path: str, tokenizer_path: str, context_length: int = 77):
        self.model_path = Path(model_path).expanduser()
        self.tokenizer_path = Path(tokenizer_path).expanduser()
        self.context_length = context_length
        self._session = None
        self._tokenizer = None
        self._input_name: str | None = None
        self._input_is_int32 = False
        self._dimension: int | None = None
        self._load_error: str | None = None

    def _ensure_loaded(self) -> None:
        if self._session is not None:
            return
        if self._load_error is not None:
            raise RuntimeError(self._load_error)
        try:
            import onnxruntime as ort

            from .tokenizer import SimpleTokenizer

            if not self.model_path.exists():
                raise FileNotFoundError(f"CLIP text model not found: {self.model_path}")
            if not self.tokenizer_path.exists():
                raise FileNotFoundError(f"CLIP tokenizer vocab not found: {self.tokenizer_path}")

            self._session = ort.InferenceSession(
                str(self.model_path), providers=["CPUExecutionProvider"]
            )
            self._tokenizer = SimpleTokenizer(str(self.tokenizer_path), self.context_length)
            inp = self._session.get_inputs()[0]
            self._input_name = inp.name
            self._input_is_int32 = "int32" in (inp.type or "")
        except Exception as e:
            self._session = None
            self._load_error = f"{type(e).__name__}: {e}"
            raise

    @property
    def available(self) -> bool:
        """True if the encoder can run (deps installed, model files present)."""
        try:
            self._ensure_loaded()
            return True
        except Exception as e:
            logger.warning(f"CLIP text encoder unavailable: {e}")
            return False

    @property
    def dimension(self) -> int:
        self._ensure_loaded()
        if self._dimension is None:
            self._dimension = len(self.embed_floats("probe"))
        return self._dimension

    def embed_floats(self, text: str) -> list[float]:
        """Embed text, returning an L2-normalized float list."""
        import numpy as np

        self._ensure_loaded()
        dtype = np.int32 if self._input_is_int32 else np.int64
        ids = np.asarray([self._tokenizer(text)], dtype=dtype)
        (out,) = self._session.run(None, {self._input_name: ids})
        vec = np.asarray(out, dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(vec))
        if norm > 0:
            vec = vec / norm
        return vec.tolist()

    def embed(self, text: str) -> bytes:
        """Embed text as packed little-endian float32 bytes (sqlite-vec format)."""
        floats = self.embed_floats(text)
        return struct.pack(f"<{len(floats)}f", *floats)
