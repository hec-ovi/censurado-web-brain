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
def fetch_context(cfg: dict, node: dict, inputs: dict, outputs: dict) -> dict:
    return ContextFetcher(cfg).resolve(node["context"], inputs, outputs)


@DBOS.step(retries_allowed=True, max_attempts=3, interval_seconds=1.0, backoff_rate=2.0)
def run_node(cfg: dict, node: dict, prompt: str) -> str:
    adapter_cfg = cfg["adapters"][node["adapter"]]
    adapter = ApiAdapter(adapter_cfg) if node["adapter"] == "api" else CliAdapter(adapter_cfg)
    return adapter.complete(prompt, want_json=node["output"] == "json")


@DBOS.step(retries_allowed=True, max_attempts=3, interval_seconds=1.0, backoff_rate=2.0)
def publish_piece(cfg: dict, piece: dict, inputs: dict, run_id: str) -> dict:
    return Publisher(cfg["backend"]).publish(piece, inputs, idempotency_key=run_id)


def _verdict(out, raw: str) -> tuple[str, str]:
    if isinstance(out, dict):
        return str(out.get("verdict", "")).lower(), str(out.get("notes", ""))
    return "", raw[:200]


def _emit(art_dir: Path, name: str, raw: str, out) -> None:
    (art_dir / f"{name}.txt").write_text(raw)
    (art_dir / f"{name}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))


@DBOS.workflow()
def article_run(cfg: dict, inputs: dict) -> dict:
    run_id = DBOS.workflow_id
    art_dir = Path(cfg["run_dir"]) / run_id
    art_dir.mkdir(parents=True, exist_ok=True)
    context = dict(inputs)
    piece = None
    for node in cfg["nodes"]:
        extra = fetch_context(cfg, node, inputs, context) if node.get("context") else {}
        prompt = render(Path(node["prompt_path"]).read_text(), {**context, **extra})
        raw = run_node(cfg, node, prompt)
        out = parse_json_output(raw) if node["output"] == "json" else raw
        _emit(art_dir, node["name"], raw, out)
        context[node["name"]] = json.dumps(out, ensure_ascii=False) if isinstance(out, dict) else out
        if node["role"] == "draft":
            if not isinstance(out, dict) or not out.get("title") or not out.get("body"):
                raise AdapterError(f"draft node '{node['name']}' output misses title/body")
            piece = out
        if node["role"] == "gate":
            verdict, notes = _verdict(out, raw)
            respin = node.get("respin")
            passes = 0
            while verdict != "publish" and respin and passes < respin.get("passes", 2):
                passes += 1
                target = respin["target"]
                rw_prompt = render(Path(respin["prompt_path"]).read_text(),
                                   {**context, **extra, "notes": notes})
                rw_raw = run_node(cfg, node, rw_prompt)
                rw = parse_json_output(rw_raw)
                _emit(art_dir, f"{target}-respin-{passes}", rw_raw, rw)
                context[target] = json.dumps(rw, ensure_ascii=False)
                if any(n["name"] == target and n["role"] == "draft" for n in cfg["nodes"]):
                    piece = rw
                gate_prompt = render(Path(node["prompt_path"]).read_text(), {**context, **extra})
                raw = run_node(cfg, node, gate_prompt)
                out = parse_json_output(raw)
                _emit(art_dir, f"{node['name']}-respin-{passes}", raw, out)
                context[node["name"]] = json.dumps(out, ensure_ascii=False)
                verdict, notes = _verdict(out, raw)
            if verdict != "publish":
                return {"status": "rejected", "run_id": run_id, "notes": notes,
                        "artifacts": str(art_dir)}
    if inputs.get("mode", "preview") == "preview":
        (art_dir / "piece.json").write_text(json.dumps(
            {"piece": piece, "inputs": {k: inputs[k] for k in ("topic", "author", "section")}},
            ensure_ascii=False, indent=1))
        return {"status": "previewed", "run_id": run_id, "artifacts": str(art_dir),
                "piece": piece}
    pub = publish_piece(cfg, piece, inputs, run_id)
    return {"status": "published", "run_id": run_id, "artifacts": str(art_dir), **pub}
