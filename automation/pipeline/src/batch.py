"""The daily batch: candidates per author, the jefe de redaccion selects, articles fan out.

Two-stage memory, curated: the candidates and the selection live in the batch plan
(plan.json, the batch's durable state; re-running the same batch id resumes it), while
each article runs the ordinary durable article workflow with its own run id, so a crashed
batch replays completed articles for free. Articles are WRITTEN in parallel (preview mode
holds each piece) and PUBLISHED sequentially in portada order: rank 1 publishes last, so
`published_at` descending puts it on top of the portada.
"""
import concurrent.futures as cf
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx

from .adapter_api import ApiAdapter
from .context import ContextFetcher
from .errors import AdapterError, ConfigError
from .registry import UsedUrls
from .render import parse_json_output, render

RUN_PY = str(Path(__file__).resolve().parents[1] / "run.py")


def _authors_with_sources(cfg: dict) -> list[dict]:
    """Every author that has attached sources and a beat: handle, beat."""
    backend = cfg["backend"]
    headers = {"Authorization": f"Bearer {os.environ[backend['token_env']]}"}
    base = backend["base_url"].rstrip("/")
    rows = httpx.get(f"{base}/authors", headers=headers, timeout=30).json()
    rows = rows.get("authors", rows) if isinstance(rows, dict) else rows
    out = []
    for a in rows:
        beat = (a.get("metadata") or {}).get("beat", "")
        try:
            attached = httpx.get(f"{base}/authors/{a['handle']}/sources",
                                 headers=headers, timeout=30).json().get("sources", [])
        except (httpx.HTTPError, ValueError):
            attached = []
        if beat and attached:
            out.append({"handle": a["handle"], "beat": beat})
    return out


def _call_node(cfg: dict, prompt_path: str, context: dict) -> tuple[dict, str]:
    prompt = render(Path(prompt_path).read_text(), context)
    raw = ApiAdapter(cfg["adapters"]["api"]).complete(prompt, want_json=True)
    return parse_json_output(raw), raw


def _candidates(cfg: dict, author: dict, batch_dir: Path, emit, directive: str = "") -> list[dict]:
    fetcher = ContextFetcher(cfg)
    handle = author["handle"]
    try:
        extra = fetcher.resolve(
            {"persona": {"persona": True},
             "titulares": {"feeds": cfg.get("batch", {}).get("feeds", {"hours": 48})}},
            {"author": handle}, {})
        out, raw = _call_node(cfg, cfg["batch"]["candidates_prompt_path"],
                              {"author": handle, "directiva": directive, **extra})
    except AdapterError as e:
        emit({"event": "batch-candidates", "run_id": batch_dir.name,
              "author": handle, "status": "failed", "error": str(e)[:300]})
        return []
    (batch_dir / f"candidates-{handle}.txt").write_text(raw)
    cands = [c for c in (out.get("candidatos") or [])
             if isinstance(c, dict) and c.get("titulo")]
    for c in cands:
        c["autor"] = handle
        c["beat"] = author["beat"]
    (batch_dir / f"candidates-{handle}.json").write_text(
        json.dumps(cands, ensure_ascii=False, indent=1))
    emit({"event": "batch-candidates", "run_id": batch_dir.name,
          "author": handle, "status": "previewed", "notes": f"{len(cands)} candidatas"})
    return cands


def _jefe(cfg: dict, candidates: list[dict], batch_dir: Path, directive: str = "") -> list[dict]:
    listing = "\n".join(
        f"- [{c['autor']}] {c['titulo']}: {c['descripcion']}" for c in candidates)
    recientes = ContextFetcher(cfg)._articles({"limit": 20})
    out, raw = _call_node(cfg, cfg["batch"]["jefe_prompt_path"],
                          {"candidatos": listing, "recientes": recientes,
                           "directiva": directive})
    (batch_dir / "jefe.txt").write_text(raw)
    by_author = {}
    for c in candidates:
        by_author.setdefault(c["autor"], []).append(c)
    selection = []
    for s in out.get("seleccion") or []:
        if not isinstance(s, dict) or s.get("autor") not in by_author or not s.get("titulo"):
            continue
        cand = next((c for c in by_author[s["autor"]]
                     if c["titulo"] == s.get("titulo")), by_author[s["autor"]][0])
        selection.append({"autor": s["autor"], "beat": cand["beat"],
                          "titulo": s["titulo"],
                          "descripcion": s.get("descripcion") or cand.get("descripcion", ""),
                          "fuente": cand.get("fuente", ""),
                          "portada_rank": int(s.get("portada_rank") or len(selection) + 1),
                          "imagen": bool(s.get("imagen")),
                          "imagen_brief": s.get("imagen_brief") or ""})
    if not selection:
        raise AdapterError("jefe: the selection came back empty")
    (batch_dir / "jefe.json").write_text(json.dumps(selection, ensure_ascii=False, indent=1))
    return selection


def _write_article(cfg_path: str, item: dict, run_id: str) -> dict:
    argv = [sys.executable, RUN_PY, "--config", cfg_path,
            "--topic", f"{item['titulo']}: {item['descripcion']}",
            "--author", item["autor"], "--section", item["beat"],
            "--run-id", run_id, "--mode", "preview"]
    if item.get("imagen") and item.get("imagen_brief"):
        argv += ["--image-brief", item["imagen_brief"]]
    p = subprocess.run(argv, capture_output=True, text=True, timeout=3600)
    result = {"run_id": run_id, "author": item["autor"], "title": item["titulo"],
              "portada_rank": item["portada_rank"]}
    try:
        result.update(json.loads(p.stdout.strip().splitlines()[-1]))
    except (ValueError, IndexError):
        result["status"] = "failed"
        result["error"] = (p.stderr or p.stdout)[-300:]
    return result


def _approve(cfg_path: str, run_id: str) -> dict:
    p = subprocess.run([sys.executable, RUN_PY, "approve", "--config", cfg_path,
                        "--run-id", run_id], capture_output=True, text=True, timeout=300)
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"status": "failed", "error": (p.stderr or p.stdout)[-300:]}


def run_batch(cfg: dict, cfg_path: str, batch_id: str, mode: str,
              authors: list[str] | None, emit, directive: str = "") -> dict:
    batch_cfg = cfg.get("batch")
    if not batch_cfg:
        raise ConfigError("batch: the config carries no batch block")
    batch_dir = Path(cfg["run_dir"]) / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)
    plan_path = batch_dir / "plan.json"

    if plan_path.is_file():
        selection = json.loads(plan_path.read_text())["seleccion"]
    else:
        roster = _authors_with_sources(cfg)
        if authors:
            roster = [a for a in roster if a["handle"] in authors]
        if not roster:
            raise ConfigError("batch: no authors with a beat and attached sources")
        candidates = []
        for author in roster:
            candidates.extend(_candidates(cfg, author, batch_dir, emit, directive))
        if not candidates:
            raise AdapterError("batch: no author produced any candidate")
        selection = _jefe(cfg, candidates, batch_dir, directive)
        plan_path.write_text(json.dumps({"seleccion": selection, "mode": mode,
                                         "directiva": directive},
                                        ensure_ascii=False, indent=1))
        emit({"event": "batch-plan", "run_id": batch_id, "status": "previewed",
              "notes": f"{len(selection)} notas elegidas de {len(candidates)} candidatas"})

    concurrency = batch_cfg.get("concurrency", 3)
    with cf.ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
        articles = list(ex.map(
            lambda pair: _write_article(cfg_path, pair[1], f"{batch_id}-n{pair[0]}"),
            enumerate(sorted(selection, key=lambda s: s["portada_rank"]), 1)))
    for art in articles:
        emit({"event": "batch-article", "run_id": art["run_id"], "author": art["author"],
              "status": art.get("status", "failed"),
              **({"error": art["error"]} if art.get("error") else {})})

    registry = UsedUrls(cfg["backend"], cap=batch_cfg.get("used_urls_cap", 50))
    for item in selection:
        if item.get("fuente"):
            try:
                registry.register(item["autor"], [item["fuente"]])
            except Exception as e:
                emit({"event": "batch-registry", "run_id": batch_id, "author": item["autor"],
                      "status": "failed", "error": str(e)[:200]})

    status = "batch-previewed"
    if mode == "auto":
        for art in sorted([a for a in articles if a.get("status") == "previewed"],
                          key=lambda a: -a["portada_rank"]):
            pub = _approve(cfg_path, art["run_id"])
            art.update(pub)
        status = "batch-published"
    result = {"status": status, "batch_id": batch_id, "artifacts": str(batch_dir),
              "articles": [{k: a.get(k) for k in
                            ("run_id", "author", "title", "portada_rank", "status",
                             "slug", "permalink") if a.get(k) is not None}
                           for a in articles]}
    (batch_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1))
    return result
