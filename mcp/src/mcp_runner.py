"""The CLI seam: run one `censurado.py` verb and return a schema-shaped result envelope.

Every content operation this layer exposes is a verb of the operator CLI, run as a child
process. Nothing here re-implements publishing, payload building, or auth: the CLI stays the
single writer, so an MCP agent and a human at the terminal go through the exact same code and
cannot drift apart. What this module adds is the envelope: an agent-readable object with the
exit code, both streams, and the parsed JSON when the verb printed JSON.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(os.environ.get("CENSURADO_REPO", str(Path(__file__).resolve().parents[2])))
CLI = REPO / "cli" / "censurado.py"

# The walk's scratch dir. The CLI hands the rendered hero from `image` to the next `preview`
# through this dir (image.json), and the step gate enforces its artifacts here, so the server
# pins ONE dir and exports it to every child: an agent that cannot touch the filesystem still
# gets the same handoff a terminal operator gets.
WORK_DIR = Path(os.environ.get("CENSURADO_WORK", "").strip() or str(REPO / ".mcp-work"))

DEFAULT_TIMEOUT = int(os.environ.get("CENSURADO_MCP_TIMEOUT", "180"))
# Bringing the stack up builds images on a cold host, and a deploy compiles the whole site.
# Both are minutes-scale, so they carry their own ceiling rather than the per-call default.
SLOW_TIMEOUT = int(os.environ.get("CENSURADO_MCP_SLOW_TIMEOUT", "1800"))

_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


class RunnerError(Exception):
    """A failure of the seam itself (no CLI, unwritable scratch): not an agent input error."""


class CliRunner:
    """Runs `censurado.py <verb> ...` in the repo and returns result envelopes."""

    def __init__(self, repo: Path = REPO, work_dir: Path = WORK_DIR,
                 python: str | None = None, timeout: int = DEFAULT_TIMEOUT):
        self.repo = Path(repo)
        self.cli = self.repo / "cli" / "censurado.py"
        self.work_dir = Path(work_dir)
        self.python = python or os.environ.get("CENSURADO_PYTHON") or sys.executable
        self.timeout = timeout

    # -- child environment ---------------------------------------------------

    def child_env(self) -> dict:
        env = dict(os.environ)
        env["CENSURADO_WORK"] = str(self.work_dir)
        env["PYTHONUNBUFFERED"] = "1"
        return env

    def tmp_dir(self) -> Path:
        d = self.work_dir / ".tmp"
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RunnerError(f"cannot create the scratch dir {d}: {exc}") from exc
        return d

    def write_temp(self, text: str, suffix: str = ".md") -> Path:
        """Park a long argument (an article body, a persona JSON) in a file for the CLI's
        --*-file flags. An MCP agent has no filesystem, so the server is what turns its
        string argument into the file the CLI reads."""
        fd, name = tempfile.mkstemp(suffix=suffix, dir=str(self.tmp_dir()))
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        return Path(name)

    def write_temp_bytes(self, blob: bytes, filename: str) -> Path:
        if not _SAFE_NAME.match(filename or ""):
            raise RunnerError(
                f"filename {filename!r} must be a plain name like hero.png "
                "(letters, digits, dot, dash, underscore; no path separators)")
        path = self.tmp_dir() / filename
        path.write_bytes(blob)
        return path

    # -- the call ------------------------------------------------------------

    def run(self, argv: list, timeout: int | None = None) -> dict:
        """One verb. Returns the envelope; never raises on a non-zero exit (a failing verb is
        an agent-correctable result, not a server fault)."""
        if not self.cli.is_file():
            raise RunnerError(
                f"the operator CLI is missing at {self.cli}. This MCP server must run from a "
                "checkout of censurado-web-brain (set CENSURADO_REPO to point at it).")
        cmd = [self.python, str(self.cli)] + [str(a) for a in argv]
        try:
            proc = subprocess.run(cmd, cwd=str(self.repo), env=self.child_env(),
                                  capture_output=True, text=True,
                                  timeout=timeout or self.timeout)
        except subprocess.TimeoutExpired as exc:
            secs = timeout or self.timeout
            return envelope(argv, 124, exc.stdout or "", (exc.stderr or "") +
                            f"\nTIMEOUT: `{argv[0]}` did not finish within {secs}s.")
        return envelope(argv, proc.returncode, proc.stdout, proc.stderr)


def envelope(argv: list, exit_code: int, stdout: str, stderr: str) -> dict:
    """The one result shape every CLI-backed tool returns. `data` appears only when the verb
    printed JSON, so a caller reads structured output where there is some and plain text
    otherwise, without guessing which verbs are which."""
    out = {
        "ok": exit_code == 0,
        "verb": str(argv[0]) if argv else "",
        "exit_code": int(exit_code),
        "stdout": stdout or "",
        "stderr": stderr or "",
    }
    parsed = _maybe_json(out["stdout"])
    if parsed is not None:
        out["data"] = parsed
    return out


def _maybe_json(text: str):
    """Parse stdout as JSON when the verb printed JSON. Several verbs print a JSON document
    followed by human lines (`preview` prints the response then PREVIEW:/NEWEST:), so a plain
    json.loads of the whole stream is not enough: fall back to the leading JSON value."""
    s = (text or "").strip()
    if not s or s[0] not in "{[":
        return None
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    try:
        value, _end = json.JSONDecoder().raw_decode(s)
    except ValueError:
        return None
    return value


ENVELOPE_KEYS = {"ok": bool, "verb": str, "exit_code": int, "stdout": str, "stderr": str}


def validate_envelope(value) -> None:
    """Fail closed on the way out: an off-contract envelope is a bug in this layer, and it
    must not reach the agent as if it were data. Mirrors schema/tool-result.schema.json."""
    if not isinstance(value, dict):
        raise RunnerError(f"result envelope must be an object, got {type(value).__name__}")
    for key, typ in ENVELOPE_KEYS.items():
        if key not in value:
            raise RunnerError(f"result envelope is missing {key!r}")
        if not isinstance(value[key], typ) or (typ is int and isinstance(value[key], bool)):
            raise RunnerError(f"result envelope field {key!r} must be a {typ.__name__}")
    extra = set(value) - set(ENVELOPE_KEYS) - {"data"}
    if extra:
        raise RunnerError(f"result envelope carries unknown field(s): {', '.join(sorted(extra))}")
