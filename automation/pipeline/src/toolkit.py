"""Run censurado.py toolkit verbs as subprocesses (the pipeline's hands on the stack)."""
import json
import subprocess
import sys
from pathlib import Path

_DEFAULT_CLI = str(Path(__file__).resolve().parents[3] / "cli" / "censurado.py")


def toolkit_cmd(cfg: dict) -> list[str]:
    return cfg.get("toolkit", {}).get("cmd") or [sys.executable, _DEFAULT_CLI]


def run_verb(cfg: dict, *args: str, timeout: float | None = None) -> dict | None:
    """One toolkit verb; parsed stdout JSON, or None when the verb failed or printed none
    (the toolkit's best-effort verbs exit 0 with an empty stdout when they skip)."""
    t = timeout or cfg.get("toolkit", {}).get("timeout_s", 60)
    try:
        p = subprocess.run([*toolkit_cmd(cfg), *args],
                           capture_output=True, text=True, timeout=t)
        if p.returncode != 0 or not p.stdout.strip():
            return None
        out = json.loads(p.stdout)
        return out if isinstance(out, dict) else None
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None
