"""Rule layer — Phase 2 of docs/observation-rules.md.

The LLM authors a predicate's shape and semantics; code fits the number, owns the
lifecycle, and decides the deterministic retirements. No model is involved in this
package.
"""

from .fit import FitError, is_literal, resolve_threshold
from .maintenance import (
    MaintenanceAction,
    MaintenanceConfig,
    apply_maintenance,
    churn_summary,
    uncovered_signals,
)
from .store import STATES, Rule, RuleStore

__all__ = [
    "FitError",
    "is_literal",
    "resolve_threshold",
    "MaintenanceAction",
    "MaintenanceConfig",
    "apply_maintenance",
    "churn_summary",
    "uncovered_signals",
    "STATES",
    "Rule",
    "RuleStore",
]
