"""Merge the panel's automation settings over the pipeline's file config.

The settings object (backend singleton, edited in the Automation tab's Models
section) carries two halves: `lanes` (endpoint/model per lane: `local` is the
pipeline's `api` adapter, `openrouter` the remote one) and `stages` (per-node
lane routing plus an optional model override). Every absent field keeps the
file's value, so empty settings derive a byte-equivalent config.
"""
import copy
import json
from pathlib import Path

# The remote lane's defaults when the file config carries no openrouter entry.
# The default model is the free Laguna tier, so an unconfigured remote lane
# never bills; pick a paid model in the panel's Models section when needed.
OPENROUTER_DEFAULTS = {
    "kind": "api",
    "base_url": "https://openrouter.ai/api/v1",
    "model": "poolside/laguna-s-2.1:free",
    "api_key_env": "OPENROUTER_API_KEY",
}

_LANE_ADAPTER = {"local": "api", "openrouter": "openrouter"}


def derive_config(base: dict, settings: dict) -> dict:
    """The effective pipeline config for one firing: base with settings merged."""
    cfg = copy.deepcopy(base)
    adapters = cfg.setdefault("adapters", {})
    lanes = settings.get("lanes") or {}
    stages = settings.get("stages") or {}

    local = lanes.get("local") or {}
    if local.get("base_url") or local.get("model"):
        entry = adapters.setdefault("api", {})
        if local.get("base_url"):
            entry["base_url"] = local["base_url"]
        if local.get("model"):
            entry["model"] = local["model"]

    remote = lanes.get("openrouter") or {}
    remote_used = bool(remote) or any(
        (s or {}).get("lane") == "openrouter" for s in stages.values())
    if remote_used:
        entry = adapters.setdefault("openrouter", dict(OPENROUTER_DEFAULTS))
        if remote.get("base_url"):
            entry["base_url"] = remote["base_url"]
        if remote.get("model"):
            entry["model"] = remote["model"]
        # The panel-stored key rides the derived config directly (it wins over
        # api_key_env in the adapter); it exists only in the runtime file.
        if remote.get("api_key"):
            entry["api_key"] = remote["api_key"]

    # The batch's own two editorial calls (each author's pitch, the jefe's selection) run on
    # `batch.adapter`, not on a node, so a lane choice has to reach them separately or the
    # edition would be decided on one lane and written on another.
    batch_lane = (settings.get("batch") or {}).get("lane")
    if batch_lane in _LANE_ADAPTER and cfg.get("batch") is not None:
        cfg["batch"]["adapter"] = _LANE_ADAPTER[batch_lane]

    for name, stage in stages.items():
        stage = stage or {}
        node = next((n for n in cfg.get("nodes", []) if n.get("name") == name), None)
        if node is None:
            continue
        lane = stage.get("lane")
        if lane in _LANE_ADAPTER:
            node["adapter"] = _LANE_ADAPTER[lane]
            # A lane switch without its own override runs on the lane's default
            # model, so a stale per-node model from the file must not stick.
            node.pop("model", None)
        if stage.get("model"):
            node["model"] = stage["model"]
    return cfg


def lane_settings(base: dict, lane: str, settings: dict | None = None) -> dict:
    """Settings that put EVERY stage on one lane, for a run that picks a lane
    instead of a per-stage routing.

    The panel's `lanes` half survives untouched, so each node runs that lane's
    saved endpoint, model and key. The `stages` half is replaced rather than
    merged: a stage model saved for the other lane would otherwise ride along
    and name a model this lane does not serve.
    """
    out = copy.deepcopy(settings or {})
    out["stages"] = {n["name"]: {"lane": lane}
                     for n in base.get("nodes", []) if n.get("name")}
    out["batch"] = {"lane": lane}
    return out


def write_derived(base_path: Path, cfg: dict, filename: str) -> Path:
    """Write a derived config BESIDE the base one (its relative `run_dir` and
    prompt paths resolve against the containing directory, so a sibling keeps
    them working) and return the new path. Callers own the filename: two writers
    sharing one would swap the config out from under each other's running batch.
    """
    out = base_path.with_name(filename)
    out.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    # The remote lane's key rides this file in plaintext, so it is owner-only.
    out.chmod(0o600)
    return out
