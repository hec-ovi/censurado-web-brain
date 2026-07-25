"""The MCP protocol surface, driven through the real server.

Covers the handshake, the catalog, error reporting, resources, and one full stdio session
against a spawned server process, which is how a client actually meets this layer.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from conftest import call
from server import INVALID_PARAMS, LATEST_PROTOCOL, METHOD_NOT_FOUND, PARSE_ERROR

SRC = Path(__file__).resolve().parents[1] / "src"


# ----- handshake -----


def test_initialize_echoes_a_supported_protocol_and_carries_the_manual(server):
    result = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                            "params": {"protocolVersion": "2025-06-18", "capabilities": {}}})["result"]
    # The client asked for a revision this server supports, so it is answered with that one.
    assert result["protocolVersion"] == "2025-06-18"
    assert result["capabilities"]["tools"] == {"listChanged": False}
    assert result["serverInfo"]["name"] == "censurado"
    # The instructions ARE the isolation: an agent with only this server learns the two-world
    # rule, the layout model, and the compliance line from here.
    instructions = result["instructions"]
    for anchor in ("site_publish", "doctor", "LOCAL", "PUBLIC", "fictional personas"):
        assert anchor in instructions


def test_initialize_falls_back_to_the_latest_known_revision(server):
    result = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                            "params": {"protocolVersion": "1999-01-01"}})["result"]
    assert result["protocolVersion"] == LATEST_PROTOCOL


def test_a_notification_is_never_answered(server):
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_ping_answers_empty(server):
    assert server.handle({"jsonrpc": "2.0", "id": 3, "method": "ping"})["result"] == {}


def test_unknown_method_is_a_protocol_error(server):
    error = server.handle({"jsonrpc": "2.0", "id": 4, "method": "teleport"})["error"]
    assert error["code"] == METHOD_NOT_FOUND


def test_a_non_jsonrpc_message_is_rejected(server):
    error = server.handle({"id": 1, "method": "initialize"})["error"]
    assert error["code"] == -32600


# ----- the catalog -----


def test_every_tool_declares_a_usable_contract(server):
    tools = server.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/list"})["result"]["tools"]
    assert len(tools) >= 30
    names = [t["name"] for t in tools]
    assert len(names) == len(set(names))
    for tool in tools:
        assert re.fullmatch(r"[a-z][a-z0-9_]{0,63}", tool["name"]), tool["name"]
        # A bare agent has nothing but these strings to work from, so a thin description is
        # a real defect, not a style nit.
        assert len(tool["description"]) > 80, tool["name"]
        schema = tool["inputSchema"]
        assert schema["type"] == "object" and schema["additionalProperties"] is False
        assert tool["outputSchema"]["type"] == "object"
        for required in schema.get("required", []):
            assert required in schema["properties"], (tool["name"], required)


def test_the_catalog_covers_the_whole_operating_surface(server):
    names = {t["name"] for t in
             server.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/list"})["result"]["tools"]}
    # One tool per thing an operator does: articles, layout, the rail, authors and their
    # pictures and prompts, images, the public deploy, and the preflight.
    assert {"article_create", "article_update", "article_delete", "article_list", "article_get",
            "portada_set", "portada_get", "recomendado_set", "recomendado_get",
            "author_create", "author_update", "author_delete", "author_get", "author_list",
            "author_sources_set", "media_upload", "image_generate", "prompt_get", "prompt_set",
            "site_publish", "doctor", "stack_up", "stack_status"} <= names


# ----- calling tools -----


def test_unknown_tool_is_a_protocol_error(server):
    response = server.handle({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                              "params": {"name": "make_coffee", "arguments": {}}})
    assert response["error"]["code"] == METHOD_NOT_FOUND


def test_a_bad_argument_comes_back_as_a_correctable_tool_error(server):
    result = call(server, "article_get", {"slug": "x", "colour": "blue"})
    assert result["isError"] is True
    assert "unknown argument(s): colour" in result["content"][0]["text"]


def test_a_missing_required_argument_is_reported_not_crashed(server):
    result = call(server, "article_get", {})
    assert result["isError"] is True
    assert "missing required argument: slug" in result["content"][0]["text"]


def test_a_wrong_type_is_reported(server):
    result = call(server, "recomendado_set", {"slugs": "a,b"})
    assert result["isError"] is True
    assert "slugs must be an array" in result["content"][0]["text"]


def test_a_failing_verb_returns_iserror_with_the_actionable_line(server, monkeypatch):
    monkeypatch.setenv("STUB_MODE", "fail")
    result = call(server, "article_list", {})
    assert result["isError"] is True
    assert result["structuredContent"]["ok"] is False
    assert "fix the thing and retry" in result["structuredContent"]["stderr"]


def test_a_successful_call_carries_both_text_and_structured_output(server):
    result = call(server, "article_list", {"day": "2026-07-01"})
    assert result.get("isError") is None
    envelope = result["structuredContent"]
    assert envelope["ok"] is True and envelope["exit_code"] == 0
    # stdout was JSON, so it is handed over parsed as well as raw.
    assert envelope["data"]["argv"] == ["archive", "--day", "2026-07-01"]
    assert result["content"][0]["text"].startswith("{")


def test_non_json_output_carries_no_data_field(server, monkeypatch):
    monkeypatch.setenv("STUB_MODE", "text")
    envelope = call(server, "author_list", {})["structuredContent"]
    assert envelope["ok"] is True and "data" not in envelope
    assert envelope["stdout"].strip() == "not json at all"


# ----- resources -----


def test_resources_are_listed_and_readable(server):
    listed = server.handle({"jsonrpc": "2.0", "id": 8, "method": "resources/list"})["result"]
    uris = [r["uri"] for r in listed["resources"]]
    assert "censurado://guide/layout" in uris and "censurado://guide/operating" in uris
    layout = server.handle({"jsonrpc": "2.0", "id": 9, "method": "resources/read",
                            "params": {"uri": "censurado://guide/layout"}})["result"]
    text = layout["contents"][0]["text"]
    assert "important" in text and "the lead" in text


def test_an_unknown_resource_is_an_invalid_params_error(server):
    response = server.handle({"jsonrpc": "2.0", "id": 10, "method": "resources/read",
                              "params": {"uri": "censurado://nope"}})
    assert response["error"]["code"] == INVALID_PARAMS


# ----- the transport -----


def test_serve_reports_a_parse_error_and_keeps_going(server):
    import io
    out = io.StringIO()
    server.out = out
    server.serve(io.StringIO('not json\n{"jsonrpc":"2.0","id":2,"method":"ping"}\n'))
    first, second = [json.loads(line) for line in out.getvalue().splitlines()]
    assert first["error"]["code"] == PARSE_ERROR
    assert second["result"] == {}      # the bad line did not take the connection down


def test_a_real_stdio_session_against_a_spawned_server(stub_repo):
    """The way a client actually meets this layer: spawn the server, handshake, list, call."""
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": LATEST_PROTOCOL, "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "author_get", "arguments": {"id": "ana"}}},
    ]
    proc = subprocess.run(
        [sys.executable, str(SRC / "server.py")],
        input="".join(json.dumps(m) + "\n" for m in messages),
        capture_output=True, text=True, timeout=120,
        env={"PATH": "/usr/bin:/bin", "CENSURADO_REPO": str(stub_repo),
             "CENSURADO_WORK": str(stub_repo / ".mcp-work"), "HOME": str(stub_repo)},
    )
    assert proc.returncode == 0, proc.stderr
    replies = {json.loads(line)["id"]: json.loads(line) for line in proc.stdout.splitlines()}
    # The notification got no reply, so exactly the three requests came back.
    assert set(replies) == {1, 2, 3}
    assert replies[1]["result"]["protocolVersion"] == LATEST_PROTOCOL
    assert len(replies[2]["result"]["tools"]) >= 30
    envelope = replies[3]["result"]["structuredContent"]
    assert envelope["data"]["argv"] == ["persona", "ana"]
    # Every child gets the pinned scratch dir, which is what makes the image handoff work.
    assert envelope["data"]["work"] == str(stub_repo / ".mcp-work")


def test_the_server_reports_its_wiring_without_a_client():
    proc = subprocess.run([sys.executable, str(SRC / "server.py"), "--self-check"],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0
    assert "tools" in proc.stdout


def test_handle_never_leaks_a_traceback(server, monkeypatch):
    """A bug inside a handler must come back as an error object, not kill the connection."""
    import mcp_tools
    monkeypatch.setitem(mcp_tools.BY_NAME["author_list"], "handler",
                        lambda args, runner: 1 / 0)
    response = server.handle({"jsonrpc": "2.0", "id": 11, "method": "tools/call",
                              "params": {"name": "author_list", "arguments": {}}})
    assert response["error"]["code"] == -32603
    assert "ZeroDivisionError" in response["error"]["message"]
