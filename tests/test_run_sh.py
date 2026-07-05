"""The stack runner ./run.sh is the no-dependency entry point (bash + docker, no make,
no apt). These tests drive it with DRY_RUN=1 so it ECHOES the docker command instead of
running it, pinning that each verb maps to the right compose invocation (the comfyui
profile, the detach flag, the start bootstrap-if-missing). No docker is touched."""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUN = ROOT / "run.sh"


def _dry(*args):
    return subprocess.run(
        ["bash", str(RUN), *args],
        cwd=ROOT,
        env={"DRY_RUN": "1", "PATH": os.environ.get("PATH", "")},
        capture_output=True,
        text=True,
    )


def test_up_is_the_detached_fast_lane():
    p = _dry("up")
    assert p.returncode == 0
    assert "+ docker compose up -d" in p.stdout
    # the fast lane never selects the comfyui profile
    assert "--profile comfyui" not in p.stdout


def test_up_console_runs_in_the_foreground():
    p = _dry("up", "--console")
    assert p.returncode == 0
    assert "+ docker compose up" in p.stdout
    assert "-d" not in p.stdout, "--console must drop the detach flag"


def test_up_gpu_adds_the_comfyui_profile():
    p = _dry("up-gpu")
    assert p.returncode == 0
    assert "+ docker compose --profile comfyui up -d" in p.stdout


def test_comfyui_starts_only_comfyui():
    p = _dry("comfyui")
    assert p.returncode == 0
    assert "+ docker compose --profile comfyui up -d comfyui" in p.stdout


def test_start_ensures_env_and_secrets_then_ups():
    p = _dry("start")
    assert p.returncode == 0
    assert "ensure .env" in p.stdout
    assert "ensure secrets" in p.stdout
    assert "+ docker compose up -d" in p.stdout


def test_unknown_command_exits_nonzero():
    p = _dry("frobnicate")
    assert p.returncode != 0
    assert "unknown command" in p.stderr


def test_help_lists_the_verbs():
    p = _dry("help")
    assert p.returncode == 0
    for verb in ("start", "up", "up-gpu", "comfyui", "down"):
        assert verb in p.stdout
