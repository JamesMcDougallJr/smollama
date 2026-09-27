"""Rule layer — Phase 2 of docs/observation-rules.md.

The LLM authors a predicate's shape and semantics; code fits the number, owns the
lifecycle, and decides the deterministic retirements. No model is involved in this
package.
"""

from .author import AUTHOR_SCHEMA, AuthorConfig, author_rules, build_author_prompt
from .fit import FitError, is_literal, resolve_threshold
from .maintenance import (
    MaintenanceAction,
    MaintenanceConfig,
    apply_maintenance,
    churn_summary,
    uncovered_signals,
)
from .review import (
    REVIEW_SCHEMA,
    ReviewAction,
    ReviewConfig,
    apply_review,
    build_review_prompt,
    nominate_for_review,
)
from .store import STATES, Rule, RuleStore

__all__ = [
    "AUTHOR_SCHEMA",
    "AuthorConfig",
    "author_rules",
    "build_author_prompt",
    "REVIEW_SCHEMA",
    "ReviewAction",
    "ReviewConfig",
    "apply_review",
    "build_review_prompt",
    "nominate_for_review",
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
