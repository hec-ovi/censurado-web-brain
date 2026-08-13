#!/usr/bin/env python3
"""Run one durable article pipeline. Contract: CONTRACT.md; result JSON on stdout.

Three invocations:
  run.py --config C --topic T --author A --section S [--run-id R] [--mode preview|auto]
  run.py approve --config C --run-id R      publish a previewed run's piece
  run.py events  --config C [-n N] [--follow]   console of run events and failures
"""
import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.config import PipelineConfig            # noqa: E402
from src.errors import ConfigError, PipelineError, PublishError  # noqa: E402


def emit_event(run_dir: Path, event: dict) -> None:
    event = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **event}
    with (run_dir / "events.jsonl").open("a") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def load_config(path: str) -> PipelineConfig | None:
    try:
        return PipelineConfig.load(path)
    except ConfigError as e:
        print(f"CONFIG_INVALID:\n{e}", file=sys.stderr)
        return None


def cmd_run(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Run one article through the durable pipeline")
    ap.add_argument("--config", required=True)
    ap.add_argument("--topic", required=True)
    ap.add_argument("--author", required=True)
    ap.add_argument("--section", required=True)
    ap.add_argument("--run-id", help="Durability key; reuse to resume/replay a run")
    ap.add_argument("--mode", choices=["preview", "auto"], default="preview",
                    help="preview (default): walk and gate, return the piece for approval; "
                         "auto: publish when the gate passes")
    ap.add_argument("--image-brief",
                    help="Hero image brief; renders best-effort through the toolkit "
                         "(skipped cleanly when ComfyUI is off)")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    if cfg is None:
        return 2

    cfg.run_dir.mkdir(parents=True, exist_ok=True)
    from dbos import DBOS, SetWorkflowID
    DBOS(config={"name": "censurado-pipeline", "log_level": "WARNING",
                 "system_database_url": f"sqlite:///{cfg.run_dir / '.dbos.sqlite'}"})
    import src.flow as flow
    DBOS.launch()

    run_id = args.run_id or "run-" + uuid.uuid4().hex[:12]
    inputs = {"topic": args.topic, "author": args.author, "section": args.section,
              "mode": args.mode}
    if args.image_brief:
        inputs["image_brief"] = args.image_brief
    emit_event(cfg.run_dir, {"event": "run-start", "run_id": run_id, "mode": args.mode,
                             "author": args.author, "section": args.section,
                             "topic": args.topic})
    try:
        with SetWorkflowID(run_id):
            handle = DBOS.start_workflow(flow.article_run, cfg.data, inputs)
        result = handle.get_result()
    except Exception as e:
        code = 1
        detail = f"pipeline failed: {e}"
        for err in (e, e.__cause__):
            if isinstance(err, PipelineError):
                code, detail = err.exit_code, f"{type(err).__name__}: {err}"
                break
        else:
            if "AdapterError" in str(e) or "MaxStepRetries" in type(e).__name__:
                code = 4
        print(detail, file=sys.stderr)
        emit_event(cfg.run_dir, {"event": "run", "run_id": run_id, "mode": args.mode,
                                 "status": "failed", "exit_code": code, "error": detail[:300]})
        return code
    finally:
        DBOS.destroy()

    emit_event(cfg.run_dir, {"event": "run", "run_id": run_id, "mode": args.mode,
                             "status": result["status"],
                             **({"notes": result["notes"]} if result.get("notes") else {})})
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] in ("published", "previewed") else 3


def cmd_approve(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="run.py approve",
                                 description="Publish the piece of a previewed run")
    ap.add_argument("--config", required=True)
    ap.add_argument("--run-id", required=True)
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    if cfg is None:
        return 2
    piece_file = cfg.run_dir / args.run_id / "piece.json"
    if not piece_file.is_file():
        print(f"CONFIG_INVALID:\nno previewed piece at {piece_file} "
              "(run the pipeline in preview mode first)", file=sys.stderr)
        return 2

    from src.publisher import Publisher
    stored = json.loads(piece_file.read_text())
    try:
        pub = Publisher(cfg.data).publish(
            stored["piece"], stored["inputs"], idempotency_key=args.run_id)
    except PublishError as e:
        print(f"PublishError: {e}", file=sys.stderr)
        emit_event(cfg.run_dir, {"event": "approve", "run_id": args.run_id,
                                 "status": "failed", "exit_code": 5, "error": str(e)[:300]})
        return 5
    result = {"status": "published", "run_id": args.run_id,
              "artifacts": str(piece_file.parent), **pub}
    emit_event(cfg.run_dir, {"event": "approve", "run_id": args.run_id,
                             "status": "published", "slug": pub.get("slug", "")})
    print(json.dumps(result, ensure_ascii=False))
    return 0


def cmd_events(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="run.py events",
                                 description="Console of run events and failures")
    ap.add_argument("--config", required=True)
    ap.add_argument("-n", type=int, default=30, help="Lines of history to show (default 30)")
    ap.add_argument("--follow", action="store_true", help="Keep printing new events")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    if cfg is None:
        return 2
    path = cfg.run_dir / "events.jsonl"

    def show(line: str) -> None:
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            return
        when = e.get("ts", "")[:19].replace("T", " ")
        status = e.get("status", "")
        mark = "FAIL" if status == "failed" else "ok  " if status else "... "
        head = f"{when}  {mark} {e.get('event', ''):9s} {e.get('run_id', ''):22s}"
        tail = " ".join(str(e[k]) for k in ("mode", "status", "author", "topic", "slug")
                        if e.get(k))
        if e.get("error"):
            tail += f"  [{e['error']}]"
        if e.get("notes"):
            tail += f"  ({e['notes']})"
        print(f"{head} {tail}")

    lines = path.read_text().splitlines() if path.is_file() else []
    for line in lines[-args.n:]:
        show(line)
    if not args.follow:
        return 0
    seen = len(lines)
    try:
        while True:
            time.sleep(2)
            lines = path.read_text().splitlines() if path.is_file() else []
            for line in lines[seen:]:
                show(line)
            seen = len(lines)
    except KeyboardInterrupt:
        return 0


def cmd_batch(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="run.py batch",
                                 description="Run the daily batch: candidates per author, "
                                             "the jefe selects, articles fan out")
    ap.add_argument("--config", required=True)
    ap.add_argument("--run-id", help="Batch id; reuse to resume (the stored plan is kept)")
    ap.add_argument("--mode", choices=["preview", "auto"], default="preview",
                    help="preview (default): write and hold every piece; "
                         "auto: publish them in portada order")
    ap.add_argument("--authors", help="Comma-separated handles (default: every author "
                                      "with a beat and attached sources)")
    ap.add_argument("--directive", default="",
                    help="Operator directive for this edition (a custom schedule's "
                         "prompt); reaches the candidates and jefe prompts")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    if cfg is None:
        return 2
    cfg.run_dir.mkdir(parents=True, exist_ok=True)
    from src.batch import run_batch
    batch_id = args.run_id or "batch-" + uuid.uuid4().hex[:12]
    authors = [a.strip() for a in args.authors.split(",")] if args.authors else None
    emit_event(cfg.run_dir, {"event": "batch-start", "run_id": batch_id, "mode": args.mode})
    try:
        result = run_batch(cfg.data, str(Path(args.config).resolve()), batch_id,
                           args.mode, authors, lambda e: emit_event(cfg.run_dir, e),
                           directive=args.directive)
    except PipelineError as e:
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        emit_event(cfg.run_dir, {"event": "batch", "run_id": batch_id, "status": "failed",
                                 "exit_code": e.exit_code, "error": str(e)[:300]})
        return e.exit_code
    emit_event(cfg.run_dir, {"event": "batch", "run_id": batch_id,
                             "status": result["status"]})
    print(json.dumps(result, ensure_ascii=False))
    return 0


def cmd_batch_approve(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="run.py batch-approve",
                                 description="Publish a previewed batch in portada order")
    ap.add_argument("--config", required=True)
    ap.add_argument("--run-id", required=True)
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    if cfg is None:
        return 2
    result_file = cfg.run_dir / args.run_id / "result.json"
    if not result_file.is_file():
        print(f"CONFIG_INVALID:\nno batch result at {result_file}", file=sys.stderr)
        return 2
    articles = json.loads(result_file.read_text())["articles"]
    held = sorted([a for a in articles if a.get("status") == "previewed"],
                  key=lambda a: -a.get("portada_rank", 0))
    published = []
    for art in held:
        code = cmd_approve(["--config", args.config, "--run-id", art["run_id"]])
        published.append({"run_id": art["run_id"], "ok": code == 0})
    print(json.dumps({"status": "batch-published", "batch_id": args.run_id,
                      "approved": published}, ensure_ascii=False))
    return 0 if all(p["ok"] for p in published) else 5


def cmd_topics(argv: list[str]) -> int:
    """Run the topic-normalization task: read the live topic board, have the
    configured model cluster true duplicates ({"mapa": {variante: canonico}}),
    and apply the remap through the newsroom cleanse CLI (permalinks and content
    hashes stay stable; only tag sets move)."""
    ap = argparse.ArgumentParser(prog="run.py topics",
                                 description="Normalize the topic board: the model maps "
                                             "duplicate tags to one canonical slug, the "
                                             "newsroom cleanse applies the remap")
    ap.add_argument("--config", required=True)
    ap.add_argument("--run-id", help="Task id for artifacts and events")
    ap.add_argument("--dry-run", action="store_true",
                    help="Plan the map and print it; write nothing")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    if cfg is None:
        return 2
    cfg.run_dir.mkdir(parents=True, exist_ok=True)
    run_id = args.run_id or "temas-" + uuid.uuid4().hex[:12]
    art_dir = cfg.run_dir / run_id
    art_dir.mkdir(parents=True, exist_ok=True)
    emit_event(cfg.run_dir, {"event": "topics-start", "run_id": run_id})

    import httpx
    from src.adapter_api import ApiAdapter
    from src.render import parse_json_output, render

    backend = cfg.data["backend"]
    base = backend["base_url"].rstrip("/")
    headers = {"Authorization": f"Bearer {os.environ[backend['token_env']]}"}
    try:
        facets = httpx.get(f"{base}/articles:facets", headers=headers, timeout=30).json()
    except httpx.HTTPError as e:
        print(f"PublishError: facets: {e}", file=sys.stderr)
        emit_event(cfg.run_dir, {"event": "topics", "run_id": run_id,
                                 "status": "failed", "exit_code": 5, "error": str(e)[:300]})
        return 5
    board = facets.get("topics") or []
    result = {"status": "topics-cleansed", "run_id": run_id, "artifacts": str(art_dir),
              "tags_before": len(board), "tags_after": len(board),
              "articles_changed": 0, "applied": 0}
    if len(board) < 2:
        emit_event(cfg.run_dir, {"event": "topics", "run_id": run_id, "status": "published"})
        print(json.dumps(result, ensure_ascii=False))
        return 0

    listado = "\n".join(f"- {t['value']} ({t['count']} notas)" for t in board)
    prompt = render(Path(cfg.data["nodes"][0]["prompt_path"]).parent.joinpath(
        "topics-cleanse.md").read_text(), {"temas": listado})
    # One huge single call (the whole topic board): it gets a task-sized
    # timeout and a second attempt instead of the per-node default.
    adapter_cfg = dict(cfg.data["adapters"]["api"])
    adapter_cfg["timeout_s"] = max(900, int(adapter_cfg.get("timeout_s") or 0))
    adapter = ApiAdapter(adapter_cfg)
    from src.errors import PipelineError as _PE  # noqa: F401
    try:
        raw = adapter.complete(prompt, want_json=True)
    except PipelineError:
        raw = adapter.complete(prompt, want_json=True)
    (art_dir / "mapa.txt").write_text(raw)
    out = parse_json_output(raw)
    known = {t["value"] for t in board}
    mapa = {k: v.strip() for k, v in (out.get("mapa") or {}).items()
            if isinstance(v, str) and k in known and v.strip() and v.strip() != k}
    map_path = art_dir / "mapa.json"
    map_path.write_text(json.dumps(mapa, ensure_ascii=False, indent=1))
    if not mapa:
        emit_event(cfg.run_dir, {"event": "topics", "run_id": run_id, "status": "published"})
        print(json.dumps(result, ensure_ascii=False))
        return 0

    cleanse_cmd = [sys.executable, "-m", "newsroom", "topics", "cleanse",
                   "--map-file", str(map_path)] + ([] if args.dry_run else ["--apply"])
    proc = subprocess.run(cleanse_cmd, capture_output=True, text=True, timeout=600,
                          cwd=str(Path(__file__).resolve().parents[2]))
    (art_dir / "cleanse.txt").write_text(proc.stdout + proc.stderr)
    if proc.returncode != 0:
        emit_event(cfg.run_dir, {"event": "topics", "run_id": run_id, "status": "failed",
                                 "exit_code": proc.returncode,
                                 "error": (proc.stderr or proc.stdout)[:300]})
        print(f"PublishError: cleanse exit {proc.returncode}", file=sys.stderr)
        return 5
    summary = json.loads(proc.stdout.strip().splitlines()[-1])
    result.update({"tags_before": summary.get("tags_before", len(board)),
                   "tags_after": summary.get("tags_after", len(board)),
                   "articles_changed": summary.get("articles_changed", 0),
                   "applied": summary.get("applied", 0),
                   "dry_run": summary.get("dry_run", args.dry_run)})
    emit_event(cfg.run_dir, {"event": "topics", "run_id": run_id, "status": "published"})
    print(json.dumps(result, ensure_ascii=False))
    return 0


def main() -> int:
    subcommands = {"approve": cmd_approve, "events": cmd_events,
                   "batch": cmd_batch, "batch-approve": cmd_batch_approve,
                   "topics": cmd_topics}
    if len(sys.argv) > 1 and sys.argv[1] in subcommands:
        return subcommands[sys.argv[1]](sys.argv[2:])
    return cmd_run(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
