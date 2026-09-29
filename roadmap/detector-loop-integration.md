# Wire the detector layer into the observation loop

- **Status**: Not started
- **Effort**: Small–Medium
- **Dependencies**: detectors ✅, rules ✅, feedback ✅ (all shipped)

## The gap

`smollama/detectors/` and `smollama/rules/` are built, tested (545 tests) and
pushed. **Nothing calls them in the live path.** `grep -rn "detect_all" smollama/`
returns no hits outside those packages and their tests.

The observation loop still does what the evaluation harness proved does not work: it
formats every current reading plus an aggregated history and asks the model to find
something interesting.

```python
# observation_loop.py — still the old path
prompt = OBSERVATION_PROMPT.format(
    current_readings=self._format_current_readings(readings),
    recent_history=self._format_history(history),
    ...
)
```

Measured on the eight golden cases: `qwen2.5:1.5b` scores **0.00 detection** —
silent even on a 6-sigma spike — and `gemma3:1b` scores **0.00 restraint**, flagging
deliberately steady readings. Neither can discriminate. Until the loop is rewired,
the machinery that fixes this is dead code and the live system has the same blind
spots that let a camera writer go unreported for 19 days.

## Goal

Change the loop's question from *"find something in these readings"* to *"describe
this specific deviation"*, and skip the model entirely when there is nothing to
describe.

## Implementation

### 1. Detection replaces scanning

In `_do_generate_observation`, before building any prompt:

```python
series = load_series(config.memory.db_path)
signals = detect_all(series, expected_sources=known_sources(...))
signals = uncovered_signals(signals, rule_store)   # structural pre-filter
```

### 2. Skip the LLM when nothing fired

The largest win, and it needs no model at all. Today every cycle costs 35–104s
regardless of whether anything happened. With detection gating, a quiet cycle costs
**zero** LLM time. Most cycles are quiet.

### 3. Narrate, don't scan

Hand the model 1–3 signals and their `detail` lines — each already self-contained
with its numbers — and ask for observation text. The prompt shrinks from ~11 sources
to a handful of specific findings, which matters because prefill dominated (57s of
qwen's 60s).

Keep `format=<schema>`, `num_predict`, and the task-specific system prompt.

### 4. Record evaluations against rules

Every cycle, for each active rule, `record_evaluation(rule_id, fired=...)`. This is
what makes `apply_maintenance` work: without evaluation counts, auto-mute and
auto-retire never trigger and the lifecycle is inert.

### 5. Maintenance on a slower cadence

Call `apply_maintenance(store, known_sources=...)` once per N cycles, not every one.

## Validation

- A cycle with no signals makes **no** model call (assert on a mock).
- A cycle with the stuck `hcsr04` signal produces an observation naming it.
- Rule evaluation counters increase across cycles and survive restart.
- Re-run `smollama.evals` afterwards: detection should rise sharply, because the
  model is no longer being asked to find the anomaly itself.

## Open questions

- **Do domain observers still apply?** `vision_observation` derives state and builds
  its own prompt. Either it becomes another detector, or the two paths coexist with
  domains claiming their sources first.
- **Who authors rules?** `author_rules` exists but nothing calls it either. Keep it
  manual (dashboard) until detection-driven observations are trusted.
- **Signal-to-observation mapping.** One observation per signal, or one covering
  several? One-per-signal is simpler and matches `maxItems`.
