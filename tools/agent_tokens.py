#!/usr/bin/env python3
"""Per-article token accounting for the censurado newsroom.

Every article we author the Lane-B way (one Claude Code subagent writes one
article) costs exactly that subagent's token count. The CLI shows it on screen
as the agent's token meter, and the completion notification carries it as
<subagent_tokens>. This script records that number alongside the real billed
breakdown pulled from the agent's transcript, so we accumulate per-article
costs and an average instead of eyeballing the screen.

Two token numbers are kept per agent, on purpose:
  - cli_tokens   : the headline number the CLI/notification reports for the
                   agent (pass it in with --cli-tokens). This is what you see.
  - the breakdown: input / output / cache_create / cache_read summed from the
                   agent transcript JSONL. total_billed = their sum. This is the
                   real cost (cache reads are billed at a discount, but counted).
They do not match because the CLI meter is computed differently; we store both.

Transcripts live at:
  ~/.claude/projects/<project>/<session>/subagents/agent-<agentId>.jsonl

Usage:
  agent_tokens.py extract <agentId|path>
  agent_tokens.py record  <agentId|path> --kind article --author author-a \
        --title "..." --cli-tokens 55023 --tool-uses 22 --duration-ms 259443
  agent_tokens.py summary            # averages, grouped by kind
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
METRICS = os.path.join(os.path.dirname(HERE), "metrics", "token-usage.jsonl")


def resolve(agent_or_path: str) -> str:
    """Accept a full path or a bare agentId; return the transcript path."""
    if os.path.sep in agent_or_path and agent_or_path.endswith(".jsonl"):
        return agent_or_path
    if os.path.isfile(agent_or_path):
        return agent_or_path
    aid = agent_or_path
    if aid.startswith("agent-"):
        aid = aid[len("agent-"):]
    if aid.endswith(".jsonl"):
        aid = aid[: -len(".jsonl")]
    pattern = os.path.expanduser(
        f"~/.claude/projects/*/*/subagents/agent-{aid}.jsonl"
    )
    hits = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    if not hits:
        sys.exit(f"no transcript found for agent '{agent_or_path}' ({pattern})")
    return hits[0]


def extract(path: str) -> dict:
    """Sum usage across an agent transcript. Outputs only aggregates."""
    inp = out = cc = cr = 0
    records = 0
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            u = None
            if isinstance(o, dict):
                m = o.get("message")
                if isinstance(m, dict):
                    u = m.get("usage")
                if u is None:
                    u = o.get("usage")
            if isinstance(u, dict):
                records += 1
                inp += u.get("input_tokens", 0) or 0
                out += u.get("output_tokens", 0) or 0
                cc += u.get("cache_creation_input_tokens", 0) or 0
                cr += u.get("cache_read_input_tokens", 0) or 0
    aid = os.path.basename(path)
    if aid.startswith("agent-"):
        aid = aid[len("agent-"):]
    if aid.endswith(".jsonl"):
        aid = aid[: -len(".jsonl")]
    return {
        "agent_id": aid,
        "usage_records": records,
        "input_tokens": inp,
        "output_tokens": out,
        "cache_create_tokens": cc,
        "cache_read_tokens": cr,
        "total_billed": inp + out + cc + cr,
    }


def cmd_extract(args):
    print(json.dumps(extract(resolve(args.agent)), indent=2))


def cmd_record(args):
    rec = extract(resolve(args.agent))
    rec.update(
        {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "kind": args.kind,
            "author": args.author,
            "title": args.title,
            "section": args.section,
            "cli_tokens": args.cli_tokens,
            "tool_uses": args.tool_uses,
            "duration_ms": args.duration_ms,
            "note": args.note,
        }
    )
    os.makedirs(os.path.dirname(METRICS), exist_ok=True)
    with open(METRICS, "a") as f:
        f.write(json.dumps(rec) + "\n")
    print(json.dumps(rec, indent=2))


# Opus 4.8 list price, USD per million tokens. Snapshot 2026-06-27 from
# https://platform.claude.com/docs/en/about-claude/pricing
# Cache writes assumed 5-minute (the default automatic-caching multiplier, 1.25x).
PRICE_OPUS_48 = {
    "input": 5.0,        # base prompt tokens
    "cache_write": 6.25,  # 5-minute write (1.25x base)
    "cache_read": 0.5,    # cache hit (0.1x base)
    "output": 25.0,       # generated tokens
}


def _costs(rec: dict, price: dict = PRICE_OPUS_48) -> dict:
    inp = rec.get("input_tokens", 0) or 0
    out = rec.get("output_tokens", 0) or 0
    cc = rec.get("cache_create_tokens", 0) or 0
    cr = rec.get("cache_read_tokens", 0) or 0
    c_in = inp * price["input"] / 1e6
    c_cw = cc * price["cache_write"] / 1e6
    c_cr = cr * price["cache_read"] / 1e6
    c_out = out * price["output"] / 1e6
    prefill_tokens = inp + cc + cr
    return {
        "prefill_tokens": prefill_tokens,
        "generated_tokens": out,
        "prefill_cost": c_in + c_cw + c_cr,
        "generated_cost": c_out,
        "total_cost": c_in + c_cw + c_cr + c_out,
    }


def cmd_cost(args):
    if not os.path.isfile(METRICS):
        sys.exit(f"no metrics file yet at {METRICS}")
    rows = []
    with open(METRICS) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    by_kind: dict[str, list] = {}
    for r in rows:
        by_kind.setdefault(r.get("kind", "unknown"), []).append(r)
    print("Opus 4.8 list price (USD/MTok): input 5 / cache-write 6.25 / "
          "cache-read 0.5 / output 25\n")
    for kind in sorted(by_kind):
        g = by_kind[kind]
        costs = [_costs(r) for r in g]
        n = len(g)
        ap = sum(c["prefill_cost"] for c in costs) / n
        ag = sum(c["generated_cost"] for c in costs) / n
        at = sum(c["total_cost"] for c in costs) / n
        apt = sum(c["prefill_tokens"] for c in costs) / n
        agt = sum(c["generated_tokens"] for c in costs) / n
        print(f"[{kind}]  n={n}")
        print(f"  avg prefill  : {apt:>12,.0f} tok  ${ap:.4f}")
        print(f"  avg generated: {agt:>12,.0f} tok  ${ag:.4f}")
        print(f"  avg total    : {'':>12}      ${at:.4f}  "
              f"(prefill {100*ap/at:.0f}% / gen {100*ag/at:.0f}%)")
        for r, c in zip(g, costs):
            label = r.get("author") or r.get("title", "")[:24]
            print(f"    - {label:<18} prefill={c['prefill_tokens']:>10,.0f}tok "
                  f"gen={c['generated_tokens']:>7,.0f}tok  ${c['total_cost']:.4f}")
        print()


def cmd_summary(args):
    if not os.path.isfile(METRICS):
        sys.exit(f"no metrics file yet at {METRICS}")
    rows = []
    with open(METRICS) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    by_kind: dict[str, list] = {}
    for r in rows:
        by_kind.setdefault(r.get("kind", "unknown"), []).append(r)

    def avg(vals):
        vals = [v for v in vals if isinstance(v, (int, float))]
        return round(sum(vals) / len(vals)) if vals else 0

    print(f"records: {len(rows)}  ({METRICS})\n")
    for kind in sorted(by_kind):
        g = by_kind[kind]
        print(f"[{kind}]  n={len(g)}")
        print(f"  avg cli_tokens   : {avg([r.get('cli_tokens') for r in g])}")
        print(f"  avg total_billed : {avg([r.get('total_billed') for r in g])}")
        print(f"  avg output_tokens: {avg([r.get('output_tokens') for r in g])}")
        print(f"  avg duration_ms  : {avg([r.get('duration_ms') for r in g])}")
        if kind == "article":
            for r in g:
                print(
                    f"    - {r.get('author','?'):<18} "
                    f"cli={r.get('cli_tokens')} billed={r.get('total_billed')} "
                    f":: {r.get('title','')[:50]}"
                )
        print()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    pe = sub.add_parser("extract", help="print usage breakdown for one agent")
    pe.add_argument("agent")
    pe.set_defaults(func=cmd_extract)

    pr = sub.add_parser("record", help="append one agent's usage to the metrics file")
    pr.add_argument("agent")
    pr.add_argument("--kind", default="article", help="article | research | investigation | other")
    pr.add_argument("--author", default=None)
    pr.add_argument("--title", default=None)
    pr.add_argument("--section", default=None)
    pr.add_argument("--cli-tokens", type=int, default=None, dest="cli_tokens")
    pr.add_argument("--tool-uses", type=int, default=None, dest="tool_uses")
    pr.add_argument("--duration-ms", type=int, default=None, dest="duration_ms")
    pr.add_argument("--note", default=None)
    pr.set_defaults(func=cmd_record)

    ps = sub.add_parser("summary", help="averages grouped by kind")
    ps.set_defaults(func=cmd_summary)

    pc = sub.add_parser("cost", help="Opus 4.8 prefill-vs-generated cost, by kind")
    pc.set_defaults(func=cmd_cost)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
