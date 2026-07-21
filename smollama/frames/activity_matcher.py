"""Zero-shot activity scoring for camera frames/windows.

Categories are plain text prompts in a git-tracked YAML file (see
``config/activity_prompts.yaml``): "classifier v2.3" is a readable, diffable
text file, not a trained model. Multiple phrasings per category are averaged
(prompt ensembling) into one embedding, so adding or editing a category never
touches the edge fleet or the encoders — only the prompt file changes, and it
is picked up on the next score() call without restarting the agent.

Distractor categories (``distractor: true``) absorb background/empty scenes:
scoring is an argmax over real *and* distractor prompts together, so a real
category only "wins" if it beats the best background explanation, not just
the zero vector.

This mirrors ClipTextEncoder's degrade-gracefully contract: missing deps,
missing file, or bad YAML make ``score()`` return None rather than raising,
so a matcher problem never blocks frame ingest.
"""

import hashlib
import logging
from pathlib import Path
from typing import Any

from .text_encoder import ClipTextEncoder

logger = logging.getLogger(__name__)


class ActivityMatcher:
    """Scores an embedding against a hot-reloadable prompt-matrix of categories."""

    def __init__(
        self,
        prompts_path: str | Path,
        text_encoder: ClipTextEncoder,
        default_threshold: float = 0.22,
    ):
        self.prompts_path = Path(prompts_path).expanduser()
        self._text_encoder = text_encoder
        self._default_threshold = default_threshold

        self._mtime: float | None = None
        self._size: int | None = None
        self._prompts_hash: str | None = None
        self._categories: list[tuple[str, bool, float]] = []  # (name, is_distractor, threshold)
        self._matrix = None  # numpy array, K x D, one row per _categories entry
        self._load_error: str | None = None

    @property
    def available(self) -> bool:
        return self.ensure_loaded()

    @property
    def prompts_hash(self) -> str | None:
        return self._prompts_hash

    def ensure_loaded(self) -> bool:
        """Reload the prompt matrix if the file changed. Returns availability."""
        try:
            stat = self.prompts_path.stat()
        except OSError as e:
            if self._load_error != str(e):
                logger.warning(f"Activity prompts file unavailable: {e}")
                self._load_error = str(e)
            self._matrix = None
            return False

        if self._matrix is not None and stat.st_mtime == self._mtime and stat.st_size == self._size:
            return True

        try:
            self._reload(stat.st_mtime, stat.st_size)
            self._load_error = None
            return True
        except Exception as e:
            logger.warning(f"Failed to load activity prompts from {self.prompts_path}: {e}")
            self._load_error = str(e)
            self._matrix = None
            return False

    def _reload(self, mtime: float, size: int) -> None:
        import numpy as np
        import yaml

        raw = self.prompts_path.read_bytes()
        prompts_hash = hashlib.sha256(raw).hexdigest()[:12]
        doc = yaml.safe_load(raw) or {}
        categories = doc.get("categories") or []
        if not categories:
            raise ValueError("prompts file has no categories")

        default_threshold = doc.get("default_threshold", self._default_threshold)

        rows = []
        meta: list[tuple[str, bool, float]] = []
        for cat in categories:
            name = cat["name"]
            phrases = cat.get("prompts") or []
            if not phrases:
                raise ValueError(f"category {name!r} has no prompts")
            is_distractor = bool(cat.get("distractor", False))
            threshold = float(cat.get("threshold", default_threshold))

            vecs = [
                np.asarray(self._text_encoder.embed_floats(phrase), dtype=np.float32)
                for phrase in phrases
            ]
            mean = np.mean(vecs, axis=0)
            norm = float(np.linalg.norm(mean))
            if norm > 0:
                mean = mean / norm
            rows.append(mean)
            meta.append((name, is_distractor, threshold))

        self._matrix = np.stack(rows, axis=0)
        self._categories = meta
        self._mtime = mtime
        self._size = size
        self._prompts_hash = prompts_hash
        logger.info(
            f"Loaded {len(meta)} activity categories from {self.prompts_path} "
            f"(hash={prompts_hash})"
        )

    def score(self, embedding: list[float]) -> dict[str, Any] | None:
        """Score an embedding against every category; argmax decides the match.

        Returns None if the matrix/encoder/prompts file is unavailable.
        Otherwise returns:
            {"scores": {name: score, ...},       # non-distractor categories
             "distractors": {name: score, ...},  # distractor categories
             "top_category": str,                # best-scoring row overall
             "top_score": float,
             "matched": bool,                    # top is real & >= its threshold
             "prompts_hash": str}
        """
        if not self.ensure_loaded():
            return None

        import numpy as np

        vec = np.asarray(embedding, dtype=np.float32)
        norm = float(np.linalg.norm(vec))
        if norm > 0:
            vec = vec / norm

        sims = self._matrix @ vec
        top_idx = int(np.argmax(sims))
        top_name, top_is_distractor, top_threshold = self._categories[top_idx]
        top_score = float(sims[top_idx])
        matched = (not top_is_distractor) and top_score >= top_threshold

        scores = {}
        distractors = {}
        for (name, is_distractor, _threshold), score in zip(self._categories, sims):
            if is_distractor:
                distractors[name] = float(score)
            else:
                scores[name] = float(score)

        return {
            "scores": scores,
            "distractors": distractors,
            "top_category": top_name,
            "top_score": top_score,
            "matched": matched,
            "prompts_hash": self._prompts_hash,
        }
