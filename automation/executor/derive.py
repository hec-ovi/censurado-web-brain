"""Merge the panel's automation settings over the pipeline's file config.

The settings object (backend singleton, edited in the Automation tab's Models
section) carries two halves: `lanes` (endpoint/model per lane: `local` is the
pipeline's `api` adapter, `openrouter` the remote one) and `stages` (per-node
lane routing plus an optional model override). Every absent field keeps the
file's value, so empty settings derive a byte-equivalent config.
"""
import copy

# The remote lane's defaults when the file config carries no openrouter entry.
OPENROUTER_DEFAULTS = {
    "kind": "api",
    "base_url": "https://openrouter.ai/api/v1",
    "model": "deepseek/deepseek-chat",
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
