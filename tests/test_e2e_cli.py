"""Cross-repo end-to-end acceptance, driven ONLY through the CLI (never by touching the DB or
files directly). This is the ship gate from the project brief: a throwaway author is created,
writes an article, attaches/detaches a source, has its voice edited, the article is edited and
then removed, and the author is deleted, each step asserted against the RUNNING backend AND the
generated public site. It also asserts the brain is not a service (content serves with no brain).

It needs the live stack up (publish :8082 + generate watcher + site :8080); it SKIPS otherwise,
so a plain `make test` without a stack still passes. Bring the stack up with
`docker compose up -d publish generate site`, then run `pytest tests/test_e2e_cli.py -q`.

The throwaway author/article are cleaned up in a finally block, so the 6 real authors and the
live corpus are never disturbed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "cli" / "censurado.py"
BACKEND = os.environ.get("E2E_BACKEND", "http://127.0.0.1:8082")
SITE = os.environ.get("E2E_SITE", "http://127.0.0.1:8080")

HANDLE = "e2e-pytest-throwaway"
# Unique per run so a re-run never republishes a slug it tombstoned last time. (The backend
# returns a store_error 500 when republishing a tombstoned slug whose author was also removed
# and recreated; a fresh slug sidesteps that edge, which normal publishing never hits.)
TITLE = f"E2E pytest articulo {os.getpid()}"


def _http(url: str, token: str | None = None) -> tuple[int, str]:
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=6) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception:
        return 0, ""


def _token() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("NEWSROOM_OPERATOR_TOKEN="):
            return line.split("=", 1)[1].strip()
    return ""


_STACK_UP = _http(BACKEND + "/healthz")[1].strip() == "ok" and _http(SITE + "/latest/")[0] == 200
pytestmark = pytest.mark.skipif(
    not _STACK_UP,
    reason=f"E2E stack not up ({BACKEND}/healthz + {SITE}/latest/); "
    "run `docker compose up -d publish generate site`",
)


def _cli(*args: str, stdin: str | None = None) -> tuple[int, str, str]:
    p = subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=ROOT, input=stdin, capture_output=True, text=True, timeout=90,
    )
    return p.returncode, p.stdout, p.stderr


def _wait(predicate, timeout: float = 30, interval: float = 1.5) -> bool:
    """Poll the generate watcher's effect (it rebuilds the static site every ~2s) instead of a
    fixed sleep, so the test tracks real regen latency and is not flaky under load."""
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


def _slug_from(stdout: str) -> str:
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line).get("slug", "")
            except Exception:
                pass
    return ""


def _content_hash(slug: str) -> str:
    st, body = _http(f"{BACKEND}/articles/{slug}", _token())
    if st != 200:
        return ""
    try:
        return (json.loads(body).get("content_hash") or "")[:8]
    except Exception:
        return ""


def _latest_count(slug: str) -> int:
    # Count the card link path, not a raw slug substring: each card's share links embed the
    # article URL url-encoded (%2Fa%2F...), so the bare slug now appears several times per card.
    # The unencoded "/a/<slug>" path appears once per card (the card-link href).
    return _http(SITE + "/latest/")[1].count("/a/" + slug)


def _persona(handle: str) -> dict:
    rc, out, _ = _cli("persona", handle)
    if rc != 0:
        return {}
    try:
        return json.loads(out)
    except Exception:
        return {}


def _cleanup():
    # Best-effort: take any leftover article down and tombstone the author. Ignore failures.
    rc, out, _ = _cli("archive", HANDLE)
    try:
        for art in (json.loads(out).get("articles") or []):
            if art.get("slug"):
                _cli("unpublish", art["slug"], "--yes")
    except Exception:
        pass
    _cli("remove-author", HANDLE, "--yes")


def test_full_cli_lifecycle_against_the_running_stack():
    _cleanup()  # idempotent start
    try:
        # 1. CREATE the author (POST /authors) and confirm the registry serves it.
        persona = {
            "id": HANDLE, "display_name": "E2E Pytest Throwaway", "gender": "x",
            "beat": "pruebas", "about": "Autor de prueba E2E, se elimina al final.",
            "who_i_am": "Autor de prueba automatizada del harness.",
            "style": "Voz sobria de prueba.", "profile_topics": ["prueba-e2e"],
        }
        rc, _, err = _cli("create-author", "--file", "-", stdin=json.dumps(persona))
        assert rc == 0, f"create-author failed: {err}"
        rc, out, _ = _cli("personas")
        assert HANDLE in out.split(), "new author is not in the registry listing"

        # 2. PUBLISH an article as that author and confirm it renders on the CDN.
        rc, out, err = _cli(
            "preview", "--author", HANDLE, "--title", TITLE,
            "--subtitle", "Un subtitulo claramente distinto del titulo de la prueba",
            "--description", "Descripcion de una sola linea para el articulo de prueba E2E.",
            "--section", "actualidad", "--topics", "prueba-e2e",
            "--body", "Primer parrafo con el hecho central de la prueba automatizada y su "
                      "contexto inmediato. Segundo parrafo con detalle adicional y una cifra de "
                      "ejemplo, 42 por ciento, que da cuerpo al articulo de prueba.",
        )
        assert rc == 0, f"preview/publish failed: {err}"
        slug = _slug_from(out)
        assert slug, "publish returned no slug"
        assert _wait(lambda: _content_hash(slug) != ""), "article never reached the backend"
        ch = _content_hash(slug)
        assert _wait(lambda: _http(f"{SITE}/a/{slug}-{ch}/")[0] == 200), "article never rendered on the CDN"
        assert _wait(lambda: _latest_count(slug) == 1), "article not listed exactly once in /latest/"

        # 3. ATTACH then DETACH a source (the backend author_sources join, via CLI).
        rc, portals, _ = _cli("portals")
        src = portals.splitlines()[0].strip()
        assert src, "no source slugs available to attach"
        _cli("sources", HANDLE, "--set", src)
        rc, out, _ = _cli("sources", HANDLE)
        assert src in out, "source was not attached"
        _cli("sources", HANDLE, "--set", "")
        rc, out, _ = _cli("sources", HANDLE)
        assert src not in out, "source was not detached"

        # 4. MODIFY the author's voice (full upsert) and confirm the backend reflects it.
        persona["about"] = "ABOUT MODIFICADO por la prueba E2E."
        persona["style"] = "STYLE MODIFICADO, mas mordaz."
        rc, _, err = _cli("create-author", "--file", "-", stdin=json.dumps(persona))
        assert rc == 0, f"author modify failed: {err}"
        rec = _persona(HANDLE)
        assert rec.get("about") == persona["about"] and rec.get("style") == persona["style"]

        # 5. EDIT the article and confirm the CDN updates with NO duplicate permalink.
        new_title = TITLE + " MODIFICADO"
        rc, _, err = _cli("edit", slug, "--set", f"title={new_title}")
        assert rc == 0, f"edit failed: {err}"
        rc, out, _ = _cli("get", slug)
        assert json.loads(out).get("title") == new_title
        new_ch = _content_hash(slug)
        assert new_ch and new_ch != ch, "edit did not change the content hash"
        assert _wait(lambda: _http(f"{SITE}/a/{slug}-{new_ch}/")[0] == 200), "edited article not on the CDN"
        assert _wait(lambda: _latest_count(slug) == 1), "edited article duplicated in /latest/"
        assert _http(f"{SITE}/a/{slug}-{ch}/")[0] == 404, "the pre-edit permalink was not purged"

        # 6. UNPUBLISH (remove) the article and confirm it is gone from the CDN.
        rc, _, err = _cli("unpublish", slug, "--yes")
        assert rc == 0, f"unpublish failed: {err}"
        assert _wait(lambda: _http(f"{SITE}/a/{slug}-{new_ch}/")[0] == 404), "removed article still has a permalink"
        assert _wait(lambda: _latest_count(slug) == 0), "removed article still listed in /latest/"

        # 7. DELETE the author and confirm it drops from the registry (no gaps).
        rc, _, err = _cli("remove-author", HANDLE, "--yes")
        assert rc == 0, f"remove-author failed: {err}"
        rc, out, _ = _cli("personas")
        assert HANDLE not in out.split(), "deleted author still in the registry"
    finally:
        _cleanup()


def test_portada_reorder_round_trips_through_the_cli():
    # Set an explicit front-page order for a throwaway future date and read it back unchanged.
    st, body = _http(f"{BACKEND}/articles?limit=2", _token())
    arts = json.loads(body).get("articles", []) if st == 200 else []
    if len(arts) < 2:
        pytest.skip("need at least 2 published articles to test an ordering")
    s1, s2 = arts[0]["slug"], arts[1]["slug"]
    date = "2099-12-31"
    spec = {"entries": [{"slug": s1, "role": "important"}, {"slug": s2, "role": ""}]}
    rc, _, err = _cli("portada", date, "--set-json", json.dumps(spec))
    assert rc == 0, f"portada set failed: {err}"
    rc, out, _ = _cli("portada", date)
    data = json.loads(out)
    days = data if isinstance(data, list) else data.get("portadas", [data])
    day = next((p for p in days if str(p.get("date", "")).startswith(date)), None)
    assert day, "the portada we set was not returned"
    order = [(e.get("slug"), e.get("role")) for e in day.get("entries", [])]
    assert order == [(s1, "important"), (s2, "")], f"portada order did not round-trip: {order}"


def test_brain_is_not_a_service_and_content_serves_without_it():
    # The delete-the-brain acceptance: no brain process exists in the running stack, yet the
    # backend and the public site both serve. The brain is a host-run CLI, not a container.
    ps = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True)
    assert "brain" not in ps.stdout, "a brain service is running; the brain must be a host-run CLI"
    assert _http(BACKEND + "/healthz")[1].strip() == "ok"
    assert _http(SITE + "/latest/")[0] == 200
