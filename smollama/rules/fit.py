"""Threshold fitting: turn a predicate *shape* into a number, from history.

The division of labour this enforces: an LLM proposes what is worth watching and
in which direction; code decides the number. A threshold the model picks is
derived from whatever it happened to see — n=1 — with no access to the
distribution. `config/activity_prompts.yaml` already states the principle for the
frame matcher: "thresholds are starting points, not tuned values ... treat a
threshold change like a unit test."

Specs are re-resolved periodically, so a rule tracks the system rather than
freezing at the moment it was created.

    literal:80          use 80 exactly (allowed, discouraged, flagged in review)
    fit:p99_7d          99th percentile of the window
    fit:p95_24h         95th percentile
    fit:baseline+4mad   median + 4 robust sigma  (for direction "above")
    fit:baseline-4mad   median - 4 robust sigma  (for direction "below")

The window suffix (`_7d`, `_24h`) documents which history the caller should pass
in; this function does not slice — the caller owns the query, so fitting stays a
pure function.
"""

import re

from ..detectors.stats import mad, median

_MAD_TO_SIGMA = 0.6745

_PERCENTILE_RE = re.compile(r"^fit:p(\d{1,2}(?:\.\d+)?)(?:_\w+)?$")
_MAD_RE = re.compile(r"^fit:baseline([+-])(\d+(?:\.\d+)?)mad$")
_LITERAL_RE = re.compile(r"^literal:(-?\d+(?:\.\d+)?)$")


class FitError(ValueError):
    """Raised when a spec is unrecognised, or history is too thin to fit from.

    Deliberately an error rather than a fallback: silently inventing a threshold
    when there is nothing to fit from is precisely the failure this module exists
    to prevent.
    """


def _percentile(values: list[float], pct: float) -> float:
    """Linear-interpolated percentile. No numpy dependency for one call."""
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (pct / 100.0) * (len(ordered) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(ordered) - 1)
    frac = rank - lo
    return ordered[lo] * (1 - frac) + ordered[hi] * frac


def resolve_threshold(spec: str, history) -> float:
    """Resolve a threshold spec against a list of historical numeric values."""
    if not spec:
        raise FitError("empty threshold spec")

    literal = _LITERAL_RE.match(spec)
    if literal:
        return float(literal.group(1))

    values = [
        float(v) for v in (history or [])
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    ]
    if not values:
        raise FitError(f"cannot fit {spec!r}: no numeric history supplied")

    pct = _PERCENTILE_RE.match(spec)
    if pct:
        return _percentile(values, float(pct.group(1)))

    mad_spec = _MAD_RE.match(spec)
    if mad_spec:
        sign, multiple = mad_spec.group(1), float(mad_spec.group(2))
        centre = median(values)
        spread = mad(values) or 0.0
        sigma = spread / _MAD_TO_SIGMA
        # A constant history has zero spread; the threshold collapses onto the
        # median, which is correct — there is no observed variation to allow for.
        return centre + (sigma * multiple if sign == "+" else -sigma * multiple)

    raise FitError(
        f"unrecognised threshold spec {spec!r}; expected literal:<n>, "
        f"fit:pNN[_window], or fit:baseline±Nmad"
    )


def is_literal(spec: str) -> bool:
    """Literal thresholds are allowed but worth surfacing — they don't re-fit, so
    they drift out of date silently as the system changes."""
    return bool(spec and _LITERAL_RE.match(spec))
