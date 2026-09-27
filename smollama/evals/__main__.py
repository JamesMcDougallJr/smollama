"""Evaluation CLI: generate, judge, compare.

Generation and judging are separate commands on purpose. Generating a run means
~30 min of local model time on a Pi; changing a rubric must never mean re-running
it. Raw outputs are stored, and judging reads them back.

    uv run python -m smollama.evals run --model qwen2.5:1.5b
    uv run python -m smollama.evals judge --run <id>
    uv run python -m smollama.evals compare <run-a> <run-b>
"""

import argparse
import json
import statistics
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .cases import load_cases
from .checks import score_case
from .judge import DEFAULT_JUDGE_MODEL, DIMENSIONS

RUNS_DIR = Path("evals/runs")
OLLAMA = "http://localhost:11434"


def _prompt(case, max_items: int, max_chars: int) -> str:
    """Build the observation prompt the way the production loop does."""
    from ..memory.observation_loop import OBSERVATION_PROMPT

    current = "\n".join(
        f"- {k}: {v}" for k, v in sorted(case.current.items())
    )
    history = "\n".join(
        f"- {k}: {v}" for k, v in sorted(case.history.items())
    )
    return OBSERVATION_PROMPT.format(
        lookback_minutes=60,
        current_readings=current or "No current readings",
        recent_history=history or "No recent history",
        past_observations="No relevant past observations",
        max_items=max_items,
        max_chars=max_chars,
    )


def _generate(model: str, prompt: str, schema: dict, num_predict: int) -> tuple:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "think": False,
        "format": schema,
        "options": {"num_predict": num_predict} if num_predict > 0 else {},
    }
    req = urllib.request.Request(
        OLLAMA + "/api/chat",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=2400) as r:
        data = json.loads(r.read())
    wall = time.perf_counter() - t0
    content = (data.get("message") or {}).get("content") or ""
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        parsed = content  # keep the raw text; the schema gate will fail it
    return parsed, wall, data.get("eval_count") or 0


def cmd_run(args) -> int:
    from ..memory.observation_loop import build_observation_schema

    cases = load_cases(tags=args.tags)
    schema = build_observation_schema(args.max_items, args.max_chars)
    run_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{args.model.replace(':', '_')}"
    out_dir = RUNS_DIR / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"run {run_id}: {len(cases)} cases against {args.model}")
    rows = []
    for i, case in enumerate(cases, 1):
        prompt = _prompt(case, args.max_items, args.max_chars)
        try:
            output, wall, tokens = _generate(
                args.model, prompt, schema, args.num_predict
            )
        except Exception as e:
            print(f"  [{i}/{len(cases)}] {case.id}: FAILED {type(e).__name__}: {e}")
            rows.append({"case_id": case.id, "error": str(e)})
            continue

        scored = score_case(
            output, case, max_items=args.max_items, max_chars=args.max_chars
        )
        scored.update({"wall_s": round(wall, 1), "eval_tokens": tokens,
                       "output": output, "prompt": prompt})
        rows.append(scored)

        flag = "ok " if scored["gates_passed"] else "GATE"
        det = scored["metrics"].get("detection")
        res = scored["metrics"].get("restraint")
        mark = "det=%s" % det if det is not None else "res=%s" % res
        print(f"  [{i}/{len(cases)}] {case.id:<22} {flag} {mark:<8} "
              f"{wall:5.1f}s {tokens:4d}tok "
              f"{','.join(scored['failures']) if scored['failures'] else ''}")

    payload = {
        "run_id": run_id,
        "model": args.model,
        "settings": {"max_items": args.max_items, "max_chars": args.max_chars,
                     "num_predict": args.num_predict},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "results": rows,
    }
    (out_dir / "run.json").write_text(json.dumps(payload, indent=2))
    _summarize(payload)
    print(f"\nwrote {out_dir / 'run.json'}")
    print(f"next: uv run python -m smollama.evals judge --run {run_id}")
    return 0


def cmd_judge(args) -> int:
    from .judge import JudgeRequest, calibration_requests, judge_batch, validate_judge

    run_path = RUNS_DIR / args.run / "run.json"
    if not run_path.exists():
        print(f"no such run: {run_path}", file=sys.stderr)
        return 2
    payload = json.loads(run_path.read_text())

    # Only gate-passing rows are worth judge tokens.
    requests = calibration_requests()
    for row in payload["results"]:
        if row.get("gates_passed"):
            requests.append(
                JudgeRequest(
                    key=row["case_id"],
                    case_input=row["prompt"],
                    output=row["output"],
                )
            )
    judged = len(requests) - len(calibration_requests())
    print(f"judging {judged} gate-passing cases (+{len(calibration_requests())} "
          f"calibration) with {args.judge}")

    scores = judge_batch(requests, model=args.judge)

    ok, problems = validate_judge(scores)
    if not ok:
        print("\nJUDGE CALIBRATION FAILED — scores discarded:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        print("\nA judge that cannot separate the calibration cases cannot be "
              "trusted on real ones. Fix the rubric or judge model and re-run.",
              file=sys.stderr)
        return 1

    for row in payload["results"]:
        row["judge"] = scores.get(row["case_id"])
    payload["judge_model"] = args.judge
    payload["judge_calibration_passed"] = True
    run_path.write_text(json.dumps(payload, indent=2))

    _summarize(payload)
    print(f"\nupdated {run_path}")
    return 0


def _summarize(payload: dict) -> dict:
    rows = [r for r in payload["results"] if "error" not in r]
    gated = [r for r in rows if r["gates_passed"]]
    det = [r["metrics"]["detection"] for r in rows
           if r["metrics"].get("detection") is not None]
    res = [r["metrics"]["restraint"] for r in rows
           if r["metrics"].get("restraint") is not None]

    summary = {
        "model": payload["model"],
        "cases": len(rows),
        "gate_pass_rate": len(gated) / len(rows) if rows else 0.0,
        "detection": statistics.mean(det) if det else None,
        "restraint": statistics.mean(res) if res else None,
        "median_wall_s": statistics.median([r["wall_s"] for r in rows]) if rows else None,
    }
    for dim in DIMENSIONS:
        vals = [r["judge"][dim] for r in rows
                if isinstance(r.get("judge"), dict) and isinstance(r["judge"].get(dim), int)]
        summary[dim] = round(statistics.mean(vals), 2) if vals else None

    print(f"\n── {payload['model']} ──")
    print(f"  gate pass   : {summary['gate_pass_rate']*100:.0f}%")
    print(f"  detection   : {_fmt(summary['detection'])}    (anomaly cases)")
    print(f"  restraint   : {_fmt(summary['restraint'])}    (normal cases)")
    print(f"  median wall : {summary['median_wall_s']}s")
    if any(summary[d] is not None for d in DIMENSIONS):
        print("  judge       : " + "  ".join(
            f"{d}={_fmt(summary[d])}" for d in DIMENSIONS))
    else:
        print("  judge       : not yet run")
    return summary


def _fmt(v) -> str:
    return "n/a" if v is None else f"{v:.2f}"


def cmd_compare(args) -> int:
    payloads = []
    for run in args.runs:
        p = RUNS_DIR / run / "run.json"
        if not p.exists():
            print(f"no such run: {p}", file=sys.stderr)
            return 2
        payloads.append(json.loads(p.read_text()))

    summaries = [_summarize(p) for p in payloads]
    keys = ["gate_pass_rate", "detection", "restraint", "median_wall_s", *DIMENSIONS]

    print("\n" + "=" * 72)
    print(f"{'metric':<16}" + "".join(f"{s['model']:>18}" for s in summaries))
    print("-" * 72)
    for k in keys:
        cells = "".join(f"{_fmt(s.get(k)):>18}" for s in summaries)
        print(f"{k:<16}{cells}")
    print("\nRead per-dimension; a single blended score hides real tradeoffs.")
    print("A 1-point judge difference on one case is noise, not signal.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="smollama.evals", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="generate a run against a local model")
    r.add_argument("--model", required=True)
    r.add_argument("--max-items", type=int, default=3)
    r.add_argument("--max-chars", type=int, default=200)
    r.add_argument("--num-predict", type=int, default=256)
    r.add_argument("--tags", nargs="*", help="only cases carrying any of these tags")
    r.set_defaults(func=cmd_run)

    j = sub.add_parser("judge", help="score a stored run with a cloud judge")
    j.add_argument("--run", required=True)
    j.add_argument("--judge", default=DEFAULT_JUDGE_MODEL)
    j.set_defaults(func=cmd_judge)

    c = sub.add_parser("compare", help="compare stored runs side by side")
    c.add_argument("runs", nargs="+")
    c.set_defaults(func=cmd_compare)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
