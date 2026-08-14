"""The durable workflow: walk the nodes, gate, publish once. Steps are the retry unit."""
import json
from pathlib import Path

from dbos import DBOS

from .adapter_api import ApiAdapter
from .adapter_cli import CliAdapter
from .context import ContextFetcher
from .errors import AdapterError
from .publisher import Publisher
from .render import parse_json_output, render
from .toolkit import run_verb


@DBOS.step(retries_allowed=True, max_attempts=3, interval_seconds=1.0, backoff_rate=2.0)
def fetch_context(cfg: dict, node: dict, inputs: dict, outputs: dict) -> dict:
    return ContextFetcher(cfg).resolve(node["context"], inputs, outputs)


@DBOS.step(retries_allowed=True, max_attempts=3, interval_seconds=1.0, backoff_rate=2.0)
def run_node(cfg: dict, node: dict, prompt: str) -> str:
    adapter_cfg = cfg["adapters"][node["adapter"]]
    kind = adapter_cfg.get("kind", node["adapter"])
    if kind == "api":
        if node.get("model"):
            adapter_cfg = {**adapter_cfg, "model": node["model"]}
        adapter = ApiAdapter(adapter_cfg)
    else:
        adapter = CliAdapter(adapter_cfg)
    raw = adapter.complete(prompt, want_json=node["output"] == "json")
    if node["output"] == "json":
        # Validate inside the retry unit: a draw that does not parse burns one
        # of the step's attempts instead of killing the run.
        parse_json_output(raw)
    return raw


@DBOS.step(retries_allowed=True, max_attempts=3, interval_seconds=1.0, backoff_rate=2.0)
def publish_piece(cfg: dict, piece: dict, inputs: dict, run_id: str) -> dict:
    return Publisher(cfg).publish(piece, inputs, idempotency_key=run_id)


@DBOS.step(retries_allowed=False)
def render_hero(cfg: dict, brief: str, alt: str) -> dict:
    """Best-effort hero render through the toolkit's `image` verb; {} when ComfyUI is
    absent or the render fails (the piece publishes text-only, like the CLI lane)."""
    out = run_verb(cfg, "image", "--prompt", brief, "--alt", alt, timeout=600)
    if out and out.get("image"):
        return {"image": out["image"], "image_alt": out.get("image_alt", alt)}
    return {}


_CORRECTOR_OWNS = ("titular", "titulo", "título", "bajada", "standfirst")


def _verdict(out, raw: str) -> tuple[str, str]:
    """The gate's decision. Canonical shape: typed observations, the CODE decides (any
    'bloqueante' blocks; 'pulido' rides to the corrector). A plain verdict/notes object
    is also honored. Observations about the titular or the bajada never block: the
    corrector rewrites both from the acta, so the reader never sees the draft's ones;
    the full observation list still reaches the corrector either way."""
    if isinstance(out, dict) and "observaciones" in out:
        obs = out.get("observaciones") or []
        blocking = [str(o.get("detalle", "")) for o in obs
                    if isinstance(o, dict) and o.get("nivel") == "bloqueante"
                    and not any(w in str(o.get("detalle", "")).lower()
                                for w in _CORRECTOR_OWNS)]
        return ("revise" if blocking else "publish"), "\n".join(blocking)
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
    if inputs.get("image_brief") and piece:
        piece.update(render_hero(cfg, inputs["image_brief"],
                                 piece.get("standfirst", "") or piece.get("title", "")))
    if inputs.get("mode", "preview") == "preview":
        (art_dir / "piece.json").write_text(json.dumps(
            {"piece": piece, "inputs": {k: inputs[k] for k in ("topic", "author", "section")}},
            ensure_ascii=False, indent=1))
        return {"status": "previewed", "run_id": run_id, "artifacts": str(art_dir),
                "piece": piece}
    pub = publish_piece(cfg, piece, inputs, run_id)
    return {"status": "published", "run_id": run_id, "artifacts": str(art_dir), **pub}
