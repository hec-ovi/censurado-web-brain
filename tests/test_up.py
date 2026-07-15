"""The stack spin-up verbs (`up` / `up-gpu`): the ONE sanctioned way an agent starts the
stack. Two layers, no docker touched anywhere:

1. Real entry point, dry: `python3 cli/censurado.py up` with DRY_RUN=1 runs main() through
   the REAL run.sh, which echoes the docker command instead of executing it, pinning that
   each verb maps to the right compose lane (fast lane vs the comfyui profile).
2. Hermetic verb logic: `cmd_up` with a captured fake subprocess.run, pinning the argv/cwd
   contract, the failure relay (exit 1 + "STOP" guidance, never a traceback), and the
   missing-runner ToolError.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "cli" / "censurado.py"


def _load():
    spec = importlib.util.spec_from_file_location("censurado_up", CLI)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cz = _load()


# ----- layer 1: the real CLI entry point, DRY_RUN through the real run.sh -----


def _cli_dry(*args):
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=ROOT,
        env={"DRY_RUN": "1", "PATH": os.environ.get("PATH", "")},
        capture_output=True,
        text=True,
    )


def test_up_verb_drives_the_fast_lane_through_run_sh():
    p = _cli_dry("up")
    assert p.returncode == 0, p.stderr
    assert "+ docker compose up -d" in p.stdout
    assert "--profile comfyui" not in p.stdout, "plain `up` must never select the image lane"
    assert "up OK" in p.stdout
    # the success text keeps the local/public boundary explicit for the driving agent
    assert "publicar --yes" in p.stdout


def test_up_gpu_verb_selects_the_comfyui_profile():
    p = _cli_dry("up-gpu")
    assert p.returncode == 0, p.stderr
    assert "--profile comfyui" in p.stdout


def test_help_names_both_lanes():
    p = _cli_dry("--help")
    assert p.returncode == 0
    assert "up" in p.stdout and "up-gpu" in p.stdout


# ----- layer 2: hermetic cmd_up logic (captured fake subprocess.run) -----


def _capture_run(monkeypatch, returncode=0, raises=None):
    calls = {}

    def fake_run(argv, cwd=None, check=None):
        calls.update(argv=argv, cwd=cwd)
        if raises is not None:
            raise raises
        return SimpleNamespace(returncode=returncode)

    monkeypatch.setattr(cz.subprocess, "run", fake_run)
    return calls


def test_cmd_up_invokes_run_sh_up_from_the_repo_root(monkeypatch, capsys):
    calls = _capture_run(monkeypatch)
    assert cz.cmd_up(SimpleNamespace(gpu=False)) == 0
    assert calls["argv"][0] == "bash" and calls["argv"][-1] == "up"
    assert calls["argv"][1].endswith("run.sh")
    assert calls["cwd"] == str(cz._REPO)


def test_cmd_up_gpu_invokes_the_up_gpu_lane(monkeypatch, capsys):
    calls = _capture_run(monkeypatch)
    assert cz.cmd_up(SimpleNamespace(gpu=True)) == 0
    assert calls["argv"][-1] == "up-gpu"
    assert "ComfyUI" in capsys.readouterr().out


def test_cmd_up_failure_relays_and_stops(monkeypatch, capsys):
    _capture_run(monkeypatch, returncode=3)
    assert cz.cmd_up(SimpleNamespace(gpu=False)) == 1
    err = capsys.readouterr().err
    assert "up FAILED" in err and "STOP" in err
    assert "Do NOT retry" in err


def test_cmd_up_launch_error_is_a_clean_tool_error(monkeypatch):
    _capture_run(monkeypatch, raises=OSError("no bash"))
    with pytest.raises(cz.ToolError):
        cz.cmd_up(SimpleNamespace(gpu=False))


def test_cmd_up_missing_runner_fails_clean(monkeypatch):
    monkeypatch.setattr(cz, "_REPO", Path("/nonexistent-censurado-root"))
    with pytest.raises(cz.ToolError):
        cz.cmd_up(SimpleNamespace(gpu=False))
