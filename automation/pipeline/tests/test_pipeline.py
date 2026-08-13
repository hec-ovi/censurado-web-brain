"""Contract tests for the pipeline box, through the real entry point (run.py)."""
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
RUN = ROOT / "automation" / "pipeline" / "run.py"
PROMPTS = ROOT / "automation" / "pipeline" / "prompts"

DRAFT = {"title": "Titulo de prueba", "standfirst": "Una frase.",
         "body": "Parrafo uno.\n\nParrafo dos.\n\nParrafo tres."}


class FakeApi(BaseHTTPRequestHandler):
    behavior = "publish"   # publish | revise | fail
    calls: list = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        type(self).calls.append(body)
        if type(self).behavior == "fail":
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"boom")
            return
        prompt = body["messages"][-1]["content"]
        if "editor de mesa" in prompt:
            content = json.dumps({"verdict": "publish" if type(self).behavior == "publish"
                                  else "revise", "notes": "motivo"})
        elif "MARCA-CONSULTAS" in prompt:
            content = json.dumps({"queries": ["consulta de prueba"],
                                  "read_urls": ["https://fuente.test/nota-1"]})
        else:
            content = json.dumps(DRAFT, ensure_ascii=False)
        out = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


class FakeBackend(BaseHTTPRequestHandler):
    posts: list = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        type(self).posts.append({"body": body, "idem": self.headers.get("Idempotency-Key"),
                                 "auth": self.headers.get("Authorization")})
        out = json.dumps({"id": "1", "slug": "nota-prueba"}).encode()
        self.send_response(201)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(out)

    feed_port: int = 0

    def do_GET(self):
        if self.path.endswith("/sources") and "/authors/" in self.path:
            out = json.dumps({"handle": "autor-test",
                              "sources": ["fuente-test", "cuenta-x"]}).encode()
        elif self.path.startswith("/sources"):
            out = json.dumps({"sources": [
                {"slug": "fuente-test", "domain": "fuente.test", "lean": "left",
                 "enabled": True, "feed_type": "native_rss",
                 "feed_urls": [f"http://127.0.0.1:{type(self).feed_port}/rss"]},
                {"slug": "cuenta-x", "domain": "x.com", "lean": "right",
                 "enabled": True, "feed_type": "site_search", "feed_urls": []},
            ]}).encode()
        elif self.path.startswith("/authors"):
            out = json.dumps([{"handle": "autor-test", "name": "Autor Test", "bio": "bio-prueba",
                               "style": "estilo-prueba: tercera persona.",
                               "metadata": {"who_i_am": "soy una prueba",
                                            "profile_topics": ["tema-perfil"],
                                            "few_shots_pos": [{"prompt": "p", "good": "EJEMPLO-SI"}],
                                            "few_shots_neg": [{"prompt": "p", "bad": "EJEMPLO-NO"}]}}]).encode()
        elif self.path.startswith("/editorial-text"):
            out = json.dumps({"entries": [
                {"key": "regla.uno", "value": "sin muletillas", "deleted": False}]}).encode()
        else:
            out = json.dumps({"slug": "nota-prueba", "content_hash": "deadbeefcafebabe"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>Fuente Test</title>
<item><title>TITULAR-DE-PRUEBA uno</title><link>https://fuente.test/nota-1</link>
<pubDate>{date}</pubDate></item></channel></rss>"""


class FakeFeed(BaseHTTPRequestHandler):
    def do_GET(self):
        from email.utils import format_datetime
        from datetime import datetime, timezone
        out = RSS.format(date=format_datetime(datetime.now(timezone.utc))).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/rss+xml")
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


@pytest.fixture()
def servers():
    FakeApi.behavior, FakeApi.calls = "publish", []
    FakeBackend.posts = []
    api = ThreadingHTTPServer(("127.0.0.1", 0), FakeApi)
    backend = ThreadingHTTPServer(("127.0.0.1", 0), FakeBackend)
    feed = ThreadingHTTPServer(("127.0.0.1", 0), FakeFeed)
    FakeBackend.feed_port = feed.server_address[1]
    for s in (api, backend, feed):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    yield api.server_address[1], backend.server_address[1]
    for s in (api, backend, feed):
        s.shutdown()


def write_config(tmp: Path, api_port: int, backend_port: int, nodes=None, cli_cmd=None,
                 cli_stdin=False, websearch=None) -> Path:
    cfg = {
        "run_dir": "runs",
        "backend": {"base_url": f"http://127.0.0.1:{backend_port}", "token_env": "TEST_TOKEN"},
        **({"websearch": websearch} if websearch else {}),
        "adapters": {
            "api": {"base_url": f"http://127.0.0.1:{api_port}/v1", "model": "fake"},
            **({"cli": {"cmd": cli_cmd, **({"stdin": True} if cli_stdin else {})}}
               if cli_cmd else {}),
        },
        "nodes": nodes or [
            {"name": "draft", "adapter": "api", "role": "draft", "output": "json",
             "prompt": str(PROMPTS / "draft.md")},
            {"name": "evaluate", "adapter": "api", "role": "gate", "output": "json",
             "prompt": str(PROMPTS / "evaluate.md")},
        ],
    }
    p = tmp / "config.json"
    p.write_text(json.dumps(cfg))
    return p


def run_pipeline(cfg: Path, *extra: str):
    env = dict(os.environ, TEST_TOKEN="tok-test")
    mode = () if any(a == "--mode" for a in extra) else ("--mode", "auto")
    return subprocess.run(
        [sys.executable, str(RUN), "--config", str(cfg), "--topic", "el software libre",
         "--author", "autor-test", "--section", "world", *mode, *extra],
        capture_output=True, text=True, env=env, cwd=ROOT, timeout=120)


def run_sub(cfg: Path, sub: str, *extra: str):
    env = dict(os.environ, TEST_TOKEN="tok-test")
    return subprocess.run(
        [sys.executable, str(RUN), sub, "--config", str(cfg), *extra],
        capture_output=True, text=True, env=env, cwd=ROOT, timeout=60)


def test_publishes_through_the_gate(tmp_path, servers):
    api_port, backend_port = servers
    p = run_pipeline(write_config(tmp_path, api_port, backend_port), "--run-id", "run-pub")
    assert p.returncode == 0, p.stderr
    result = json.loads(p.stdout.strip().splitlines()[-1])
    assert result["status"] == "published"
    assert result["slug"] == "nota-prueba"
    assert result["permalink"] == "/a/nota-prueba-deadbeef/"
    assert len(FakeBackend.posts) == 1
    assert FakeBackend.posts[0]["idem"] == "run-pub"
    assert FakeBackend.posts[0]["auth"] == "Bearer tok-test"
    assert FakeBackend.posts[0]["body"]["metadata"]["standfirst"] == "Una frase."
    art = tmp_path / "runs" / "run-pub"
    assert (art / "draft.json").is_file() and (art / "evaluate.json").is_file()


def test_gate_rejects_without_publishing(tmp_path, servers):
    api_port, backend_port = servers
    FakeApi.behavior = "revise"
    p = run_pipeline(write_config(tmp_path, api_port, backend_port))
    assert p.returncode == 3, p.stderr
    result = json.loads(p.stdout.strip().splitlines()[-1])
    assert result["status"] == "rejected" and result["notes"] == "motivo"
    assert FakeBackend.posts == []


def test_cli_adapter_drives_a_node(tmp_path, servers):
    api_port, backend_port = servers
    fake_cli = tmp_path / "fake-agent.py"
    fake_cli.write_text(
        "import json,sys\nassert sys.argv[1]\n"
        f"print(json.dumps({DRAFT!r}, ensure_ascii=False))\n")
    nodes = [
        {"name": "draft", "adapter": "cli", "role": "draft", "output": "json",
         "prompt": str(PROMPTS / "draft.md")},
        {"name": "evaluate", "adapter": "api", "role": "gate", "output": "json",
         "prompt": str(PROMPTS / "evaluate.md")},
    ]
    cfg = write_config(tmp_path, api_port, backend_port, nodes=nodes,
                       cli_cmd=[sys.executable, str(fake_cli), "{prompt}"])
    p = run_pipeline(cfg)
    assert p.returncode == 0, p.stderr
    assert len(FakeBackend.posts) == 1


def test_cli_stdin_mode_feeds_the_prompt(tmp_path, servers):
    api_port, backend_port = servers
    fake_cli = tmp_path / "fake-stdin-agent.py"
    fake_cli.write_text(
        "import json,sys\nassert 'software libre' in sys.stdin.read()\n"
        f"print(json.dumps({DRAFT!r}, ensure_ascii=False))\n")
    nodes = [
        {"name": "draft", "adapter": "cli", "role": "draft", "output": "json",
         "prompt": str(PROMPTS / "draft.md")},
        {"name": "evaluate", "adapter": "api", "role": "gate", "output": "json",
         "prompt": str(PROMPTS / "evaluate.md")},
    ]
    cfg = write_config(tmp_path, api_port, backend_port, nodes=nodes,
                       cli_cmd=[sys.executable, str(fake_cli)], cli_stdin=True)
    p = run_pipeline(cfg)
    assert p.returncode == 0, p.stderr
    assert len(FakeBackend.posts) == 1


def test_context_sources_reach_the_prompt(tmp_path, servers):
    api_port, backend_port = servers
    manual = tmp_path / "manual.md"
    manual.write_text("CONTENIDO-DEL-MANUAL: nunca uses relleno.")
    nodes = [
        {"name": "draft", "adapter": "api", "role": "draft", "output": "json",
         "prompt": str(PROMPTS / "draft.md"),
         "context": {"skill": {"file": str(manual)},
                     "persona": {"persona": True},
                     "reglas": {"editorial": "es"}}},
        {"name": "evaluate", "adapter": "api", "role": "gate", "output": "json",
         "prompt": str(PROMPTS / "evaluate.md")},
    ]
    p = run_pipeline(write_config(tmp_path, api_port, backend_port, nodes=nodes))
    assert p.returncode == 0, p.stderr
    draft_prompt = FakeApi.calls[0]["messages"][-1]["content"]
    assert "CONTENIDO-DEL-MANUAL" in draft_prompt
    assert "estilo-prueba" in draft_prompt and "soy una prueba" in draft_prompt
    assert "EJEMPLO-SI" in draft_prompt and "EJEMPLO-NO" in draft_prompt
    assert "tema-perfil" in draft_prompt
    assert "regla.uno: sin muletillas" in draft_prompt
    assert len(FakeBackend.posts) == 1


def test_invalid_config_refuses_to_start(tmp_path, servers):
    api_port, backend_port = servers
    nodes = [{"name": "draft", "adapter": "cli", "role": "draft", "output": "json",
              "prompt": "missing.md"}]
    cfg = write_config(tmp_path, api_port, backend_port, nodes=nodes)
    p = run_pipeline(cfg)
    assert p.returncode == 2
    assert "not configured" in p.stderr and "not found" in p.stderr


def test_adapter_failure_exits_after_retries(tmp_path, servers):
    api_port, backend_port = servers
    FakeApi.behavior = "fail"
    p = run_pipeline(write_config(tmp_path, api_port, backend_port))
    assert p.returncode == 4, p.stderr
    assert len(FakeApi.calls) == 3
    assert FakeBackend.posts == []


def test_feeds_context_inlines_fresh_titulars(tmp_path, servers):
    api_port, backend_port = servers
    prompt = tmp_path / "draft-con-feeds.md"
    prompt.write_text("Titulares:\n{titulares}\n\nEscribi la nota de {topic}.")
    nodes = [
        {"name": "draft", "adapter": "api", "role": "draft", "output": "json",
         "prompt": str(prompt), "context": {"titulares": {"feeds": {"hours": 48}}}},
        {"name": "evaluate", "adapter": "api", "role": "gate", "output": "json",
         "prompt": str(PROMPTS / "evaluate.md")},
    ]
    p = run_pipeline(write_config(tmp_path, api_port, backend_port, nodes=nodes))
    assert p.returncode == 0, p.stderr
    sent = FakeApi.calls[0]["messages"][-1]["content"]
    assert "TITULAR-DE-PRUEBA" in sent
    assert "cuenta-x" in sent


def test_websearch_context_runs_the_proposed_queries(tmp_path, servers):
    api_port, backend_port = servers
    ws = tmp_path / "fake-websearch.py"
    ws.write_text(
        "import json, sys\n"
        "if sys.argv[1] == 'web-search':\n"
        "    print(json.dumps({'ok': True, 'data': {'results': [\n"
        "        {'title': 'Nota externa', 'url': 'https://externo.test/nota',"
        " 'snippet': 'resumen'}]}}))\n"
        "else:\n"
        "    print(json.dumps({'ok': True, 'data': {'pages': [\n"
        "        {'content': 'CONTENIDO-DE ' + sys.argv[2], 'blocked': False}]}}))\n")
    qprompt = tmp_path / "consultas.md"
    qprompt.write_text("MARCA-CONSULTAS para {topic}")
    dprompt = tmp_path / "draft-con-web.md"
    dprompt.write_text("Material:\n{web}\n\nNota sobre {topic}.")
    nodes = [
        {"name": "queries", "adapter": "api", "output": "json", "prompt": str(qprompt)},
        {"name": "draft", "adapter": "api", "role": "draft", "output": "json",
         "prompt": str(dprompt),
         "context": {"web": {"websearch": {"queries_from": "queries",
                                           "urls_from": "queries"}}}},
        {"name": "evaluate", "adapter": "api", "role": "gate", "output": "json",
         "prompt": str(PROMPTS / "evaluate.md")},
    ]
    cfg = write_config(tmp_path, api_port, backend_port, nodes=nodes,
                       websearch={"cmd": [sys.executable, str(ws)]})
    p = run_pipeline(cfg)
    assert p.returncode == 0, p.stderr
    draft_prompt = FakeApi.calls[1]["messages"][-1]["content"]
    assert "CONTENIDO-DE https://fuente.test/nota-1" in draft_prompt
    assert "CONTENIDO-DE https://externo.test/nota" in draft_prompt
    assert "externo.test" in draft_prompt


def test_preview_mode_holds_the_piece_and_approve_publishes_it(tmp_path, servers):
    api_port, backend_port = servers
    cfg = write_config(tmp_path, api_port, backend_port)
    p = run_pipeline(cfg, "--mode", "preview", "--run-id", "run-hold")
    assert p.returncode == 0, p.stderr
    result = json.loads(p.stdout.strip().splitlines()[-1])
    assert result["status"] == "previewed"
    assert result["piece"]["title"] == DRAFT["title"]
    assert FakeBackend.posts == []
    assert (tmp_path / "runs" / "run-hold" / "piece.json").is_file()

    a = run_sub(cfg, "approve", "--run-id", "run-hold")
    assert a.returncode == 0, a.stderr
    approved = json.loads(a.stdout.strip().splitlines()[-1])
    assert approved["status"] == "published" and approved["slug"] == "nota-prueba"
    assert len(FakeBackend.posts) == 1
    assert FakeBackend.posts[0]["idem"] == "run-hold"


def test_approve_refuses_a_run_without_a_previewed_piece(tmp_path, servers):
    api_port, backend_port = servers
    cfg = write_config(tmp_path, api_port, backend_port)
    a = run_sub(cfg, "approve", "--run-id", "run-nunca-corrido")
    assert a.returncode == 2
    assert "no previewed piece" in a.stderr
    assert FakeBackend.posts == []


def test_events_console_shows_runs_and_failures(tmp_path, servers):
    api_port, backend_port = servers
    cfg = write_config(tmp_path, api_port, backend_port)
    ok = run_pipeline(cfg, "--run-id", "run-bien")
    assert ok.returncode == 0, ok.stderr
    FakeApi.behavior = "fail"
    bad = run_pipeline(cfg, "--run-id", "run-mal")
    assert bad.returncode == 4
    ev = run_sub(cfg, "events")
    assert ev.returncode == 0, ev.stderr
    assert "run-bien" in ev.stdout and "published" in ev.stdout
    assert "run-mal" in ev.stdout and "FAIL" in ev.stdout


def test_websearch_context_must_name_an_earlier_node(tmp_path, servers):
    api_port, backend_port = servers
    nodes = [
        {"name": "draft", "adapter": "api", "role": "draft", "output": "json",
         "prompt": str(PROMPTS / "draft.md"),
         "context": {"web": {"websearch": {"queries_from": "evaluate"}}}},
        {"name": "evaluate", "adapter": "api", "role": "gate", "output": "json",
         "prompt": str(PROMPTS / "evaluate.md")},
    ]
    p = run_pipeline(write_config(tmp_path, api_port, backend_port, nodes=nodes))
    assert p.returncode == 2
    assert "not an earlier node" in p.stderr


def test_same_run_id_replays_without_double_publish(tmp_path, servers):
    api_port, backend_port = servers
    cfg = write_config(tmp_path, api_port, backend_port)
    first = run_pipeline(cfg, "--run-id", "run-replay")
    assert first.returncode == 0, first.stderr
    second = run_pipeline(cfg, "--run-id", "run-replay")
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout.strip().splitlines()[-1])["slug"] == "nota-prueba"
    assert len(FakeBackend.posts) == 1
