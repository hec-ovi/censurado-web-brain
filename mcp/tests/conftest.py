"""Shared fixtures: a stub newsroom repo the real server can drive with no stack up.

The stub is a fake `cli/censurado.py` that echoes back the argv it was handed (and the
contents of any file argument), so a test asserts on what the server actually invoked,
through the real subprocess path, without a backend, a GPU, or the network.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

STUB_CLI = '''#!/usr/bin/env python3
"""Stand-in for the operator CLI: echo the call back as JSON."""
import json, os, sys
from pathlib import Path

argv = sys.argv[1:]
mode = os.environ.get("STUB_MODE", "ok")
if mode == "fail":
    sys.stderr.write("ERROR: the stub was told to fail - fix the thing and retry.\\n")
    sys.exit(1)
if mode == "text":
    print("not json at all")
    sys.exit(0)
files = {}
for item in argv:
    p = Path(item)
    if p.is_file():
        files[p.name] = p.read_text(encoding="utf-8", errors="replace")
print(json.dumps({"argv": argv, "files": files,
                  "work": os.environ.get("CENSURADO_WORK", "")}))
'''


@pytest.fixture()
def stub_repo(tmp_path):
    """A repo shaped like censurado-web-brain, with the CLI replaced by the stub."""
    cli_dir = tmp_path / "cli"
    cli_dir.mkdir(parents=True)
    (cli_dir / "censurado.py").write_text(STUB_CLI, encoding="utf-8")
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / "deploy-cdn.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (tmp_path / ".env").write_text("NEWSROOM_OPERATOR_TOKEN=stub.token\n"
                                   "CLOUDFLARE_ACCOUNT_ID=abc123\n", encoding="utf-8")
    return tmp_path


@pytest.fixture()
def runner(stub_repo):
    from mcp_runner import CliRunner
    return CliRunner(repo=stub_repo, work_dir=stub_repo / ".mcp-work", timeout=60)


@pytest.fixture()
def server(runner):
    from server import Server
    return Server(runner=runner)


def call(server, name, arguments=None, msg_id=7):
    """One tools/call round-trip, returning the result payload."""
    response = server.handle({"jsonrpc": "2.0", "id": msg_id, "method": "tools/call",
                              "params": {"name": name, "arguments": arguments or {}}})
    assert "error" not in response, response
    return response["result"]


def argv_of(result):
    """The argv the stub CLI reports having been called with."""
    return json.loads(result["structuredContent"]["stdout"])["argv"]


def files_of(result):
    return json.loads(result["structuredContent"]["stdout"])["files"]
