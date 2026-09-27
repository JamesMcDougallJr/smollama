# Design: Derived Rules and Closed-Loop Observation

**Status: Phases 1–6 implemented. Phase 7 (live actuation) deliberately not.**
`smollama/detectors/`, `smollama/rules/`, `smollama/actions/`, plus the `/rules`
page and the keep/dismiss control on `/observations`. The design was written before any code so
the shape could be argued with first; see *What building phases 1–2 changed about
this design* near the end for where it turned out to be wrong.

## The problem

The observation loop asks an LLM to scan every reading and find what matters.
Measured on a Pi 5, that does not work:

- A cycle cost 104s (334s before capping generation) and ran every ~24 min.
- Output was unusable: 93 of 341 stored observations were JSON fragments or
  essay preambles from a parse-failure fallback.
- `qwen2.5:1.5b` under plain `format="json"` was **always silent** — it reported
  nothing at 82 °C CPU and 97.5% memory.
- Meanwhile the genuinely notable events went unreported for weeks: the Jetson
  writer went silent on Sep 7 (19 days), `hcsr04` was stuck at exactly 0.0, swap
  sat at 4.9 GB, disk at 99%. Every one was trivially detectable. None were
  detected.

Unsupervised discovery is the wrong job for a small local model. The signals that
mattered were arithmetic.

## Reframe

Don't ask what is *important* (needs labels). Ask what is *different* (needs only
history). **Each source is its own baseline.**

- "82 °C is bad" — needs domain knowledge.
- "82 °C is 6 MADs above this source's own 24h baseline" — needs no labels.

The LLM's durable contribution is not the threshold. It is **deciding which of
the infinite computable signals is worth watching at all**, and later, whether
watching it still makes sense. Let it name the signal; let the data set the
number.

## Three tiers

```
Tier 1  detectors   deterministic signals over sliding windows   µs, every cycle
Tier 2  rules       LLM authors semantics, code fits thresholds   LLM, bounded
Tier 3  actions     act, record outcome, revise the rule          gated, opt-in
```

Each tier is independently useful and independently shippable. Tier 1 alone
would have caught every failure listed above.

---

## Tier 1 — Detectors

Pure functions over reading history. No LLM, no labels, no state to persist —
windowed SQL over `readings_log` (queries measure 0.07–0.36 ms at current scale,
and unlike EWMA there is nothing to checkpoint across the frequent restarts).

| Detector | Catches | Needs |
|---|---|---|
| `flatline` | stuck sensor (variance ≈ 0 over N) | one window |
| `stale` | dead producer (no data in N min) | timestamps |
| `level_shift` | step change (robust z vs baseline) | two windows |
| `trend` | drift (slope over window) | one window |
| `envelope` | novelty (outside historical min/max) | baseline |

Two windows, not one. A single 60-min aggregate is a *description* — `min=0.0,
max=0.0, avg=0.0` is only interesting beside "and it was 47 cm yesterday". The
absence of a reference window is the structural reason models go silent on the
current prompt.

Use **median/MAD, not mean/std** — std is poisoned by the very outlier being
detected.

Multi-scale (5 min / 1 h / 24 h) from the same code catches spikes, drift and
diurnal effects separately.

**Known pitfall:** a flat 24h baseline flags normal diurnal variation (CPU temp
climbing each afternoon) as anomalous. Either compare against same-time-of-day
history or accept a tuning period. Do not pretend this is solved at design time.

Detectors emit **candidate signals**, not observations:

```
{source, detector, window, value, baseline, score, direction, first_seen}
```

---

## Tier 2 — Rules

A rule is a persisted, named, deterministic predicate over a detector's output.

### Division of labour

The LLM proposes the predicate's **shape and semantics**. Code fits the
**number**:

```json
{"source": "system:cpu_temp", "detector": "sustained_above",
 "sustain_seconds": 300, "threshold": "fit:p99_7d",
 "rationale": "thermal throttling risk"}
```

`fit:p99_7d` is resolved from `readings_log` and **re-fit periodically**, so a
rule tracks the system instead of freezing the moment it was born. A literal
threshold from a single observation is n=1: the model has no access to the
distribution. `config/activity_prompts.yaml` already states this principle —
*"thresholds are starting points, not tuned values … treat a threshold change
like a unit test"* — and a threshold guessed from one window violates it.

Supported fits: `fit:p99_7d`, `fit:p95_24h`, `fit:baseline+4mad`,
`literal:<n>` (allowed, discouraged, flagged in review).

### Identity and dedup

Key on `(full_id, detector, direction)` — one rule per source per detector kind.

`cpu_temp > 80` and `cpu_temp >= 79.5` are semantically one rule and textually
two. String dedup yields near-duplicates; LLM dedup costs a call every cycle and
is nondeterministic. A proposal for an existing key is an **update**, which must
justify itself against the incumbent.

### Never rely on a negative instruction

"Don't create a rule if one exists" is an instruction the model may ignore. We
measured exactly this failure: "at most 3 observations" in the prompt was
ignored; `maxItems: 3` in the schema was obeyed.

So: **code excludes already-covered signals from the prompt entirely.** The model
cannot duplicate what it never sees. Don't ask — prevent.

### Lifecycle

```
proposed ──promote──> active ──┬── retune ──> active (new threshold)
    │                          ├── mute ────> muted ──> active
    └── reject                 └── retire ──> retired (resurrectable)
```

New rules land `proposed`, surface on the dashboard, auto-promote after N clean
evaluations or on a click. An LLM-invented rule encodes an assumption about
normal that no human approved; this keeps a bad one from becoming load-bearing.

### Retirement is the highest-risk operation

Asymmetric: **a bad retained rule makes noise you notice; a wrongly retired rule
makes silence you don't.** That is exactly the Sep 7 failure. Bias accordingly.

Most retirement never reaches the LLM:

| Condition | Action | Decided by |
|---|---|---|
| Rule's source hasn't reported in N days | park | code |
| 0 fires in 30 days, signal never near threshold | retire (dead) | code |
| Fires in >X% of evaluations | mute (describes normal) | code |
| Duplicate identity key | supersede | code |

The LLM adjudicates only the ambiguous middle — rules that fire *sometimes* and
whose **meaning** is now questionable. Deterministic triage nominates ≤5
candidates per review; the LLM never sees the whole table. Reviewing 200 rules
per cycle recreates the prompt bloat this design exists to remove (prefill is
already 57s of qwen's 60s).

Decision is a 4-way enum, not keep/retire:

```
keep | retune | mute | retire
```

`retune` is usually the right answer — the rule is sound and the number drifted.
It delegates straight back to the `fit:` resolver, so the LLM still never picks a
number.

Guardrails:

- Retirement is a **state transition, not a DELETE**. Reason + timestamp
  recorded; resurrectable.
- A reason is **required**. If it can't articulate why, don't act. Log every
  reason — that log is the only window into whether the model's judgment is good.
- **Minimum age** before review eligibility, or create/retire oscillation burns
  the budget.
- **Higher evidence bar** to retire a rule that has ever fired legitimately.
- Decision constrained by schema `enum`; review budget bounded by config.

### What the LLM sees per candidate

Compact and factual: predicate, age, `fired_count`, `evaluations`, fire rate,
`last_fired`, whether the source still reports, a few recent values vs
threshold. Currently-firing rules only — never full history, which both bloats
the prompt and biases the model toward validating rather than questioning.

### Storage

A `rules` table in `memory.db` (not git-tracked YAML — an LLM mutating a
versioned file is awkward), with a dashboard view and YAML export for
inspection.

---

## Tier 3 — Closed-loop action

The goal: detect → act → observe outcome → revise the rule. *Humidity rises →
adjust thermostat → confirm it worked → modify the rule.*

**This is a different risk class.** Monitoring being wrong produces noise.
Actuation being wrong produces a cold house, a short-cycled compressor, a frozen
pipe. Everything below is a safety requirement, not a nicety.

### The door is already open

`agent.py:209` auto-registers every `WritePlugin`'s tools. Enabling `led` (or any
actuator) together with `observation_use_tools: true` — a knob that already
exists — lets the observation loop actuate hardware today. The envelope below is
not future work.

### Action is not resolution

The proposed example — *alert → act → remove the alert* — contains a trap worth
naming. **Removing the rule after acting destroys the feedback mechanism.** If
the rule is gone you cannot detect that the action failed, or that the condition
returned.

Correct behaviour: **suppress the rule for a cooldown**, let the action take
effect, then re-evaluate. Retire only when the underlying condition stops being
meaningful (a dehumidifier was installed permanently), never merely because
something was done about it. Conflating "I acted" with "no longer needed" is how
closed loops go blind.

### Safety envelope — enforced in code, never in the prompt

- **Allow-list** of actuators. Nothing actuates unless explicitly listed.
- **Per-actuator bounds**: absolute min/max, plus max delta per action.
- **Rate limit**: max N actions per target per hour.
- **Deadband / hysteresis**: required, or the loop hunts. This is why thermostats
  have hysteresis; an LLM without one oscillates.
- **Cooldown** after any action before the same rule may act again.
- **Global kill switch** in config, default off.
- **`dry_run` mode** that records intended actions without executing. The single
  most valuable de-risking step: watch what it *would* do for a week first.

Prose constraints get ignored; structural ones get obeyed. This belongs in the
tool layer.

### Outcome record

Learning requires an explicit causal record, not "observe later":

```
{rule_id, action, target, value_before, value_after,
 observation_window, outcome_metric, verdict, confounders_noted}
```

**Attribution is weak.** If humidity falls after a setpoint change, it may have
been the thermostat, the weather, or an open window. You cannot A/B test a house.
Require a minimum observation window, and treat the correlation as weak evidence
rather than learned causation. Being explicit about this is the difference
between a feedback loop and a superstition generator.

---

## Ground truth

There is none. Nothing records whether an alert was ever actionable, so "does
this rule still make sense?" is partly unanswerable — the model optimises a
proxy.

Cheapest fix by far: a **keep/dismiss control** on the dashboard beside each
observation. One click yields a real label, and those labels make retirement
evidential instead of speculative. Build this *before* trusting LLM retirement
much; it is a small amount of UI for a large amount of signal.

## Meta-metrics

Track rule **create and retire rates**. High churn means the LLM is guessing or
thresholds are being badly fit — a rule system that detects its own instability
using the same detector layer. Free, and the earliest warning that Tier 2 is
misbehaving.

Also worth watching: fraction of cycles that invoke the LLM at all (should be
low), and fraction of observations whose rule later gets muted.

## Phased plan

Each phase is shippable and gated on evidence from the previous one.

| Phase | Scope | Validation gate |
|---|---|---|
| 1 | Detectors + candidate signals, no LLM | **DONE** — `smollama/detectors/`. Fires on the real stuck `hcsr04` (486 readings at 0.0); staleness validated by replaying real data with an advanced clock. |
| 2 | Rules table, `fit:` resolver, deterministic retirement, dashboard view | **DONE** — `smollama/rules/` + `/rules`. Rules survive restart; auto-mute fires on a deliberately noisy rule; proposed rules are promotable by hand. |
| 3 | Dashboard keep/dismiss feedback | **DONE** — `observation_feedback` in memory.db + a control on `/observations`. `feedback_summary()` returns `keep_rate: None` rather than 0.0 with no labels, so an absent rate is never mistaken for evidence. |
| 4 | LLM rule authoring, bounded, `proposed` only | **DONE** — `rules/author.py`. `threshold_spec` is a closed enum of `fit:` specs with no `literal:` option; sources are validated against the signals the model was shown; authored rules land `proposed` and cover nothing. |
| 5 | LLM review/retire on nominated candidates | **DONE** — `rules/review.py`. Deterministic triage nominates ≤5; four-way keep/retune/mute/retire; `retune` delegates to the `fit:` resolver; retiring a rule that has ever fired is refused outright. |
| 6 | Tier 3 in `dry_run` only | **DONE** — `smollama/actions/`. Nothing executes: `propose_action` evaluates against the envelope and returns a decision, and no actuator is wired to it. Defaults `enabled=False`, `dry_run=True`. |
| 7 | Tier 3 live, one actuator, tight bounds | **NOT DONE, deliberately.** See below. |

Phase 1 alone resolves the stated problem. Phases 4+ are optional and should be
justified by Phase 1–3 evidence, not assumed.

### Why Phase 7 is not implemented

Everything through Phase 6 is reversible: a bad detector makes noise, a bad rule is
muted, a bad action proposal is logged and discarded. Phase 7 is the first step
where being wrong moves hardware.

Two reasons to stop here rather than finish the list:

1. **The models available on this node cannot discriminate.** The evaluation harness
   measured `qwen2.5:1.5b` at 0.00 detection (silent on a 6-sigma spike) and
   `gemma3:1b` at 0.00 restraint (flagging deliberately steady readings). Wiring
   either to an actuator would act on judgement already measured as unreliable.
2. **The phase's own gate requires a human.** "Manual kill switch tested first" is
   not something to self-certify, and a week of `dry_run` logs has to actually be
   inspected by someone before the envelope's bounds can be trusted.

What exists is the lock, not the key: `ActionEnvelope` defaults to `enabled=False`
and `dry_run=True`, and no actuator is connected to `propose_action`. This matters
because `agent.py` already auto-registers every WritePlugin's tools, so enabling an
actuator alongside `observation_use_tools: true` is all it would take.

### What building phases 1–2 changed about this design

Four things the plan got wrong, corrected by failing tests rather than by review:

- **A window must be a sample count, not a duration.** `level_shift` first used a
  300-second recent window, which assumed 30-second cadence; this system logs
  roughly every 20 minutes, so it would have contained zero samples and never
  fired in production.
- **A constant baseline breaks the z-score.** MAD is 0, so a robust z is undefined
  and the least ambiguous shift there is — a pinned sensor starting to move — was
  silently missed. It needs its own branch, scored moderately so a `0 -> 0.1` move
  cannot outrank a 19-day silence.
- **Not every detector has a threshold.** `flatline` and `stale` are structural
  (zero variance, or no data); only `level_shift`, `trend`, and `envelope` compare
  against a number. `threshold_spec` is therefore optional.
- **Auto-promote needs a fire-rate check.** Without one, a proposed rule that fires
  constantly is promoted and then muted in the same pass — two state changes for
  one decision, inflating the very churn metric meant to detect instability.

From phases 3–6:

- **A rate computed from no labels is not zero.** `feedback_summary()` returns
  `keep_rate: None` when a source has never been rated, because a fabricated 0.0
  reads downstream as "nobody finds this useful" rather than "we don't know".
- **Guards must re-read state, not trust what they were handed.** The retire guard
  originally checked `fired_count` on the candidate object passed into review, which
  could be stale — a rule that had fired since nomination would have been retired on
  a zero. It now re-reads from the store.
- **SQLite connections need `check_same_thread=False` here.** The dashboard reads
  these stores from FastAPI's thread pool while the agent writes from the event loop.
  Only surfaced by actually loading the page.
- **`request.form()` pulls in python-multipart.** Dashboard verbs therefore live in
  the URL path (`/api/rules/{id}/promote`) rather than a form field, which avoids the
  dependency and is better REST anyway.

Also confirmed: `known_sources` is bounded by `readings_log` retention
(`readings_max_age_days`, 7 days), so a producer dead longer than that is pruned
and cannot be detected as stale at all. The 19-day case needs a longer-lived
registry than the readings table provides.

## Open questions

- Diurnal baselines: same-time-of-day comparison, or accept tuning?
- Does rule authoring need the capable-model tier, or is a 1.5B model adequate
  once it only proposes shapes and never numbers? **Untested.** Given it couldn't
  reliably pick a schema enum today, assume not until measured.
- Should rules be per-node or cluster-wide? A threshold fit on the master's CPU
  temp is meaningless for the Jetson's.
- Cross-source rules ("humidity high *and* window open") — worth the complexity,
  or does single-source cover the real cases?
