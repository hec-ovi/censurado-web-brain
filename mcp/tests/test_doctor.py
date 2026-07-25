"""The preflight, driven with canned verb results.

The doctor is what an agent trusts before it touches the portal, so the tests pin the two
things that matter: a red verdict names the failing lane and one concrete next move, and a
green verdict is only green when the authenticated content reads actually worked.
"""
from __future__ import annotations

import json

import mcp_doctor
import pytest
from conftest import call
from mcp_doctor import Doctor


class FakeRunner:
    """Answers each verb from a canned table, recording what was asked."""

    def __init__(self, repo, answers=None):
        self.repo = repo
        self.cli = repo / "cli" / "censurado.py"
        self.work_dir = repo / ".mcp-work"
        self.answers = answers or {}
        self.calls = []

    def tmp_dir(self):
        d = self.work_dir / ".tmp"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def write_temp_bytes(self, blob, filename):
        path = self.tmp_dir() / filename
        path.write_bytes(blob)
        return path

    def run(self, argv, timeout=None):
        self.calls.append(list(argv))
        answer = self.answers.get(argv[0], {})
        stdout = answer.get("stdout", "")
        envelope = {"ok": answer.get("ok", True), "verb": argv[0],
                    "exit_code": 0 if answer.get("ok", True) else 1,
                    "stdout": stdout, "stderr": answer.get("stderr", "")}
        try:
            envelope["data"] = json.loads(stdout)
        except (json.JSONDecodeError, TypeError):
            pass
        return envelope


HEALTHY_STATUS = json.dumps({"ready": True, "services": {
    "backend": {"up": True, "status": 200, "url": "http://b/healthz", "core": True},
    "site": {"up": True, "status": 200, "url": "http://s/", "core": True},
    "comfyui": {"up": True, "status": 200, "url": "http://c/system_stats", "core": False},
    "public": {"up": True, "status": 200, "url": "https://live/", "core": False}}})

DEAD_STATUS = json.dumps({"ready": False, "services": {
    "backend": {"up": False, "status": 0, "url": "http://b/healthz", "core": True},
    "site": {"up": False, "status": 0, "url": "http://s/", "core": True},
    "comfyui": {"up": False, "status": 0, "url": "http://c/system_stats", "core": False}}})

CLI_DOCTOR = ("  [OK] publish backend http://b/healthz (HTTP 200)\n"
              "  [OK] sub-skill authors routed + valid\n"
              "  [OK] workflow: 12 modes, 23 node files all present\n"
              "  --- 0 failure(s), 0 warning(s). OK to operate.\n")

GREEN = {
    "status": {"stdout": HEALTHY_STATUS},
    "personas": {"stdout": "ana\n"},
    "portals": {"stdout": "lanacion\n"},
    "archive": {"stdout": json.dumps({"total": 1, "articles": []})},
    "recomendado": {"stdout": json.dumps({"slugs": []})},
    "portada": {"stdout": json.dumps({"portadas": []})},
    "image": {"stdout": json.dumps({"4": {"inputs": {"text": "x"}}})},
    "doctor": {"stdout": CLI_DOCTOR},
}


@pytest.fixture(autouse=True)
def _no_host_binaries(monkeypatch):
    """The deploy lane shells out to docker/node/wrangler; pin them so the verdict under
    test is about the doctor's logic, not about what this host happens to have installed."""
    monkeypatch.setattr(mcp_doctor, "_binary", lambda argv, timeout=30: (True, "v1.2.3"))
    monkeypatch.setattr(mcp_doctor, "_probe", lambda url, timeout=10: (200, "HTTP 200"))


def _levels(report):
    return {f"{r['group']}/{r['check']}": r["level"] for r in report["checks"]}


def test_a_healthy_stack_reports_ready(stub_repo, monkeypatch):
    monkeypatch.setenv("NEWSROOM_OPERATOR_TOKEN", "t")
    report = Doctor(FakeRunner(stub_repo, GREEN)).report()
    assert report["ready"] is True and report["counts"]["fail"] == 0
    assert "next_action" not in report
    levels = _levels(report)
    assert levels["services/backend"] == "OK" and levels["content/authors"] == "OK"
    assert levels["deploy/account-id"] == "OK"
    # The shallow run says plainly what it did NOT try, instead of implying it passed.
    assert levels["image/render"] == "WARN" and levels["media/upload"] == "WARN"
    assert levels["deploy/auth"] == "WARN"


def test_a_dead_backend_fails_and_names_the_one_next_move(stub_repo):
    answers = dict(GREEN, status={"stdout": DEAD_STATUS})
    for verb in ("personas", "portals", "archive", "recomendado", "portada"):
        answers[verb] = {"ok": False, "stderr": "ERROR: cannot reach http://b (refused)"}
    report = Doctor(FakeRunner(stub_repo, answers)).report()
    assert report["ready"] is False
    levels = _levels(report)
    assert levels["services/backend"] == "FAIL" and levels["services/site"] == "FAIL"
    assert levels["content/authors"] == "FAIL"
    assert levels["services/comfyui"] == "WARN"      # the image lane is optional
    assert report["next_action"] == "Call stack_up (no GPU needed); it waits until the stack serves."


def test_a_rejected_token_fails_the_content_plane_even_when_the_ports_answer(stub_repo):
    """The exact case a port probe misses: services up, writes impossible."""
    answers = dict(GREEN)
    answers["personas"] = {"ok": False, "stderr": "ERROR: cannot read the author registry (401)"}
    report = Doctor(FakeRunner(stub_repo, answers)).report()
    assert report["ready"] is False
    assert _levels(report)["content/authors"] == "FAIL"
    assert "content/authors" in report["summary"]


def test_a_missing_operator_token_is_a_failure(stub_repo, monkeypatch):
    monkeypatch.delenv("NEWSROOM_OPERATOR_TOKEN", raising=False)
    (stub_repo / ".env").write_text("CLOUDFLARE_ACCOUNT_ID=abc\n", encoding="utf-8")
    report = Doctor(FakeRunner(stub_repo, GREEN)).report()
    assert _levels(report)["config/operator-token"] == "FAIL"
    assert "bootstrap.sh" in report["next_action"]


def test_the_token_value_is_never_echoed(stub_repo, monkeypatch):
    monkeypatch.setenv("NEWSROOM_OPERATOR_TOKEN", "super-secret-value")
    report = Doctor(FakeRunner(stub_repo, GREEN)).report()
    assert "super-secret-value" not in json.dumps(report)


def test_deep_mode_exercises_the_image_render_and_the_media_store(stub_repo):
    answers = dict(GREEN)
    answers["image"] = {"stdout": json.dumps({"image": "/media/abc.png", "image_alt": "",
                                              "seed": 1, "bytes": 4242})}
    answers["media"] = {"stdout": json.dumps({"url": "/media/probe.png"})}
    runner = FakeRunner(stub_repo, answers)
    report = Doctor(runner).report(deep=True)
    levels = _levels(report)
    assert report["deep"] is True
    assert levels["image/render"] == "OK" and levels["media/upload"] == "OK"
    assert levels["media/readback"] == "OK"
    rendered = [c for c in runner.calls if c[0] == "image" and "--dry-run" not in c]
    assert rendered and "--steps" in rendered[0]


def test_deep_mode_fails_when_the_image_lane_only_pretends_to_work(stub_repo):
    """`image` exits 0 with IMAGE SKIPPED when the renderer is unreachable, so an ok exit
    with no url must still be a failure."""
    answers = dict(GREEN)
    answers["image"] = {"ok": True, "stdout": "", "stderr": "IMAGE SKIPPED: ComfyUI not reachable"}
    report = Doctor(FakeRunner(stub_repo, answers)).report(deep=True)
    assert _levels(report)["image/render"] == "FAIL"
    assert report["ready"] is False


def test_a_half_wired_reactions_binding_fails_the_deploy_lane(stub_repo, monkeypatch):
    monkeypatch.setenv("D1_REACTIONS_ID", "db-123")
    monkeypatch.delenv("REACTIONS_SALT", raising=False)
    report = Doctor(FakeRunner(stub_repo, GREEN)).report()
    assert _levels(report)["deploy/reactions"] == "FAIL"


def test_a_missing_deploy_binary_fails_the_deploy_lane(stub_repo, monkeypatch):
    monkeypatch.setattr(mcp_doctor, "_binary", lambda argv, timeout=30: (False, "not installed"))
    report = Doctor(FakeRunner(stub_repo, GREEN)).report()
    levels = _levels(report)
    assert levels["deploy/docker"] == "FAIL" and levels["deploy/node"] == "FAIL"


def test_deep_mode_fails_when_wrangler_is_not_logged_in(stub_repo, monkeypatch):
    def binary(argv, timeout=30):
        if "wrangler" in argv:
            return False, "You are not authenticated."
        return True, "v1"
    monkeypatch.setattr(mcp_doctor, "_binary", binary)
    answers = dict(GREEN, image={"stdout": json.dumps({"image": "/media/a.png", "bytes": 1})},
                   media={"stdout": json.dumps({"url": "/media/p.png"})})
    report = Doctor(FakeRunner(stub_repo, answers)).report(deep=True)
    assert _levels(report)["deploy/auth"] == "FAIL"
    assert "wrangler login" in report["next_action"]


def test_the_recipe_rows_come_from_the_cli_self_check(stub_repo):
    report = Doctor(FakeRunner(stub_repo, GREEN)).report()
    recipe = [r for r in report["checks"] if r["group"] == "recipe"]
    details = " ".join(r["detail"] for r in recipe)
    assert "sub-skill authors" in details and "23 node files" in details
    # The service rows the CLI self-check also prints are not duplicated here.
    assert "publish backend" not in details


def test_the_doctor_tool_returns_the_report_plus_readable_text(server, monkeypatch):
    """Through the protocol: the report is structured output AND a legible verdict."""
    monkeypatch.setenv("STUB_MODE", "ok")
    result = call(server, "doctor", {})
    report = result["structuredContent"]
    assert set(report) >= {"ready", "deep", "checks", "counts", "summary"}
    assert all(r["level"] in ("OK", "WARN", "FAIL") for r in report["checks"])
    text = result["content"][0]["text"]
    assert "[" in text and "---" in text
