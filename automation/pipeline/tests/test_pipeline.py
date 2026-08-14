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
    behavior = "publish"   # publish | revise | respin-once | fail | loop-stream
    calls: list = []
    gate_calls = 0
    served_model = "fake"

    def do_GET(self):
        out = json.dumps({"data": [{"id": type(self).served_model}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(out)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        type(self).calls.append(body)
        if type(self).behavior == "fail":
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"boom")
            return
        if type(self).behavior == "loop-stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            piece = json.dumps({"choices": [{"delta": {"content": '"eeuu": "usa", ' * 8}}]})
            try:
                for _ in range(600):
                    self.wfile.write(f"data: {piece}\n\n".encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        if type(self).behavior == "cut-stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            piece = json.dumps({"choices": [{"delta": {"content": '{"grupos": [["a",'}}]})
            self.wfile.write(f"data: {piece}\n\n".encode())
            return
        prompt = body["messages"][-1]["content"]
        if "Actua como editor de mesa" in prompt:
            type(self).gate_calls += 1
            if type(self).behavior == "title-nit":
                obs = [{"nivel": "bloqueante", "detalle": "el titular usa un verbo debil"}]
            else:
                if type(self).behavior == "respin-once":
                    revise = type(self).gate_calls == 1
                else:
                    revise = type(self).behavior != "publish"
                obs = ([{"nivel": "bloqueante", "detalle": "motivo"}] if revise
                       else [{"nivel": "pulido", "detalle": "brillo"}])
            content = json.dumps({"observaciones": obs})
        elif "EXACTAMENTE" in prompt:
            content = json.dumps({**DRAFT, "title": "Titulo corregido"}, ensure_ascii=False)
        elif "MARCA-CONSULTAS" in prompt:
            content = json.dumps({"queries": ["consulta de prueba"],
                                  "read_urls": ["https://fuente.test/nota-1"]})
        elif "MARCA-TUIT" in prompt:
            content = json.dumps({**DRAFT, "body": "Parrafo uno.\n\n{{tweet:123}}\n\nCierre."},
                                 ensure_ascii=False)
        elif "historias valen la pena" in prompt:
            content = json.dumps({"candidatos": [
                {"titulo": "Historia uno", "descripcion": "La primera.",
                 "fuente": "https://fuente.test/nota-1?utm_source=rss"},
                {"titulo": "Historia dos", "descripcion": "La segunda.",
                 "fuente": "https://fuente.test/nota-2"}]}, ensure_ascii=False)
        elif "Un primer pase propuso" in prompt:
            content = json.dumps({"grupos": [["javier-milei", "milei"]]})
        elif "tablero de temas vigente" in prompt:
            content = json.dumps({"grupos": [["javier-milei", "milei"],
                                             ["desmonte", "destilacion"]]})
        elif "jefe de redacción" in prompt:
            content = json.dumps({"seleccion": [
                {"autor": "autor-test", "titulo": "Historia uno", "descripcion": "La primera.",
                 "portada_rank": 1, "imagen": False, "imagen_brief": ""},
                {"autor": "autor-test", "titulo": "Historia dos", "descripcion": "La segunda.",
                 "portada_rank": 2, "imagen": False, "imagen_brief": ""}]}, ensure_ascii=False)
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
    author_upserts: list = []
    used_urls: list = []
    puts: list = []

    def do_PUT(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        type(self).puts.append({"path": self.path, "body": body})
        out = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(out)

    def _upsert_author(self, body):
        type(self).author_upserts.append(body)
        type(self).used_urls = (body.get("metadata") or {}).get("used_urls", [])
        out = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(out)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        if self.path == "/authors":
            self._upsert_author(body)
            return
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
        elif self.path.startswith("/topics"):
            out = json.dumps({"topics": [{"slug": "tema-existente"}]}).encode()
        elif self.path.startswith("/articles:facets"):
            out = json.dumps({"topics": [
                {"value": "javier-milei", "count": 5}, {"value": "milei", "count": 2},
                {"value": "desmonte", "count": 3}, {"value": "destilacion", "count": 1},
                {"value": "otro-tema", "count": 1}]}).encode()
        elif self.path.split("?")[0] == "/articles":
            out = json.dumps({"articles": [{"slug": "nota-vieja", "title": "Nota vieja",
                                            "topics": ["milei", "otro-tema"]}]}).encode()
        elif self.path.startswith("/articles/"):
            out = json.dumps({"slug": self.path.rsplit("/", 1)[-1],
                              "content_hash": "deadbeefcafebabe",
                              "title": "Nota vieja", "body": "Cuerpo.",
                              "author": "autor-test", "section": "world",
                              "topics": ["milei", "otro-tema"],
                              "published_at": "2026-08-13T12:00:00Z",
                              "metadata": {}}).encode()
        elif self.path.startswith("/authors"):
            out = json.dumps([{"handle": "autor-test", "name": "Autor Test", "bio": "bio-prueba",
                               "style": "estilo-prueba: tercera persona.",
                               "metadata": {"who_i_am": "soy una prueba",
                                            "profile_topics": ["tema-perfil"],
                                            "few_shots_pos": [{"prompt": "p", "good": "EJEMPLO-SI"}],
                                            "few_shots_neg": [{"prompt": "p", "bad": "EJEMPLO-NO"}],
                                            "beat": "world",
                                            "used_urls": type(self).used_urls}}]).encode()
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
    FakeApi.behavior, FakeApi.calls, FakeApi.gate_calls = "publish", [], 0
    FakeApi.served_model = "fake"
    FakeBackend.posts, FakeBackend.author_upserts, FakeBackend.used_urls = [], [], []
    FakeBackend.puts = []
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
                 cli_stdin=False, websearch=None, batch=None, extra_adapters=None) -> Path:
    cfg = {
        "run_dir": "runs",
        "backend": {"base_url": f"http://127.0.0.1:{backend_port}", "token_env": "TEST_TOKEN"},
        **({"websearch": websearch} if websearch else {}),
        **({"batch": batch} if batch else {}),
        "adapters": {
            "api": {"base_url": f"http://127.0.0.1:{api_port}/v1", "model": "fake"},
            **({"cli": {"cmd": cli_cmd, **({"stdin": True} if cli_stdin else {})}}
               if cli_cmd else {}),
            **(extra_adapters or {}),
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


def run_sub(cfg: Path, sub: str, *extra: str, env_extra: dict | None = None):
    env = dict(os.environ, TEST_TOKEN="tok-test", **(env_extra or {}))
    return subprocess.run(
        [sys.executable, str(RUN), sub, "--config", str(cfg), *extra],
        capture_output=True, text=True, env=env, cwd=ROOT, timeout=300)


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
    meta = FakeBackend.posts[0]["body"]["metadata"]
    assert meta["description"] == "Una frase."
    assert meta["author_name"] == "Autor Test" and meta["author_bio"] == "bio-prueba"
    assert meta["card"] == {"type": "text"}
    art = tmp_path / "runs" / "run-pub"
    assert (art / "draft.json").is_file() and (art / "evaluate.json").is_file()


def test_second_api_adapter_and_node_model_override(tmp_path, servers):
    # A named api-kind adapter (the OpenRouter lane) can drive one node with its
    # own model, and a node-level "model" overrides the adapter's default; the
    # other nodes stay on the local default. The payload the endpoint receives is
    # the proof.
    api_port, backend_port = servers
    nodes = [
        {"name": "draft", "adapter": "api", "role": "draft", "output": "json",
         "prompt": str(PROMPTS / "draft.md")},
        {"name": "evaluate", "adapter": "gates", "model": "fake-max", "role": "gate",
         "output": "json", "prompt": str(PROMPTS / "evaluate.md")},
    ]
    gates = {"gates": {"kind": "api", "base_url": f"http://127.0.0.1:{api_port}/v1",
                       "model": "fake-big"}}
    p = run_pipeline(write_config(tmp_path, api_port, backend_port, nodes=nodes,
                                  extra_adapters=gates), "--run-id", "run-mixed")
    assert p.returncode == 0, p.stderr
    models = {c["messages"][-1]["content"][:30]: c["model"] for c in FakeApi.calls}
    gate_models = [c["model"] for c in FakeApi.calls
                   if "Actua como editor de mesa" in c["messages"][-1]["content"]]
    assert gate_models == ["fake-max"], models
    draft_models = [c["model"] for c in FakeApi.calls
                    if "Actua como editor de mesa" not in c["messages"][-1]["content"]]
    assert draft_models == ["fake"], models


def test_config_rejects_bad_adapter_kind_and_cli_model_override(tmp_path, servers):
    api_port, backend_port = servers
    bad_kind = write_config(tmp_path, api_port, backend_port,
                            extra_adapters={"raro": {"kind": "grpc", "base_url": "x", "model": "y"}})
    p = run_pipeline(bad_kind)
    assert p.returncode == 2 and "adapters.raro.kind" in p.stderr

    fake_cli = tmp_path / "fake-agent.py"
    fake_cli.write_text("print('x')\n")
    nodes = [
        {"name": "draft", "adapter": "cli", "model": "no-va", "role": "draft",
         "output": "json", "prompt": str(PROMPTS / "draft.md")},
    ]
    p = run_pipeline(write_config(tmp_path, api_port, backend_port, nodes=nodes,
                                  cli_cmd=[sys.executable, str(fake_cli), "{prompt}"]))
    assert p.returncode == 2 and "only an api-kind adapter takes a model override" in p.stderr


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
    dprompt = tmp_path / "draft-con-manual.md"
    dprompt.write_text("Manual:\n{skill}\n\nCarta:\n{persona}\n\nReglas:\n{reglas}\n\n"
                       "Temas:\n{temas}\n\nRecientes:\n{relacionadas}\n\n"
                       "Escribi la nota de {topic}.")
    nodes = [
        {"name": "draft", "adapter": "api", "role": "draft", "output": "json",
         "prompt": str(dprompt),
         "context": {"skill": {"file": str(manual)},
                     "persona": {"persona": True},
                     "reglas": {"editorial": "es"},
                     "temas": {"topics": True},
                     "relacionadas": {"articles": {"limit": 5}}}},
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
    assert "tema-existente" in draft_prompt
    assert "nota-vieja: Nota vieja" in draft_prompt
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


def test_tweet_markers_attach_their_cards_on_publish(tmp_path, servers):
    api_port, backend_port = servers
    toolkit = tmp_path / "fake-toolkit.py"
    toolkit.write_text(
        "import json, sys\n"
        "assert sys.argv[1] == 'tweet' and sys.argv[2] == '123'\n"
        "print(json.dumps({'id': '123', 'handle': 'prueba', 'text': 'hola'}))\n")
    dprompt = tmp_path / "draft-con-tuit.md"
    dprompt.write_text("MARCA-TUIT: escribi la nota de {topic}.")
    nodes = [
        {"name": "draft", "adapter": "api", "role": "draft", "output": "json",
         "prompt": str(dprompt)},
        {"name": "evaluate", "adapter": "api", "role": "gate", "output": "json",
         "prompt": str(PROMPTS / "evaluate.md")},
    ]
    cfg = write_config(tmp_path, api_port, backend_port, nodes=nodes)
    raw = json.loads(cfg.read_text())
    raw["toolkit"] = {"cmd": [sys.executable, str(toolkit)]}
    cfg.write_text(json.dumps(raw))
    p = run_pipeline(cfg)
    assert p.returncode == 0, p.stderr
    posted = FakeBackend.posts[0]["body"]
    assert "{{tweet:123}}" in posted["body"]
    assert posted["metadata"]["tweets"] == [{"id": "123", "handle": "prueba", "text": "hola"}]


def test_title_observations_route_to_the_corrector(tmp_path, servers):
    api_port, backend_port = servers
    FakeApi.behavior = "title-nit"
    p = run_pipeline(write_config(tmp_path, api_port, backend_port))
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout.strip().splitlines()[-1])["status"] == "published"
    assert FakeApi.gate_calls == 1
    assert len(FakeBackend.posts) == 1


def test_gate_respin_rewrites_and_passes(tmp_path, servers):
    api_port, backend_port = servers
    FakeApi.behavior = "respin-once"
    nodes = [
        {"name": "draft", "adapter": "api", "role": "draft", "output": "json",
         "prompt": str(PROMPTS / "draft.md")},
        {"name": "evaluate", "adapter": "api", "role": "gate", "output": "json",
         "prompt": str(PROMPTS / "evaluate.md"),
         "respin": {"prompt": str(PROMPTS / "respin.md"), "target": "draft", "passes": 2}},
    ]
    p = run_pipeline(write_config(tmp_path, api_port, backend_port, nodes=nodes),
                     "--run-id", "run-respin")
    assert p.returncode == 0, p.stderr
    assert len(FakeBackend.posts) == 1
    assert FakeBackend.posts[0]["body"]["title"] == "Titulo corregido"
    art = tmp_path / "runs" / "run-respin"
    assert (art / "draft-respin-1.json").is_file()
    assert (art / "evaluate-respin-1.json").is_file()


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


BATCH = {"concurrency": 1, "used_urls_cap": 3,
         "candidates_prompt": str(PROMPTS / "candidates.md"),
         "jefe_prompt": str(PROMPTS / "jefe.md")}


def test_registry_caps_and_rotates(tmp_path, servers, monkeypatch):
    monkeypatch.syspath_prepend(str(RUN.parent))
    from src.registry import UsedUrls, normalize_url
    monkeypatch.setenv("TEST_TOKEN", "tok-test")
    assert normalize_url("HTTPS://Sitio.Test/Nota/?utm_source=rss&x=1#frag") == \
        "https://sitio.test/Nota?x=1"
    _, backend_port = servers
    reg = UsedUrls({"base_url": f"http://127.0.0.1:{backend_port}",
                    "token_env": "TEST_TOKEN"}, cap=3)
    reg.register("autor-test", ["https://a.test/1", "https://a.test/2"])
    assert FakeBackend.used_urls == ["https://a.test/1", "https://a.test/2"]
    reg.register("autor-test", ["https://a.test/3", "https://a.test/4"])
    assert FakeBackend.used_urls == ["https://a.test/3", "https://a.test/4",
                                     "https://a.test/1"]


def test_feeds_context_omits_used_titulars(tmp_path, servers):
    api_port, backend_port = servers
    FakeBackend.used_urls = ["https://fuente.test/nota-1"]
    prompt = tmp_path / "draft-filtrado.md"
    prompt.write_text("Titulares:\n{titulares}\n\nNota de {topic}.")
    nodes = [
        {"name": "draft", "adapter": "api", "role": "draft", "output": "json",
         "prompt": str(prompt), "context": {"titulares": {"feeds": {"hours": 48}}}},
        {"name": "evaluate", "adapter": "api", "role": "gate", "output": "json",
         "prompt": str(PROMPTS / "evaluate.md")},
    ]
    p = run_pipeline(write_config(tmp_path, api_port, backend_port, nodes=nodes))
    assert p.returncode == 0, p.stderr
    sent = FakeApi.calls[0]["messages"][-1]["content"]
    assert "TITULAR-DE-PRUEBA" not in sent
    assert "omitidos" in sent


def test_batch_preview_holds_the_edition(tmp_path, servers):
    api_port, backend_port = servers
    cfg = write_config(tmp_path, api_port, backend_port, batch=BATCH)
    p = run_sub(cfg, "batch", "--run-id", "lote-1",
                "--directive", "MARCA-DIRECTIVA: solo energia")
    assert p.returncode == 0, p.stderr
    result = json.loads(p.stdout.strip().splitlines()[-1])
    assert result["status"] == "batch-previewed"
    assert [a["status"] for a in result["articles"]] == ["previewed", "previewed"]
    lote = tmp_path / "runs" / "lote-1"
    assert (lote / "plan.json").is_file()
    assert json.loads((lote / "plan.json").read_text())["directiva"].startswith("MARCA-DIRECTIVA")
    assert (lote / "candidates-autor-test.json").is_file()
    assert (tmp_path / "runs" / "lote-1-n1" / "piece.json").is_file()
    # The operator directive reached both the candidates and the jefe prompts.
    steering = [c["messages"][-1]["content"] for c in FakeApi.calls
                if "MARCA-DIRECTIVA" in c["messages"][-1]["content"]]
    assert any("historias valen la pena" in s for s in steering), "candidates saw it"
    assert any("jefe de redacción" in s for s in steering), "the jefe saw it"
    assert FakeBackend.posts == []
    assert FakeBackend.used_urls == ["https://fuente.test/nota-2",
                                     "https://fuente.test/nota-1"]


def test_batch_auto_publishes_in_portada_order(tmp_path, servers):
    api_port, backend_port = servers
    cfg = write_config(tmp_path, api_port, backend_port, batch=BATCH)
    p = run_sub(cfg, "batch", "--run-id", "lote-auto", "--mode", "auto")
    assert p.returncode == 0, p.stderr
    result = json.loads(p.stdout.strip().splitlines()[-1])
    assert result["status"] == "batch-published"
    assert len(FakeBackend.posts) == 2
    assert FakeBackend.posts[0]["idem"] == "lote-auto-n2"
    assert FakeBackend.posts[1]["idem"] == "lote-auto-n1"


def test_batch_resume_reuses_the_plan(tmp_path, servers):
    api_port, backend_port = servers
    cfg = write_config(tmp_path, api_port, backend_port, batch=BATCH)
    first = run_sub(cfg, "batch", "--run-id", "lote-re")
    assert first.returncode == 0, first.stderr
    candidate_calls = sum("historias valen la pena" in c["messages"][-1]["content"]
                          for c in FakeApi.calls)
    second = run_sub(cfg, "batch", "--run-id", "lote-re")
    assert second.returncode == 0, second.stderr
    after = sum("historias valen la pena" in c["messages"][-1]["content"]
                for c in FakeApi.calls)
    assert after == candidate_calls
    assert FakeBackend.posts == []


def test_same_run_id_replays_without_double_publish(tmp_path, servers):
    api_port, backend_port = servers
    cfg = write_config(tmp_path, api_port, backend_port)
    first = run_pipeline(cfg, "--run-id", "run-replay")
    assert first.returncode == 0, first.stderr
    second = run_pipeline(cfg, "--run-id", "run-replay")
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout.strip().splitlines()[-1])["slug"] == "nota-prueba"
    assert len(FakeBackend.posts) == 1


def test_topics_confirms_the_map_and_cleanses_the_board(tmp_path, servers):
    # The clustering pass proposes two groups; the confirm pass keeps only the
    # real one, and the cleanse applies it over PUT /articles/{slug}.
    api_port, backend_port = servers
    cfg = write_config(tmp_path, api_port, backend_port)
    p = run_sub(cfg, "topics", "--run-id", "temas-test",
                env_extra={"NEWSROOM_PUBLISH_BASE_URL": f"http://127.0.0.1:{backend_port}",
                           "NEWSROOM_OPERATOR_TOKEN": "tok-test"})
    assert p.returncode == 0, p.stderr
    result = json.loads(p.stdout.strip().splitlines()[-1])
    assert result["status"] == "topics-cleansed"
    assert result["articles_changed"] == 1 and result["applied"] == 1
    topic_calls = [c for c in FakeApi.calls if "temas" in c["messages"][-1]["content"]]
    assert all(c["temperature"] == 0.2 for c in topic_calls)
    mapa = json.loads((tmp_path / "runs" / "temas-test" / "mapa.json").read_text())
    assert mapa == {"milei": "javier-milei"}
    put = FakeBackend.puts[-1]
    assert put["path"] == "/articles/nota-vieja"
    assert put["body"]["topics"] == ["javier-milei", "otro-tema"]


def test_topics_kills_a_degenerate_output_loop_quickly(tmp_path, servers):
    # A runaway generation repeating one pattern must die at the detector,
    # not at the timeout, and the failure names the real cause.
    api_port, backend_port = servers
    FakeApi.behavior = "loop-stream"
    cfg = write_config(tmp_path, api_port, backend_port)
    import time
    t0 = time.monotonic()
    p = run_sub(cfg, "topics", "--run-id", "temas-loop",
                env_extra={"NEWSROOM_PUBLISH_BASE_URL": f"http://127.0.0.1:{backend_port}",
                           "NEWSROOM_OPERATOR_TOKEN": "tok-test"})
    assert p.returncode == 4, p.stderr
    assert "degenerate output loop" in p.stderr
    assert time.monotonic() - t0 < 60


def test_topics_refuses_a_lane_serving_another_model(tmp_path, servers):
    api_port, backend_port = servers
    FakeApi.served_model = "otro-modelo"
    cfg = write_config(tmp_path, api_port, backend_port)
    p = run_sub(cfg, "topics", "--run-id", "temas-mismatch")
    assert p.returncode == 2, p.stderr
    assert "lane serves otro-modelo" in p.stderr
    assert FakeApi.calls == []


def test_topics_treats_an_unterminated_stream_as_a_failed_call(tmp_path, servers):
    # A stream that ends without its terminator never reaches the parser as a
    # truncated answer: the call fails, the retry fails the same way, exit 4.
    api_port, backend_port = servers
    FakeApi.behavior = "cut-stream"
    cfg = write_config(tmp_path, api_port, backend_port)
    p = run_sub(cfg, "topics", "--run-id", "temas-cut",
                env_extra={"NEWSROOM_PUBLISH_BASE_URL": f"http://127.0.0.1:{backend_port}",
                           "NEWSROOM_OPERATOR_TOKEN": "tok-test"})
    assert p.returncode == 4, p.stderr
    assert "stream ended early" in p.stderr
