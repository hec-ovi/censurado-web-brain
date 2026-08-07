"""The durable workflow: walk the nodes, gate, publish once. Steps are the retry unit."""
import json
import re
from pathlib import Path

from dbos import DBOS

from .adapter_api import ApiAdapter
from .adapter_cli import CliAdapter
from .context import ContextFetcher
from .errors import AdapterError
from .publisher import Publisher

_PLACEHOLDER = re.compile(r"\{([a-z0-9_-]+)\}")


def render(template: str, context: dict) -> str:
    return _PLACEHOLDER.sub(
        lambda m: str(context[m.group(1)]) if m.group(1) in context else m.group(0), template)


def parse_json_output(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b <= a:
        raise AdapterError("no JSON object in node output")
    try:
        return json.loads(text[a:b + 1])
    except json.JSONDecodeError as e:
        raise AdapterError(f"node output is not valid JSON: {e}") from e


@DBOS.step(retries_allowed=True, max_attempts=3, interval_seconds=1.0, backoff_rate=2.0)
def fetch_context(cfg: dict, node: dict, inputs: dict) -> dict:
    return ContextFetcher(cfg["backend"]).resolve(node["context"], inputs)


@DBOS.step(retries_allowed=True, max_attempts=3, interval_seconds=1.0, backoff_rate=2.0)
def run_node(cfg: dict, node: dict, prompt: str) -> str:
    adapter_cfg = cfg["adapters"][node["adapter"]]
    adapter = ApiAdapter(adapter_cfg) if node["adapter"] == "api" else CliAdapter(adapter_cfg)
    return adapter.complete(prompt, want_json=node["output"] == "json")


@DBOS.step(retries_allowed=True, max_attempts=3, interval_seconds=1.0, backoff_rate=2.0)
def publish_piece(cfg: dict, piece: dict, inputs: dict, run_id: str) -> dict:
    return Publisher(cfg["backend"]).publish(piece, inputs, idempotency_key=run_id)


@DBOS.workflow()
def article_run(cfg: dict, inputs: dict) -> dict:
    run_id = DBOS.workflow_id
    art_dir = Path(cfg["run_dir"]) / run_id
    art_dir.mkdir(parents=True, exist_ok=True)
    context = dict(inputs)
    piece = None
    for node in cfg["nodes"]:
        extra = fetch_context(cfg, node, inputs) if node.get("context") else {}
        prompt = render(Path(node["prompt_path"]).read_text(), {**context, **extra})
        raw = run_node(cfg, node, prompt)
        (art_dir / f"{node['name']}.txt").write_text(raw)
        out = parse_json_output(raw) if node["output"] == "json" else raw
        (art_dir / f"{node['name']}.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=1))
        context[node["name"]] = json.dumps(out, ensure_ascii=False) if isinstance(out, dict) else out
        if node["role"] == "draft":
            if not isinstance(out, dict) or not out.get("title") or not out.get("body"):
                raise AdapterError(f"draft node '{node['name']}' output misses title/body")
            piece = out
        if node["role"] == "gate":
            verdict = str(out.get("verdict", "")).lower() if isinstance(out, dict) else ""
            if verdict != "publish":
                return {"status": "rejected", "run_id": run_id,
                        "notes": out.get("notes", "") if isinstance(out, dict) else raw[:200],
                        "artifacts": str(art_dir)}
    pub = publish_piece(cfg, piece, inputs, run_id)
    return {"status": "published", "run_id": run_id, "artifacts": str(art_dir), **pub}
