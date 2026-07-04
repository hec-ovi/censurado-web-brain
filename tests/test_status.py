"""The backend liveness/auth probe (``newsroom.status.probe_backend``), driven directly.

``probe_backend`` is the whole substance of the ``status`` CLI verb: the single non-raising
GET against the backend read API, mapped to a typed verdict an operator (or a health check)
branches on. The verb-level tests in ``test_cli.py`` inject a fake probe, so these tests pin
the REAL transport logic instead: a live 2xx author count, a transport fault, a 401/403 auth
rejection, a non-auth 5xx, a garbage body, and the bounded timeout. httpx is monkeypatched at
``newsroom.status.httpx.get`` so no network is touched.
"""

from __future__ import annotations

import httpx
import pytest

from newsroom.status import PROBE_TIMEOUT, BackendProbe, probe_backend


class _Resp:
    """A minimal httpx-response double: a status code plus a JSON body (or a ValueError on
    ``.json()`` to model a non-JSON error page)."""

    def __init__(self, status_code, body=None, *, bad_json=False):
        self.status_code = status_code
        self._body = body
        self._bad_json = bad_json

    def json(self):
        if self._bad_json:
            raise ValueError("not json")
        return self._body


def _patch_get(monkeypatch, resp=None, *, raises=None, capture=None):
    def fake_get(url, headers=None, timeout=None):
        if capture is not None:
            capture.update(url=url, headers=headers, timeout=timeout)
        if raises is not None:
            raise raises
        return resp

    monkeypatch.setattr("newsroom.status.httpx.get", fake_get)


def test_probe_counts_live_authors(monkeypatch):
    # A 2xx with a mix of rows: only non-blank, non-deleted author rows count.
    rows = [
        {"handle": "ada", "deleted": False},
        {"handle": "  ", "deleted": False},   # blank handle -> skipped
        {"handle": "gone", "deleted": True},  # tombstoned -> skipped
        "not-a-dict",                          # junk row -> skipped
        {"handle": "bo"},                      # no deleted key -> live
    ]
    _patch_get(monkeypatch, _Resp(200, {"authors": rows}))
    p = probe_backend("http://x", "tok")
    assert isinstance(p, BackendProbe)
    assert p.reachable and p.authorized and p.status == 200
    assert p.author_count == 2  # ada + bo


def test_probe_transport_fault_is_unreachable(monkeypatch):
    # An httpx transport error is NOT re-raised: it maps to a typed unreachable verdict.
    _patch_get(monkeypatch, raises=httpx.ConnectError("refused"))
    p = probe_backend("http://x", "tok")
    assert p.reachable is False and p.authorized is False
    assert p.status == 0 and "refused" in p.detail
    assert p.author_count is None


@pytest.mark.parametrize("code", [401, 403])
def test_probe_auth_rejection_is_unauthorized(monkeypatch, code):
    # A 401/403 means the token was rejected: reachable, but not authorized.
    _patch_get(monkeypatch, _Resp(code, {"code": "unauthorized", "detail": "nope"}))
    p = probe_backend("http://x", "tok")
    assert p.reachable is True and p.authorized is False
    assert p.status == code and p.detail == "unauthorized"


def test_probe_non_auth_error_stays_authorized(monkeypatch):
    # A 500 is reachable AND the token was not rejected: authorized stays True, reason in detail.
    _patch_get(monkeypatch, _Resp(500, {"code": "boom", "detail": "kaput"}))
    p = probe_backend("http://x", "tok")
    assert p.reachable is True and p.authorized is True
    assert p.status == 500 and p.detail == "boom"


def test_probe_error_with_empty_body_uses_http_status(monkeypatch):
    # A non-2xx with no code/detail in the body falls back to a synthetic http_<status> note.
    _patch_get(monkeypatch, _Resp(502, {}))
    p = probe_backend("http://x", "tok")
    assert p.reachable is True and p.authorized is True and p.detail == "http_502"


def test_probe_non_json_body_falls_back(monkeypatch):
    # A 200 whose body is not JSON (an HTML page): no crash, zero authors counted.
    _patch_get(monkeypatch, _Resp(200, bad_json=True))
    p = probe_backend("http://x", "tok")
    assert p.reachable is True and p.authorized is True and p.author_count == 0


def test_probe_uses_a_bounded_timeout_and_bearer(monkeypatch):
    # The probe sends the operator bearer, hits <base>/authors, and passes a bounded timeout
    # (never None) so an unresponsive backend cannot hang the caller.
    cap: dict = {}
    _patch_get(monkeypatch, _Resp(200, {"authors": []}), capture=cap)
    probe_backend("http://x/", "tok")
    assert cap["timeout"] == PROBE_TIMEOUT
    assert cap["headers"]["Authorization"] == "Bearer tok"
    assert cap["url"] == "http://x/authors"  # base rstrip'd + /authors
