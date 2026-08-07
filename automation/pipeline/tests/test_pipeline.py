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

    def do_GET(self):
        out = json.dumps({"slug": "nota-prueba", "content_hash": "deadbeefcafebabe"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
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
    for s in (api, backend):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    yield api.server_address[1], backend.server_address[1]
    for s in (api, backend):
        s.shutdown()


def write_config(tmp: Path, api_port: int, backend_port: int, nodes=None, cli_cmd=None,
                 cli_stdin=False) -> Path:
    cfg = {
        "run_dir": "runs",
        "backend": {"base_url": f"http://127.0.0.1:{backend_port}", "token_env": "TEST_TOKEN"},
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
    return subprocess.run(
        [sys.executable, str(RUN), "--config", str(cfg), "--topic", "el software libre",
         "--author", "autor-test", "--section", "world", *extra],
        capture_output=True, text=True, env=env, cwd=ROOT, timeout=120)


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


def test_same_run_id_replays_without_double_publish(tmp_path, servers):
    api_port, backend_port = servers
    cfg = write_config(tmp_path, api_port, backend_port)
    first = run_pipeline(cfg, "--run-id", "run-replay")
    assert first.returncode == 0, first.stderr
    second = run_pipeline(cfg, "--run-id", "run-replay")
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout.strip().splitlines()[-1])["slug"] == "nota-prueba"
    assert len(FakeBackend.posts) == 1
