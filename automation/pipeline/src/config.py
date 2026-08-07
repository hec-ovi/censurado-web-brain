"""Load and fail-closed validate the pipeline config (schema/pipeline-config.schema.json)."""
import json
import os
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
        api = adapters.get("api")
        if api is not None:
            for k in ("base_url", "model"):
                if not api.get(k):
                    v.append(f"adapters.api.{k}: required")
        cli = adapters.get("cli")
        if cli is not None:
            cmd = cli.get("cmd")
            if not isinstance(cmd, list) or not cmd:
                v.append("adapters.cli.cmd: required non-empty argv list")
            elif not cli.get("stdin") and not any("{prompt}" in a for a in cmd):
                v.append("adapters.cli.cmd: no element carries {prompt} (or set adapters.cli.stdin)")

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
            if n.get("adapter") not in ADAPTERS:
                v.append(f"{tag}.adapter: must be one of {sorted(ADAPTERS)}")
            elif n["adapter"] not in adapters:
                v.append(f"{tag}.adapter: '{n['adapter']}' is not configured under adapters")
            if n.setdefault("role", "plain") not in ROLES:
                v.append(f"{tag}.role: must be one of {sorted(ROLES)}")
            if n.setdefault("output", "text") not in OUTPUTS:
                v.append(f"{tag}.output: must be one of {sorted(OUTPUTS)}")
            drafts += n.get("role") == "draft"
            prompt = n.get("prompt")
            if not prompt:
                v.append(f"{tag}.prompt: required")
            else:
                pp = (base / prompt).resolve()
                if not pp.is_file():
                    v.append(f"{tag}.prompt: file not found: {pp}")
                n["prompt_path"] = str(pp)
        if nodes and drafts != 1:
            v.append(f"nodes: exactly one node must have role 'draft' (found {drafts})")

        if v:
            raise ConfigError("\n".join(v))
        return cls(raw)
