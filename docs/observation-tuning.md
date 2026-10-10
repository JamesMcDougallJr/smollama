# Tuning the Observation Pass

The observation loop's LLM call is shaped by config, not hardcoded. Defaults are
set for a **small local model (~1–2B) on CPU**; loosen them when you run
something capable of agentic behaviour.

All keys live under `memory:` in `config.yaml` / `cluster.yaml`.

| Key | Default | Purpose |
|---|---|---|
| `observation_num_predict` | `256` | Generation cap. `0` = uncapped (model decides). |
| `observation_max_items` | `3` | Max observations per cycle (enforced by the schema). |
| `observation_max_chars` | `200` | Max chars per observation (enforced by the schema). |
| `observation_structured_output` | `true` | Pin decoding to a JSON schema. |
| `observation_use_tools` | `false` | Let the pass call tools and run the full agent loop. |
| `observation_system_prompt` | `""` | `""` = built-in task prompt, `"node"` = inherit `agent.system_prompt`, or a literal string. |
| `observation_use_detectors` | `true` | Detect in code, describe with the model. Off restores the old scanning prompt. |
| `observation_max_signals` | `3` | Most findings described in one cycle, highest score first. |
| `observation_maintenance_every` | `10` | Run rule lifecycle maintenance once per this many cycles. `0` disables. |
| `detector_window_hours` | `168` | History the detectors see, independent of `observation_lookback_minutes`. |
| `quarantine_enabled` | `true` | Give the agent tools to stop storing a source with invalid data. Off removes them from the model entirely. |
| `quarantine_max_sources` | `10` | Most sources stopped at once. |
| `quarantine_min_samples` | `30` | Identical consecutive readings required before a source qualifies. |
| `quarantine_min_flat_hours` | `6` | ...and they must span at least this long. |
| `quarantine_trickle_hours` | `6` | While stopped, one heartbeat reading is still kept per this interval. |

## Who does the detecting

`observation_use_detectors` is the biggest switch here, because it changes what the
model is asked for.

**On (default).** `smollama/detectors/` runs first. If nothing fired, **no model is
called at all** — the cycle costs zero inference. If something fired, the model gets
1–3 specific findings and writes one sentence each.

**Off.** Every current reading and an aggregated history go into the prompt and the
model is asked to find something itself.

Off is the path the evaluation harness measured at **0.00 detection** on
`qwen2.5:1.5b` — silent even on a 6-sigma spike — and **0.00 restraint** on
`gemma3:1b`, which flagged deliberately steady readings. Neither can discriminate,
and no prompt fixes that, which is why detection moved into code. Measured on the
same eight cases after the switch: 100% gate pass, every planted anomaly surfaced,
and 3 of 8 cycles needing no model at all.

Keep it on unless you are deliberately reproducing the older behaviour.

A signal keeps costing an inference every cycle until an **active** rule covers it.
`/rules` lists what is firing with a **Watch** button that creates one — before that
button existed, `store.propose` had no caller in the running system, so no rule could
come into being and the coverage filter was structurally inert.

Signals that describe one event are collapsed first: `mem_percent` and
`mem_available_mb` move in opposite directions when memory fills, so only the
highest-scoring of the pair is narrated and the other is carried along in
`related_sources`. The pairs are a hardcoded list in `detectors/core.py`
(`CORRELATED_METRICS`); an unlisted pair reports twice.

On this path the loop fills `related_sources` itself from the signal that fired
rather than trusting the model to copy an identifier — `qwen2.5:1.5b` returned
`system:cpu_temp` for a case that never mentioned it, and `system:hcsr04` for
`hcsr04:distance`. That field decides what later keep/dismiss feedback is attributed
to, so a wrong one teaches the system about an unrelated source.

## Stopping a source that reports invalid data

The agent has three tools: `stop_recording_source`, `resume_recording_source` and
`list_stopped_sources`. They exist to save space when a sensor is dead or unplugged
and just writes the same value forever (an unwired HC-SR04 logged a phantom `0.0`
every cycle for a week).

A wrong call loses data silently, so the model is not trusted with the decision:

- **It supplies a source and a reason, never evidence.** Code recomputes whether the
  source's trailing run is one unchanging value (`quarantine_min_samples` readings over
  `quarantine_min_flat_hours`). Extra arguments are ignored, so there is no path for
  the model's own numbers into the check.
- **A changing source is always refused**, whatever the reason. A level shift or a trend
  is what this system exists to record. Refusals come back as data with a reason, so the
  model can correct course instead of retrying blindly.
- **It is reversible by construction.** The source is still read every cycle. Repeats of
  the frozen value are not stored; the first reading that differs releases it and *is*
  stored, so a replaced sensor or a door that finally opens resumes within one cycle.
- **Bounded:** at most `quarantine_max_sources` at once, a one-hour cooldown before a
  just-released source can be stopped again (flap guard), and an audit row for every
  stop and release recording who did it.

While stopped, signals about the source are not narrated (its flatline would otherwise
fire every cycle, and its staleness once recording stops), and `/rules` lists it under
**Stopped recording** with a **Resume** button.

What it does not do: it does not stop the plugin or the producer, so an edge node keeps
publishing and the master keeps reading. To actually stop a sensor, disable the plugin in
config. It also does not delete existing rows.

## The one hard constraint

`observation_structured_output` and `observation_use_tools` are **mutually
exclusive**. A JSON schema pins decoding to a fixed shape, which leaves no room
for the model to emit a tool call. Setting both logs a warning and honours
`use_tools`, dropping the schema — tools are the more specific intent.

Measured against Ollama with `qwen2.5:1.5b`, same prompt asking it to use a tool:

| Request | Tool calls emitted |
|---|---|
| tools, no schema | **1** (`read_source`) |
| tools **+** schema | **0** — output forced into the schema instead |

The failure is silent: it looks like the model chose not to act, which is why the
conflict is resolved loudly instead of being left to the caller.

## Two worked profiles

**Small local model (the default).** Bounded, structured, single-shot. The
measured reason these defaults exist: on a Pi 5 with an 11-source prompt, given
only `format: "json"`, `qwen2.5:1.5b` returned empty objects and `gemma3:1b`
enumerated every input until it overran the token cap mid-object (unparseable).
The same models under a schema produced conformant output in half the wall time.

```yaml
memory:
  observation_num_predict: 256
  observation_structured_output: true
  observation_use_tools: false
  observation_system_prompt: ""      # terse, non-conversational, no tools
```

**Capable model that should act.** Let it gather its own context and take
actions. Expect cycles to cost far more wall time and to sometimes return prose
rather than parseable JSON — unparseable responses are discarded, so the pass
degrades to "no observation recorded" rather than storing junk.

```yaml
memory:
  observation_num_predict: 0         # uncapped
  observation_structured_output: false  # implied by use_tools anyway
  observation_use_tools: true        # read sensors, search memory, publish
  observation_system_prompt: node    # behave as the node persona
```

With `use_tools: true` the pass runs the multi-iteration agent loop, bounded by
`agent.max_tool_iterations`. Tool schemas are sent to the model
programmatically — do **not** list tools in `agent.system_prompt`; that list
drifts out of date and the model already has the real definitions.

## Whether structured output is still needed

Test it rather than assume. Two things to check on any new model:

1. **Latency + validity** on a real prompt — does it return parseable JSON
   within the cap?
2. **Discrimination** — feed it a deliberately normal reading set and a
   deliberately anomalous one. A model that reports nothing in both cases looks
   great on latency and is useless. This is how `qwen2.5:1.5b` was caught being
   always-silent under plain `format: "json"`.

A speed benchmark alone will pick the wrong model.
