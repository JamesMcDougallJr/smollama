"""Deterministic detector layer — Phase 1 of docs/observation-rules.md.

Code detects; the LLM describes. No model is involved in this package.
"""

from .core import (
    DETECTORS,
    DetectorConfig,
    Sample,
    Signal,
    detect_all,
    detect_envelope,
    detect_flatline,
    detect_level_shift,
    detect_stale,
    detect_trend,
)

__all__ = [
    "DETECTORS",
    "DetectorConfig",
    "Sample",
    "Signal",
    "detect_all",
    "detect_envelope",
    "detect_flatline",
    "detect_level_shift",
    "detect_stale",
    "detect_trend",
]
