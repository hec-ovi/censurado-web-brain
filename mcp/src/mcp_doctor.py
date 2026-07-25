"""The preflight: does the whole stack actually work, end to end?

`stack_status` answers "are the ports answering". This answers the harder question an agent
needs before it starts a shift: can I read the content plane with the token I have, can the
image lane render, is the deploy lane wired, is the editorial recipe on disk. Every check runs
through the same path the tools use (a CLI verb) rather than a private shortcut, so a green
doctor means the tools work, not that a parallel implementation works.

Nothing here raises. A dead service, a missing binary, and an unparseable response are all
reported as rows, because a doctor that crashes on the first fault tells you the least when
you need it most.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from mcp_runner import SLOW_TIMEOUT, CliRunner, RunnerError

OK, WARN, FAIL = "OK", "WARN", "FAIL"

# A 1x1 transparent PNG: the smallest real image that proves the media store accepts an
# upload and hands back a URL.
_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")

_DEEP_IMAGE_PROMPT = ("a plain grey studio backdrop, soft even light, no text, no people, "
                      "documentary still")


class Doctor:
    def __init__(self, runner: CliRunner | None = None):
        self.runner = runner or CliRunner()
        self.rows: list = []

    # -- row helpers ---------------------------------------------------------

    def add(self, group: str, check: str, level: str, detail: str) -> None:
        self.rows.append({"group": group, "check": check, "level": level, "detail": detail})

    def _run(self, argv, timeout=None):
        try:
            return self.runner.run(argv, timeout=timeout)
        except RunnerError as exc:
            return {"ok": False, "verb": argv[0] if argv else "", "exit_code": 127,
                    "stdout": "", "stderr": str(exc)}

    @staticmethod
    def _tail(env: dict, limit: int = 300) -> str:
        text = (env.get("stderr") or env.get("stdout") or "").strip().splitlines()
        return text[-1][:limit] if text else f"exit {env.get('exit_code')}"

    # -- groups --------------------------------------------------------------

    def check_config(self) -> None:
        repo, cli = self.runner.repo, self.runner.cli
        self.add("config", "cli", OK if cli.is_file() else FAIL,
                 f"operator CLI at {cli}" if cli.is_file()
                 else f"no operator CLI at {cli}: point CENSURADO_REPO at a censurado-web-brain checkout")
        env_file = repo / ".env"
        self.add("config", "env-file", OK if env_file.is_file() else FAIL,
                 f"{env_file} present" if env_file.is_file()
                 else f"no {env_file}: run ./bootstrap.sh on the host to mint the secrets")
        has_token = _env_present("NEWSROOM_OPERATOR_TOKEN", repo)
        self.add("config", "operator-token", OK if has_token else FAIL,
                 "operator token configured (value never read back)" if has_token
                 else "NEWSROOM_OPERATOR_TOKEN is not set in the environment or .env: "
                      "every content write will refuse")
        try:
            self.runner.tmp_dir()
            self.add("config", "scratch", OK, f"scratch dir writable at {self.runner.work_dir}")
        except RunnerError as exc:
            self.add("config", "scratch", FAIL, str(exc))

    def check_services(self) -> None:
        env = self._run(["status", "--json"])
        data = env.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("services"), dict):
            self.add("services", "probe", FAIL,
                     f"the status verb returned no verdict: {self._tail(env)}")
            return
        for name, svc in sorted(data["services"].items()):
            up, core = bool(svc.get("up")), bool(svc.get("core"))
            detail = f"{svc.get('url', '')} (HTTP {svc.get('status')})"
            if up:
                self.add("services", name, OK, detail)
            elif core:
                self.add("services", name, FAIL, detail + " is DOWN: bring it up with stack_up")
            else:
                self.add("services", name, WARN, detail + " is down (optional lane)")

    def check_content_plane(self) -> None:
        """The authenticated reads every content tool depends on. A 401 here is the single
        most common cause of "the agent can see the site but cannot change anything"."""
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        probes = (
            ("authors", ["personas"], "author registry"),
            ("sources", ["portals"], "source catalog"),
            ("articles", ["archive", "--limit", "1"], "article read API"),
            ("recomendado", ["recomendado"], "front-page rail"),
            ("portadas", ["portada", day], "front-page plans"),
        )
        for check, argv, label in probes:
            env = self._run(argv)
            if env.get("ok"):
                self.add("content", check, OK, f"{label} readable")
            else:
                self.add("content", check, FAIL, f"{label} unreadable: {self._tail(env)}")

    def check_image_lane(self, deep: bool) -> None:
        env = self._run(["image", "--prompt", "doctor probe", "--dry-run"])
        if env.get("ok") and isinstance(env.get("data"), dict):
            self.add("image", "graph", OK, "the ComfyUI render graph builds from the template")
        else:
            self.add("image", "graph", FAIL,
                     f"the render template does not build a graph: {self._tail(env)}")
        if not deep:
            self.add("image", "render", WARN,
                     "not rendered (run doctor with deep=true to render and upload a real image)")
            return
        env = self._run(["image", "--prompt", _DEEP_IMAGE_PROMPT, "--alt", "doctor probe",
                         "--width", "512", "--height", "512", "--steps", "4"],
                        timeout=SLOW_TIMEOUT)
        data = env.get("data") if isinstance(env.get("data"), dict) else {}
        url = data.get("image", "")
        if env.get("ok") and url:
            self.add("image", "render", OK,
                     f"rendered and uploaded a hero ({data.get('bytes', 0)} bytes) to {url}")
        else:
            # `image` exits 0 with IMAGE SKIPPED when ComfyUI is unreachable, so an ok exit
            # with no url still means the lane cannot produce a hero.
            self.add("image", "render", FAIL, f"no image produced: {self._tail(env)}")

    def check_media_store(self, deep: bool) -> None:
        if not deep:
            self.add("media", "upload", WARN,
                     "not exercised (run doctor with deep=true to upload a probe image)")
            return
        try:
            path = self.runner.write_temp_bytes(_PIXEL_PNG, "doctor-probe.png")
        except RunnerError as exc:
            self.add("media", "upload", FAIL, str(exc))
            return
        try:
            env = self._run(["media", str(path)])
        finally:
            path.unlink(missing_ok=True)
        data = env.get("data") if isinstance(env.get("data"), dict) else {}
        url = data.get("url", "")
        if not (env.get("ok") and url):
            self.add("media", "upload", FAIL, f"media upload failed: {self._tail(env)}")
            return
        self.add("media", "upload", OK, f"uploaded a probe image to {url}")
        base = os.environ.get("CENSURADO_PUBLISH", "http://127.0.0.1:8082").rstrip("/")
        code, detail = _probe(base + url if url.startswith("/") else url)
        self.add("media", "readback", OK if code == 200 else WARN,
                 f"GET {url} -> {detail}")

    def check_deploy_lane(self, deep: bool) -> None:
        repo = self.runner.repo
        script = repo / "deploy" / "deploy-cdn.sh"
        self.add("deploy", "script", OK if script.is_file() else FAIL,
                 f"{script} present" if script.is_file() else f"no deploy script at {script}")
        for binary, argv, why in (
            ("docker", ["docker", "--version"], "the deploy compiles the site in a container"),
            ("node", ["node", "--version"], "wrangler runs on Node.js 20+"),
        ):
            found, detail = _binary(argv)
            self.add("deploy", binary, OK if found else FAIL,
                     detail if found else f"{binary} not runnable ({detail}): {why}")
        acct = _env_present("CLOUDFLARE_ACCOUNT_ID", repo)
        self.add("deploy", "account-id", OK if acct else FAIL,
                 "CLOUDFLARE_ACCOUNT_ID is set" if acct
                 else "CLOUDFLARE_ACCOUNT_ID is unset, so the deploy script exits before it "
                      "publishes: put it in .env")
        if _env_present("D1_REACTIONS_ID", repo) and not _env_present("REACTIONS_SALT", repo):
            self.add("deploy", "reactions", FAIL,
                     "D1_REACTIONS_ID is set but REACTIONS_SALT is empty, which the deploy "
                     "script refuses: mint a salt and keep it stable")
        else:
            self.add("deploy", "reactions", OK, "the reactions binding is coherent")
        if not deep:
            self.add("deploy", "auth", WARN,
                     "wrangler login not checked (run doctor with deep=true to verify it)")
            return
        found, detail = _binary(["npx", "-y", "wrangler", "whoami"], timeout=120)
        if found and "not authenticated" not in detail.lower():
            self.add("deploy", "auth", OK, "wrangler is authenticated")
        else:
            self.add("deploy", "auth", FAIL,
                     f"wrangler cannot publish: {detail}. The human runs `wrangler login` once "
                     "on this host; an agent cannot do it.")

    def check_recipe(self) -> None:
        """Fold in the CLI's own self-check: the skill package and the on-disk recipe files
        the editorial verbs read. Owned there, mirrored here so one call covers the stack."""
        env = self._run(["doctor"])
        rows = [line.strip() for line in (env.get("stdout") or "").splitlines()
                if line.strip().startswith("[")]
        seen = 0
        for line in rows:
            level, _, detail = line.partition("]")
            level = level.lstrip("[").strip()
            detail = detail.strip()
            if level not in (OK, WARN, FAIL) or detail.startswith("---"):
                continue
            # The service rows are already covered by check_services with better detail.
            if detail.startswith(("publish backend", "site ", "ComfyUI")):
                continue
            seen += 1
            self.add("recipe", f"cli-{seen}", level, detail)
        if not seen:
            self.add("recipe", "cli-doctor", FAIL,
                     f"the CLI self-check produced no rows: {self._tail(env)}")

    # -- the report ----------------------------------------------------------

    def report(self, deep: bool = False) -> dict:
        self.rows = []
        self.check_config()
        self.check_services()
        self.check_content_plane()
        self.check_image_lane(deep)
        self.check_media_store(deep)
        self.check_deploy_lane(deep)
        self.check_recipe()
        counts = {"ok": 0, "warn": 0, "fail": 0}
        for row in self.rows:
            counts[row["level"].lower()] += 1
        ready = counts["fail"] == 0
        failed = [f"{r['group']}/{r['check']}" for r in self.rows if r["level"] == FAIL]
        summary = (f"{counts['ok']} ok, {counts['warn']} warning(s), {counts['fail']} failure(s). "
                   + ("Ready to operate." if ready else "NOT ready: " + ", ".join(failed) + "."))
        out = {"ready": ready, "deep": bool(deep), "checks": self.rows,
               "counts": counts, "summary": summary}
        if not ready:
            out["next_action"] = _next_action(self.rows)
        return out


def _next_action(rows) -> str:
    """One concrete move for the first failure that has one, so the report ends with what to
    do rather than a list of what is wrong."""
    ids = [f"{r['group']}/{r['check']}" for r in rows if r["level"] == FAIL]
    hints = {
        "config/operator-token": "Ask the human to run ./bootstrap.sh on the host; an agent "
                                 "cannot mint the operator token.",
        "config/env-file": "Ask the human to run ./bootstrap.sh on the host.",
        "services/backend": "Call stack_up (no GPU needed); it waits until the stack serves.",
        "services/site": "Call stack_up (no GPU needed); it waits until the stack serves.",
        "image/render": "Call stack_up with gpu=true to add the image lane, then re-run doctor.",
        "deploy/auth": "Ask the human to run `wrangler login` once on this host.",
        "deploy/account-id": "Ask the human to put CLOUDFLARE_ACCOUNT_ID in .env.",
    }
    for check_id in ids:
        if check_id in hints:
            return hints[check_id]
    return f"Report the failing check(s) to the human: {', '.join(ids)}."


def _env_present(key: str, repo: Path) -> bool:
    """Is a config value set, in the environment or the host's .env? Presence only: this
    layer never reads a secret's value back, and never prints one."""
    if os.environ.get(key, "").strip():
        return True
    env_file = Path(repo) / ".env"
    if not env_file.is_file():
        return False
    try:
        lines = env_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    for line in lines:
        line = line.strip()
        if line.startswith(key + "="):
            value = line.split("=", 1)[1].split(" #", 1)[0].strip().strip('"').strip("'")
            if value:
                return True
    return False


def _probe(url: str, timeout: int = 10):
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": "censurado-doctor"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, f"HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        return exc.code, f"HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 0, f"unreachable ({getattr(exc, 'reason', None) or exc})"


def _binary(argv, timeout: int = 30):
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return False, "not installed"
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout}s"
    except OSError as exc:
        return False, str(exc)
    out = (proc.stdout or proc.stderr or "").strip().splitlines()
    detail = out[-1][:200] if out else f"exit {proc.returncode}"
    return proc.returncode == 0, detail


def report_text(report: dict) -> str:
    """The same verdict as prose, for the text block of the tool result."""
    lines = [f"  [{r['level']}] {r['group']}/{r['check']}: {r['detail']}"
             for r in report.get("checks", [])]
    lines.append("  --- " + report.get("summary", ""))
    if report.get("next_action"):
        lines.append("  NEXT: " + report["next_action"])
    return "\n".join(lines)


if __name__ == "__main__":  # a plain CLI lane, handy when debugging the server itself
    import sys
    deep_flag = "--deep" in sys.argv[1:]
    result = Doctor().report(deep=deep_flag)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["ready"] else 1)
