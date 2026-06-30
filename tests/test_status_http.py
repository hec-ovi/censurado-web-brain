"""The backend-connection status API (infra chunk 6), driven end to end.

This exercises the REAL entry point: ``create_app`` mounted under a FastAPI
``TestClient`` and hit over HTTP, with the backend read API (the outbound GET /authors
the probe makes) mocked at ``newsroom.mirror.client.httpx.get`` -- the same module-level
monkeypatch the mirror tests use. It covers the three connection verdicts the route must
distinguish (reachable + authorized, backend unreachable, backend unauthorized), the
graceful degrade (a down backend is a 200 body, never a 500), the config echo (base URL,
token-configured), and the OpenAPI typing. It also pins the underlying non-raising
``probe_backend`` and the ``status`` CLI verb (the parity surface).
"""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from newsroom.brain import create_app
from newsroom.cli import main
from newsroom.config import Settings
from newsroom.mirror import BackendProbe, probe_backend

_BACKEND = "http://backend.test:8080"
_TOKEN = "op-token"


def _client(tmp_path, *, base_url: str = _BACKEND, token: str = _TOKEN) -> TestClient:
    settings = Settings(
        persona_db_path=tmp_path / "brain.db",
        inference_base_url="http://127.0.0.1:1/v1",
        publish_base_url=base_url,
        operator_token=token,
    )
    return TestClient(create_app(settings=settings))


def _authors_response(*handles: str) -> httpx.Response:
    rows = [{"handle": h, "name": h.title()} for h in handles]
    return httpx.Response(200, json={"authors": rows}, request=httpx.Request("GET", "http://x/authors"))


# ----- the route: reachable + authorized -----


def test_backend_status_reachable_and_authorized(tmp_path, monkeypatch):
    # A 2xx from GET /authors -> reachable + authorized, with the live author count as
    # the useful signal, and the config echoed back (base URL rstripped, token present).
    monkeypatch.setattr(
        "newsroom.mirror.client.httpx.get",
        lambda *a, **k: _authors_response("ada-lovelace", "ida-rivers"),
    )
    client = _client(tmp_path, base_url="http://backend.test:8080/")  # trailing slash

    resp = client.get("/status/backend")
    assert resp.status_code == 200
    body = resp.json()
    assert body["backend_base_url"] == "http://backend.test:8080"  # rstripped
    assert body["token_configured"] is True
    assert body["reachable"] is True
    assert body["authorized"] is True
    assert body["author_count"] == 2
    assert body["status_code"] == 200
    assert body["detail"] == ""


# ----- the route: backend unreachable (transport fault) -----


def test_backend_status_unreachable_degrades_to_200(tmp_path, monkeypatch):
    # A transport fault (connection refused) must NOT 500 the route: it degrades to a 200
    # body with reachable=False, so an operator sees "backend down" rather than a crash.
    def boom(*_a, **_k):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("newsroom.mirror.client.httpx.get", boom)
    client = _client(tmp_path)

    resp = client.get("/status/backend")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reachable"] is False
    assert body["authorized"] is False
    assert body["author_count"] is None
    assert body["status_code"] == 0
    assert "refused" in body["detail"]
    # The config is still reported even though the backend is down.
    assert body["backend_base_url"] == _BACKEND
    assert body["token_configured"] is True


# ----- the route: backend reachable but unauthorized (401) -----


def test_backend_status_unauthorized(tmp_path, monkeypatch):
    # A 401 means the host answered but rejected the token: reachable=True,
    # authorized=False, with the backend's problem code surfaced in detail.
    monkeypatch.setattr(
        "newsroom.mirror.client.httpx.get",
        lambda *a, **k: httpx.Response(
            401, json={"code": "invalid_token"}, request=httpx.Request("GET", "http://x/authors")
        ),
    )
    client = _client(tmp_path)

    resp = client.get("/status/backend")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reachable"] is True
    assert body["authorized"] is False
    assert body["status_code"] == 401
    assert body["detail"] == "invalid_token"
    assert body["author_count"] is None


def test_backend_status_reports_unset_token(tmp_path, monkeypatch):
    # With no operator token configured, token_configured is False even before any probe
    # verdict; the probe still runs (the backend itself decides auth).
    monkeypatch.setattr(
        "newsroom.mirror.client.httpx.get",
        lambda *a, **k: httpx.Response(
            401, json={"code": "missing_token"}, request=httpx.Request("GET", "http://x/authors")
        ),
    )
    client = _client(tmp_path, token="")

    body = client.get("/status/backend").json()
    assert body["token_configured"] is False
    assert body["authorized"] is False


def test_status_route_is_typed_in_openapi(tmp_path):
    client = _client(tmp_path)
    spec = client.get("/openapi.json").json()
    assert "/status/backend" in spec["paths"]
    get = spec["paths"]["/status/backend"]["get"]
    ref = get["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    assert ref.endswith("/BackendStatusOut")


# ----- the underlying probe (non-raising, all branches) -----


def test_probe_backend_counts_live_authors(monkeypatch):
    # The 2xx path counts live authors the same way fetch_web_authors filters: a blank
    # handle and a tombstoned row are not counted.
    def fake_get(url, **_k):
        assert url == "http://backend.test:8080/authors"
        return httpx.Response(
            200,
            json={"authors": [
                {"handle": "ada"},
                {"handle": "ada", "deleted": True},  # tombstoned -> not counted
                {"handle": ""},  # blank handle -> not counted
                {"handle": "tomas"},
            ]},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr("newsroom.mirror.client.httpx.get", fake_get)
    probe = probe_backend("http://backend.test:8080/", _TOKEN)
    assert probe.reachable is True and probe.authorized is True
    assert probe.author_count == 2
    assert probe.status == 200


def test_probe_backend_transport_fault_is_unreachable(monkeypatch):
    def boom(*_a, **_k):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr("newsroom.mirror.client.httpx.get", boom)
    probe = probe_backend("http://backend.test:8080", _TOKEN)
    assert probe.reachable is False and probe.authorized is False
    assert probe.status == 0 and "no route to host" in probe.detail


@pytest.mark.parametrize("code", [401, 403])
def test_probe_backend_auth_rejection(monkeypatch, code):
    monkeypatch.setattr(
        "newsroom.mirror.client.httpx.get",
        lambda *a, **k: httpx.Response(
            code, json={"code": "denied"}, request=httpx.Request("GET", "http://x/authors")
        ),
    )
    probe = probe_backend("http://x", _TOKEN)
    assert probe.reachable is True and probe.authorized is False
    assert probe.status == code and probe.detail == "denied"


def test_probe_backend_non_auth_error_stays_authorized(monkeypatch):
    # A 5xx is reached-but-erroring, NOT a token rejection: authorized stays True so the
    # operator does not mistake a backend fault for a credential problem.
    monkeypatch.setattr(
        "newsroom.mirror.client.httpx.get",
        lambda *a, **k: httpx.Response(503, text="oops", request=httpx.Request("GET", "http://x/authors")),
    )
    probe = probe_backend("http://x", _TOKEN)
    assert probe.reachable is True and probe.authorized is True
    assert probe.status == 503 and probe.detail == "http_503"
    assert probe.author_count is None


def test_probe_backend_uses_a_bounded_timeout(monkeypatch):
    # The probe must pass a bounded timeout so a hung backend cannot hang the caller.
    seen: dict = {}

    def fake_get(url, **kwargs):
        seen.update(kwargs)
        return _authors_response("ada")

    monkeypatch.setattr("newsroom.mirror.client.httpx.get", fake_get)
    probe_backend("http://x", _TOKEN)
    assert isinstance(seen.get("timeout"), float) and seen["timeout"] > 0


# ----- the CLI parity verb -----


def test_cli_status_prints_diagnostic_and_exit_0_when_wired(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEWSROOM_PUBLISH_BASE_URL", "http://backend.test:8080")
    monkeypatch.setenv("NEWSROOM_OPERATOR_TOKEN", "op-token")
    monkeypatch.setenv("NEWSROOM_PERSONA_DB_PATH", str(tmp_path / "brain.db"))

    def fake_probe(base_url, token):
        assert base_url == "http://backend.test:8080" and token == "op-token"
        return BackendProbe(reachable=True, authorized=True, status=200, author_count=3)

    rc = main(["status"], backend_probe=fake_probe)
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["backend_base_url"] == "http://backend.test:8080"
    assert out["token_configured"] is True
    assert out["reachable"] is True and out["authorized"] is True
    assert out["author_count"] == 3
    assert out["status_code"] == 200


def test_cli_status_exit_1_when_unreachable(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEWSROOM_PUBLISH_BASE_URL", "http://backend.test:8080")
    monkeypatch.setenv("NEWSROOM_OPERATOR_TOKEN", "op-token")
    monkeypatch.setenv("NEWSROOM_PERSONA_DB_PATH", str(tmp_path / "brain.db"))

    rc = main(
        ["status"],
        backend_probe=lambda *_a: BackendProbe(
            reachable=False, authorized=False, status=0, detail="boom"
        ),
    )
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert out["reachable"] is False
    assert out["detail"] == "boom"
