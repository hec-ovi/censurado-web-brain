"""Load and fail-closed validate the pipeline config (schema/pipeline-config.schema.json)."""
import json
import os
import re
from pathlib import Path

from .errors import ConfigError

ADAPTERS = {"api", "cli"}
ROLES = {"draft", "gate", "plain"}
OUTPUTS = {"json", "text"}


class PipelineConfig:
    """The validated config; `data` is the plain dict handed to the workflow."""

    def __init__(self, data: dict):
        self.data = data

    @property
    def run_dir(self) -> Path:
        return Path(self.data["run_dir"])

    @classmethod
    def load(cls, path: str) -> "PipelineConfig":
        p = Path(path)
        try:
            raw = json.loads(p.read_text())
        except OSError as e:
            raise ConfigError(f"cannot read config: {e}") from e
        except json.JSONDecodeError as e:
            raise ConfigError(f"config is not JSON: {e}") from e

        v: list[str] = []
        base = p.resolve().parent

        run_dir = raw.get("run_dir")
        if not isinstance(run_dir, str) or not run_dir:
            v.append("run_dir: required string")
        else:
            raw["run_dir"] = str((base / run_dir).resolve())

        backend = raw.get("backend")
        if not isinstance(backend, dict):
            v.append("backend: required object")
        else:
            if not backend.get("base_url"):
                v.append("backend.base_url: required")
            token_env = backend.get("token_env")
            if not token_env:
                v.append("backend.token_env: required")
            elif not os.environ.get(token_env):
                v.append(f"backend.token_env: env var {token_env} is not set")

        adapters = raw.get("adapters")
        if not isinstance(adapters, dict):
            v.append("adapters: required object")
            adapters = {}
        # An adapter entry's KIND (api|cli) defaults to its name; extra entries
        # (e.g. "openrouter") declare theirs, so several api endpoints can coexist.
        kinds: dict[str, str] = {}
        for aname, acfg in adapters.items():
            if not isinstance(acfg, dict):
                v.append(f"adapters.{aname}: must be an object")
                continue
            kind = acfg.get("kind", aname)
            if kind not in ADAPTERS:
                v.append(f"adapters.{aname}.kind: must be one of {sorted(ADAPTERS)}")
                continue
            kinds[aname] = kind
            if kind == "api":
                for k in ("base_url", "model"):
                    if not acfg.get(k):
                        v.append(f"adapters.{aname}.{k}: required")
            else:
                cmd = acfg.get("cmd")
                if not isinstance(cmd, list) or not cmd:
                    v.append(f"adapters.{aname}.cmd: required non-empty argv list")
                elif not acfg.get("stdin") and not any("{prompt}" in a for a in cmd):
                    v.append(f"adapters.{aname}.cmd: no element carries {{prompt}} (or set adapters.{aname}.stdin)")

        for block in ("websearch", "toolkit"):
            block_cfg = raw.get(block)
            if block_cfg is not None:
                bc = block_cfg.get("cmd")
                if bc is not None and (not isinstance(bc, list) or not bc):
                    v.append(f"{block}.cmd: must be a non-empty argv list")

        batch = raw.get("batch")
        if batch is not None:
            for key, default in (("candidates_prompt", "prompts/candidates.md"),
                                 ("jefe_prompt", "prompts/jefe.md")):
                bp = (base / batch.get(key, default)).resolve()
                if not bp.is_file():
                    v.append(f"batch.{key}: file not found: {bp}")
                else:
                    batch[key.replace("_prompt", "_prompt_path")] = str(bp)
            for key, floor in (("concurrency", 1), ("used_urls_cap", 1)):
                val = batch.get(key)
                if val is not None and (not isinstance(val, int) or val < floor):
                    v.append(f"batch.{key}: must be an integer >= {floor}")
            adapter = batch.get("adapter")
            if adapter is not None:
                entry = (raw.get("adapters") or {}).get(adapter)
                if entry is None:
                    v.append(f"batch.adapter: no adapter named {adapter!r}")
                elif (entry.get("kind") or adapter) != "api" and adapter != "api":
                    v.append(f"batch.adapter: {adapter!r} is not an api-kind adapter")

        nodes = raw.get("nodes")
        if not isinstance(nodes, list) or not nodes:
            v.append("nodes: required non-empty list")
            nodes = []
        names: set[str] = set()
        drafts = 0
        for i, n in enumerate(nodes):
            tag = f"nodes[{i}]"
            if not isinstance(n, dict):
                v.append(f"{tag}: must be an object")
                continue
            name = n.get("name")
            if not name:
                v.append(f"{tag}.name: required")
            elif name in names:
                v.append(f"{tag}.name: duplicate '{name}'")
            else:
                names.add(name)
            if n.get("adapter") not in adapters:
                v.append(f"{tag}.adapter: '{n.get('adapter')}' is not configured under adapters")
            model = n.get("model")
            if model is not None:
                if not isinstance(model, str) or not model:
                    v.append(f"{tag}.model: must be a non-empty string")
                elif kinds.get(n.get("adapter")) == "cli":
                    v.append(f"{tag}.model: only an api-kind adapter takes a model override")
            if n.setdefault("role", "plain") not in ROLES:
                v.append(f"{tag}.role: must be one of {sorted(ROLES)}")
            if n.setdefault("output", "text") not in OUTPUTS:
                v.append(f"{tag}.output: must be one of {sorted(OUTPUTS)}")
            drafts += n.get("role") == "draft"
            respin = n.get("respin")
            if respin is not None:
                rtag = f"{tag}.respin"
                if n.get("role") != "gate":
                    v.append(f"{rtag}: only a gate node can respin")
                elif not isinstance(respin, dict):
                    v.append(f"{rtag}: must be an object")
                else:
                    target = respin.get("target")
                    if not isinstance(target, str) or target == name or target not in names:
                        v.append(f"{rtag}.target: must name an earlier node")
                    passes = respin.setdefault("passes", 2)
                    if not isinstance(passes, int) or passes < 1:
                        v.append(f"{rtag}.passes: must be an integer >= 1")
                    rp = respin.get("prompt")
                    if not rp:
                        v.append(f"{rtag}.prompt: required")
                    else:
                        rpp = (base / rp).resolve()
                        if not rpp.is_file():
                            v.append(f"{rtag}.prompt: file not found: {rpp}")
                        else:
                            respin["prompt_path"] = str(rpp)
            prompt = n.get("prompt")
            if not prompt:
                v.append(f"{tag}.prompt: required")
            else:
                pp = (base / prompt).resolve()
                if not pp.is_file():
                    v.append(f"{tag}.prompt: file not found: {pp}")
                n["prompt_path"] = str(pp)
            ctx = n.get("context")
            if ctx is not None:
                if not isinstance(ctx, dict):
                    v.append(f"{tag}.context: must be an object")
                    continue
                for ck, cs in ctx.items():
                    ctag = f"{tag}.context.{ck}"
                    if not re.match(r"^[a-z0-9_-]+$", ck):
                        v.append(f"{ctag}: placeholder name must be [a-z0-9_-]+")
                    if not isinstance(cs, dict) or len(cs) != 1:
                        v.append(f"{ctag}: must be exactly one of file|persona|editorial|feeds|websearch|topics|articles")
                        continue
                    (kind, val), = cs.items()
                    if kind == "file":
                        fp = (base / val).resolve()
                        if not fp.is_file():
                            v.append(f"{ctag}.file: not found: {fp}")
                        else:
                            cs["file"] = str(fp)
                    elif kind == "persona":
                        if val is not True:
                            v.append(f"{ctag}.persona: must be true")
                    elif kind == "editorial":
                        if not isinstance(val, str) or not val:
                            v.append(f"{ctag}.editorial: language code required")
                    elif kind == "feeds":
                        if not isinstance(val, dict):
                            v.append(f"{ctag}.feeds: must be an object (may be empty)")
                    elif kind == "topics":
                        if val is not True:
                            v.append(f"{ctag}.topics: must be true")
                    elif kind == "articles":
                        if not isinstance(val, dict):
                            v.append(f"{ctag}.articles: must be an object (may be empty)")
                    elif kind == "websearch":
                        if not isinstance(val, dict) or not isinstance(val.get("queries_from"), str):
                            v.append(f"{ctag}.websearch: queries_from (a prior node name) is required")
                        else:
                            for field in ("queries_from", "urls_from"):
                                ref = val.get(field)
                                if ref is not None and (ref == name or ref not in names):
                                    v.append(f"{ctag}.websearch.{field}: "
                                             f"'{ref}' is not an earlier node")
                    else:
                        v.append(f"{ctag}: unknown source '{kind}'")
        if nodes and drafts != 1:
            v.append(f"nodes: exactly one node must have role 'draft' (found {drafts})")

        if v:
            raise ConfigError("\n".join(v))
        return cls(raw)
