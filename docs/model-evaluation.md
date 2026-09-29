# Model Evaluation

How to say objectively that a new model is better than the one in production.

## Two different things, deliberately separated

Conflating these is the common mistake — they have opposite requirements.

| | `pytest` | eval harness |
|---|---|---|
| Subject | our code | the model |
| Determinism | required | impossible |
| Runs | every commit, CI gate | on demand, when swapping models |
| Cost | free, ~3s | ~30 min/model on the Pi, plus judge tokens |
| Output | pass/fail | scores, compared across runs |

Model quality can never be a unit test — a flaky assertion on a non-deterministic
generator is worse than no assertion. Everything model-facing lives in the
harness and is invoked explicitly.

## Scoring in two stages

**Deterministic checks first; the LLM judge only for what code cannot decide.**
Same principle as the observation architecture: code decides, the LLM narrates.

### Stage 1 — gates and metrics (free, deterministic, no judge)

Gates. Any failure scores the case **0** — no judge tokens spent on output that
doesn't parse:

| Gate | Check |
|---|---|
| `schema` | Response validates against the observation schema |
| `max_items` | ≤ `observation_max_items` |
| `max_chars` | Each text ≤ `observation_max_chars` |
| `enum` | `type` ∈ {pattern, anomaly, status} |
| `sources_exist` | Every `related_sources` entry appears in the input |
| `no_invented_numbers` | Every number in the text appears in the input |

`no_invented_numbers` is the hallucination check and the reason the golden cases
carry their input numbers as data — a model that reports "CPU at 91°C" when the
input said 82°C fails deterministically, with no judge needed.

Metrics, scored against each case's known answer:

| Metric | Meaning |
|---|---|
| `detection` | Flagged the source the case plants (recall). **Scan path only** |
| `echo` | Handed the signal line back unchanged. **Detector path only**; 0.00 is good |
| `restraint` | Emitted nothing on a deliberately-normal case (precision) |
| `wall_s`, `eval_tokens`, `resident_mb` | Cost |

**`restraint` is the metric that catches the failure mode a latency benchmark
misses.** `qwen2.5:1.5b` under plain `format="json"` scored perfectly on speed
and validity while reporting nothing at 82 °C CPU and 97.5 % memory. Without
paired normal/anomalous cases, that model looks excellent.

### Stage 2 — judge (only for the genuinely subjective)

Anchored 0–3 per dimension. Anchors matter more than the scale: "rate quality
1–5" produces noise, while a definition per point produces agreement.

| Dim | 0 | 1 | 2 | 3 |
|---|---|---|---|---|
| `insight` | absent/wrong | restates the reading (`"cpu_temp is 82C"`) | identifies deviation from normal | deviation **and** its implication or likely cause |
| `grounding` | no numbers | mentions a source, no value | cites the value | cites value **and** the baseline it deviates from |
| `concision` | preamble/padding | some filler | terse, minor redundancy | every clause carries information |
| `typing` | wrong type | defensible | correct | correct and severity-appropriate |

Level 1 on `insight` is exactly `gemma3:1b`'s observed failure — it produced
valid, well-typed JSON that only restated its input. That is a 1, not a 2, and
the anchor is what makes the distinction reproducible across runs.

## Validate the judge

**A judge nobody checked is not a measurement.** Every run includes calibration
cases — fabricated outputs of known quality:

| Case | Expected |
|---|---|
| `cal_good` | high `insight`, high `grounding` |
| `cal_restating` | `insight` ≤ 1 |
| `cal_hallucinated` | fails `no_invented_numbers` at stage 1 |
| `cal_essay` | low `concision` |

If the judge doesn't rank these correctly, **the run is invalid** and its scores
are discarded. This catches a broken prompt, a wrong model ID, or a schema change
before anyone acts on the numbers.

Also: the judge is **blinded** — it never sees which model produced an output,
and candidate order is shuffled when comparing. Model names in a judge prompt
invite brand priors.

## Flow

Generation and judging are separate commands, and that separation is
load-bearing: generation costs ~30 min per model on this Pi. Changing a rubric
must never mean re-running it.

```bash
# 1. deterministic, every commit
uv run pytest

# 2. generate — slow, local model, stores raw outputs
uv run python -m smollama.evals run --model qwen2.5:1.5b

#    ...or measure the old scanning path, for comparison
uv run python -m smollama.evals run --model qwen2.5:1.5b --path scan

# 3. judge — fast, cloud, re-runnable against any stored run
uv run python -m smollama.evals judge --run <id> --judge claude-opus-5

# 4. compare
uv run python -m smollama.evals compare <run-a> <run-b>
```

Runs land in `evals/runs/<timestamp>-<model>/` as JSON: the case inputs, raw
outputs, stage-1 results, and (after judging) rubric scores. Committing a run
makes a claim reproducible.

## Two paths, and why `detection` only applies to one

`--path` selects which question the model is asked, matching the
`memory.observation_use_detectors` config switch:

- **`detectors`** (default, what production does) — code detects, the model
  describes. Cases whose detection is silent make **no model call at all**, and are
  reported as `decided by detectors` rather than counted as model restraint.
- **`scan`** — the pre-detector path: the model is handed every reading and asked to
  find something itself.

On the detector path `detection` is deliberately **not reported**. The detector layer
decided it, so a number there would credit the model for code's work. `related_sources`
is likewise excluded from gating and scoring, because the loop fills that field itself
before storing.

That leaves a real question the harness can still answer deterministically: did the
model add anything? `echo` is the fraction of observations that hand the signal line
back verbatim — passing every other gate while contributing nothing. Everything else
about description quality is genuinely subjective and goes to the judge's `insight`
and `grounding` dimensions.

`compare` prints a warning when the runs used different paths, because that is a
comparison of architectures, not of models.

Measured on `qwen2.5:1.5b`, same model and same eight cases:

| | scan | detectors |
|---|---|---|
| gate pass | 100% | 100% |
| detection | **0.00** | n/a — code's, and it caught all 5 |
| echo | n/a | 0.20 |
| model calls | 8 of 8 | 5 of 8 |
| total wall | 165.2s | 117.2s |

The wall-time gap understates the production effect: 3 of 8 golden cases are quiet,
where in production nearly every cycle is.

### Judge model and cost

Default `claude-opus-5`. The judge should be more capable than anything being
judged — a weak judge cannot recognise insight it wouldn't produce. Configure via
`--judge`; `claude-fable-5` for maximum capability.

Judging is **not latency-sensitive**, so it goes through the **Batch API at 50%
off**: all cases submitted as one batch, polled to completion. For ~30 cases the
judge cost is cents.

> Note: the exact judge model ID is a config value, not a constant in the code.
> Model IDs change; hardcoding one guarantees a stale default.

## Golden cases

Cases are versioned data, not code. Four kinds, and the mix matters more than
the count:

1. **Real failures from this system's history** — the writer going silent on
   Sep 7, `hcsr04` stuck at exactly 0.0, swap at 4.9 GB, disk at 99 %. These are
   gold because the correct answer is known and they are what production
   actually produced.
2. **Deliberately normal** — steady readings. Correct answer: report nothing.
   Without these, `detection` alone rewards a model that flags everything.
3. **Graded subtlety** — the same anomaly at 6σ, 3σ, 1.5σ, to find where a model
   stops discriminating rather than just whether it can.
4. **Adversarial** — readings whose *values* contain text that looks like an
   instruction, and inputs that tempt schema violations.

A case records its own expected answer:

```json
{"id": "writer_silent",
 "sources": {...},
 "expect": {"detect": ["jetson-nano:jetson_inference:person_count"],
            "min_observations": 1},
 "provenance": "production, 2026-09-07 — writer stopped, undetected 19 days"}
```

## Reading the results

Report **per-dimension, never a single blended score.** A model that is 2× faster
and slightly worse at `insight` is a real tradeoff for a Pi, and an average hides
it. One composite rule only: **a model failing stage-1 gates scores 0 overall**,
regardless of how good its prose is.

Treat as noise unless it reproduces: a 1-point difference on one dimension of one
case. Judge scores have variance; ranking changes on a single case are not
signal.

## What this does not measure

Stated plainly so the numbers aren't over-read:

- **Long-run behaviour.** Every case is single-shot. Drift over hundreds of live
  cycles is invisible here.
- **Tool use.** The `observation_use_tools` profile isn't covered; these cases
  assume the schema-constrained single-shot path.
- **Real-world distribution.** Cases over-represent anomalies, because that's
  what's worth testing. Production is overwhelmingly normal — which is why
  `restraint` is weighted as heavily as `detection`.
- **Whether the observations are useful to a human.** No ground truth exists for
  that without the dashboard keep/dismiss feedback in
  `docs/observation-rules.md`.
