#!/usr/bin/env python3
"""Run one durable article pipeline. Contract: CONTRACT.md; result JSON on stdout."""
import argparse
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.config import PipelineConfig            # noqa: E402
from src.errors import ConfigError, PipelineError  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Run one article through the durable pipeline")
    ap.add_argument("--config", required=True)
    ap.add_argument("--topic", required=True)
    ap.add_argument("--author", required=True)
    ap.add_argument("--section", required=True)
    ap.add_argument("--run-id", help="Durability key; reuse to resume/replay a run")
    args = ap.parse_args()

    try:
        cfg = PipelineConfig.load(args.config)
    except ConfigError as e:
        print(f"CONFIG_INVALID:\n{e}", file=sys.stderr)
        return 2

    cfg.run_dir.mkdir(parents=True, exist_ok=True)
    from dbos import DBOS, SetWorkflowID
    DBOS(config={"name": "censurado-pipeline", "log_level": "WARNING",
                 "system_database_url": f"sqlite:///{cfg.run_dir / '.dbos.sqlite'}"})
    import src.flow as flow
    DBOS.launch()

    run_id = args.run_id or "run-" + uuid.uuid4().hex[:12]
    inputs = {"topic": args.topic, "author": args.author, "section": args.section}
    try:
        with SetWorkflowID(run_id):
            handle = DBOS.start_workflow(flow.article_run, cfg.data, inputs)
        result = handle.get_result()
    except Exception as e:
        for err in (e, e.__cause__):
            if isinstance(err, PipelineError):
                print(f"{type(err).__name__}: {err}", file=sys.stderr)
                return err.exit_code
        print(f"pipeline failed: {e}", file=sys.stderr)
        return 4 if "AdapterError" in str(e) or "MaxStepRetries" in type(e).__name__ else 1
    finally:
        DBOS.destroy()

    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "published" else 3


if __name__ == "__main__":
    sys.exit(main())
