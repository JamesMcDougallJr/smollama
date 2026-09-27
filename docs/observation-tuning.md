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
