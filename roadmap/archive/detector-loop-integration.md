# Wire the detector layer into the observation loop

- **Status**: ✅ Complete (2026-09-28)
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


---

## What shipped

All five steps, plus two defects the validation run exposed.

`ObservationLoop` now runs `load_series` → `detect_all` → `uncovered_signals` before
building any prompt, records an evaluation against every active rule each cycle, and
calls `apply_maintenance` once per `observation_maintenance_every` cycles. Config:
`observation_use_detectors` (default on), `observation_max_signals`,
`observation_maintenance_every`, `detector_window_hours`. The agent owns a `RuleStore`
of its own, on the same `memory.db` the dashboard's `/rules` page reads.

`SIGNAL_OBSERVATION_PROMPT` replaces `OBSERVATION_PROMPT` on that path. The old
prompt is kept and reachable via `observation_use_detectors: false`.

### Open questions, resolved

- **Domain observers coexist.** `vision_observation` derives its own state and builds
  its own prompt, so domains claim their sources first and the detector pass covers
  the remainder. Rule evaluation still spans every signal, including claimed ones — a
  rule is being evaluated regardless of which pass narrates.
- **Rule authoring stays manual**, as planned. `author_rules` is still uncalled.
- **One observation per signal**, matching the schema's `maxItems`.

### Two defects the first validation run found

Both were in the direction the plan did not anticipate — the model's *output*, not
the detection.

1. **Invented source identifiers.** `qwen2.5:1.5b` produced correct observation text
   with `related_sources: ["system:cpu_temp"]` on a case that never mentioned it, and
   `["system:hcsr04"]` for `hcsr04:distance`. A 1.5B model does not reliably copy an
   identifier, and that field is what later keep/dismiss feedback is attributed to.
   Fixed in code (`_sanitize_sources`): the detector already knows which source fired,
   so the model is told to leave the field empty and the loop fills it.
2. **Detector tag leaked into prose.** A `[stale/absent]` prefix in the prompt came
   back inside the stored observation text. `Signal.detail` is self-contained by
   design, so the tag was removed from the prompt entirely.

A third, left to the gate: the model computed `12.6` from a 63.0 reading and a 50.4
baseline. The arithmetic was right, but a derived number cannot be checked against
the readings, so the prompt now forbids it and `no_invented_numbers` catches
regressions.

### Measured

`smollama.evals` gained `--path {detectors,scan}` so both paths can be run against
the same golden cases. On the detector path `detection` is deliberately not reported
— code decided it — and `echo` takes its place, measuring whether the model handed
the signal back verbatim.

| `qwen2.5:1.5b` | scan | detectors |
|---|---|---|
| gate pass | 100% | 100% |
| detection | **0.00** | n/a — code's, and it caught all 5 |
| echo | n/a | 0.20 |
| model calls | 8 of 8 | 5 of 8 |
| total wall | 165.2s | 117.2s |

The wall-time gap understates the production effect: 3 of 8 golden cases are quiet,
where in production nearly every cycle is.

581 tests pass (545 before).

### Still open

- `echo` of 0.20 is one case (`stuck_sensor`) returned verbatim. Harmless — the text
  was accurate — but it shows the model sometimes adds nothing, which is worth
  knowing before crediting it for the observation.
- The judge has not been run against a detector-path run yet, so `insight` and
  `grounding` on the new task are unmeasured.
- Nothing has been observed over a full day of live cycles.
