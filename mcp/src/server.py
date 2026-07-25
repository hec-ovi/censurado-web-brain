"""The MCP server: the whole newsroom as one tool surface, over stdio.

Speaks MCP over newline-delimited JSON-RPC on stdin/stdout (the stdio transport), with no
dependencies beyond the standard library, so it runs from a bare checkout with no install
step. Protocol revision 2025-11-25, negotiating down to whatever an older client asks for.

The point of this layer is isolation: an agent given ONLY this server, with the stack up, can
run the whole portal. It has no shell, no filesystem, and no repo, so everything it needs to
operate well (the two-world rule, the layout model, the editorial bar, the compliance line)
is carried in the initialize instructions and the tool descriptions, not in files it cannot
read.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp_runner import CliRunner, RunnerError, validate_envelope  # noqa: E402
from mcp_tools import BY_NAME, public_tools, validate_args  # noqa: E402

SERVER_NAME = "censurado"
SERVER_VERSION = "1.0.0"
LATEST_PROTOCOL = "2025-11-25"
SUPPORTED_PROTOCOLS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR = (
    -32700, -32600, -32601, -32602, -32603)

INSTRUCTIONS = """\
You are operating a live AI news portal through this server. Everything the portal knows
(articles, authors, sources, the front page) lives behind these tools; there is nothing else
to read and nothing to install.

Start here
- Call `doctor` first. It tells you whether the stack, the content plane, the image lane and
  the deploy lane actually work. If a core service is down, call `stack_up` ONCE; if that
  fails, relay the error to the human and stop.
- Every tool returns {ok, exit_code, stdout, stderr, data}. When a call fails, read `stderr`:
  it carries an actionable line. Relay it rather than retrying the same call in a loop.

Two worlds, never confuse them
- `article_create` and `article_update` stage to the LOCAL site. The local site repaints
  itself within a few seconds, so you never need to rebuild or re-check anything to make a
  change appear.
- `site_publish` deploys everything to the PUBLIC internet. It is irreversible and it ships
  whatever is currently staged. Show the human what will go live, get an explicit yes, then
  pass confirm=true. Nothing else here is public-facing.

Writing an article
- Read `editorial_style` and `editorial_rules` for the author's language, and `author_get`
  for the persona, before drafting. Write in that author's voice.
- For a serious piece, walk `workflow_step` node by node (start with mode='single-article')
  and save each artifact it names with `workflow_save`. The walk enforces the sourcing floor,
  the accurate-headline gate, and the respin loop. Do not draft a whole piece from memory.
- Headlines must be accurate to the body. Attribute claims to named sources in plain text.
- The newest article leads the front page. To publish without taking the lead, backdate
  `published_at`.

The front page
- `portada_set` arranges one day: array order is page order, entries[0] is the lead (full
  width by position), role 'important' is a double card (full row), role '' is a single (half
  row, two per row). Never leave a gap: promote a lone trailing single to 'important'.
- `recomendado_set` is a separate GLOBAL rail of up to 10 slugs that persists across days.
- Get slugs from `article_list(day=...)`, never from memory: a slug that does not exist that
  day is silently dropped and your intended lead changes.

Images and authors
- `image_generate` renders a hero and remembers it, so the next `article_create` attaches it
  by itself. `media_upload` takes bytes you carry. An author's portrait is just a media path
  passed to `author_update(avatar=...)`.

Compliance
- The site is openly AI-generated and its authors are fictional personas. Never impersonate a
  real person, never present a persona as a real journalist, keep the editorial notice, and
  mark opinion and satire as such.
"""

LAYOUT_GUIDE = """\
# The front-page layout model

Three sizes exist and people name them loosely:

| the human says | what it is | how you set it |
|---|---|---|
| the lead, the main story, the day's portada | the full-bleed hero across the top | make it the FIRST entry; role is ignored there |
| double card, full width, destacada | a card spanning both columns on its own row | role: "important" |
| single card, normal card | half a row; singles pair up two per row | role: "" |

Desktop is a two-column grid filled in index order, left to right, top to bottom. Entries
0 to 5 with entry 3 marked "important":

    row 1  [ 0  the lead (hero) .............. ]
    row 2  [ 1 single ]  [ 2 single ]
    row 3  [ 3  double card ................... ]
    row 4  [ 4 single ]  [ 5 single ]

Rules that matter:
- Never leave a gap. The singles between two full-row cards must come out even, or the last
  one sits beside an empty cell. Fix a lone trailing single by promoting it to "important".
- Keep doubles few, or they stop reading as featured.
- Mobile is one column in the same order. Arrange for desktop; mobile follows.
- Same-day articles you leave out still show: they append after your entries as singles.
- The Recomendado rail is NOT part of a day's plan. It is one global list of up to 10 slugs
  that stays on the front page until you change it.
"""

RESOURCES = [
    {"uri": "censurado://guide/operating", "name": "operating-manual",
     "title": "How to operate this portal",
     "description": "The same manual served with the connection: the two-world rule, the "
                    "writing path, the front page, compliance.",
     "mimeType": "text/markdown"},
    {"uri": "censurado://guide/layout", "name": "layout-model",
     "title": "The front-page layout model",
     "description": "Lead, double card, single card, and the no-gaps rule.",
     "mimeType": "text/markdown"},
    {"uri": "censurado://guide/style", "name": "editorial-style",
     "title": "The editorial style guide",
     "description": "The newsroom voice and house rules, read live from the recipe.",
     "mimeType": "text/markdown"},
]


class Server:
    def __init__(self, runner: CliRunner | None = None, out=None):
        self.runner = runner or CliRunner()
        self.out = out or sys.stdout
        self.protocol = LATEST_PROTOCOL

    # -- transport -----------------------------------------------------------

    def serve(self, stream=None) -> int:
        stream = stream or sys.stdin
        for line in stream:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                self._send({"jsonrpc": "2.0", "id": None,
                            "error": {"code": PARSE_ERROR, "message": f"invalid JSON: {exc}"}})
                continue
            response = self.handle(message)
            if response is not None:
                self._send(response)
        return 0

    def _send(self, message: dict) -> None:
        self.out.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.out.flush()

    # -- dispatch ------------------------------------------------------------

    def handle(self, message):
        """One JSON-RPC message in, one response out (or None for a notification)."""
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return {"jsonrpc": "2.0", "id": None,
                    "error": {"code": INVALID_REQUEST, "message": "not a JSON-RPC 2.0 message"}}
        method, msg_id = message.get("method"), message.get("id")
        params = message.get("params") or {}
        if msg_id is None:                      # a notification: never answered
            return None
        try:
            result = self.dispatch(method, params)
        except JsonRpcError as exc:
            return {"jsonrpc": "2.0", "id": msg_id,
                    "error": {"code": exc.code, "message": str(exc)}}
        except Exception as exc:                # a server bug must not kill the connection
            return {"jsonrpc": "2.0", "id": msg_id,
                    "error": {"code": INTERNAL_ERROR,
                              "message": f"internal error in {method}: {type(exc).__name__}: {exc}"}}
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def dispatch(self, method, params):
        if method == "initialize":
            return self.initialize(params)
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": public_tools()}
        if method == "tools/call":
            return self.call_tool(params)
        if method == "resources/list":
            return {"resources": RESOURCES}
        if method == "resources/read":
            return self.read_resource(params)
        raise JsonRpcError(METHOD_NOT_FOUND, f"unknown method: {method}")

    def initialize(self, params):
        asked = str((params or {}).get("protocolVersion") or "")
        self.protocol = asked if asked in SUPPORTED_PROTOCOLS else LATEST_PROTOCOL
        return {
            "protocolVersion": self.protocol,
            "capabilities": {"tools": {"listChanged": False},
                             "resources": {"subscribe": False, "listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "title": "Censurado newsroom",
                           "version": SERVER_VERSION,
                           "description": "Run a live AI news portal: articles, authors, front "
                                          "page layout, images, and the public deploy."},
            "instructions": INSTRUCTIONS,
        }

    # -- tools ---------------------------------------------------------------

    def call_tool(self, params):
        name = (params or {}).get("name")
        tool = BY_NAME.get(name)
        if tool is None:
            raise JsonRpcError(METHOD_NOT_FOUND, f"unknown tool: {name}")
        args = (params or {}).get("arguments") or {}
        problem = validate_args(tool, args)
        if problem:
            return _tool_error(f"ERROR: {problem}.")
        try:
            result = tool["handler"](args, self.runner)
        except RunnerError as exc:
            return _tool_error(f"ERROR: {exc}")
        except KeyError as exc:                 # a required argument the schema let through
            return _tool_error(f"ERROR: missing required argument: {exc}")
        if tool.get("outputSchema") is None:
            validate_envelope(result)
        text = tool["text"](result) if tool.get("text") else _envelope_text(result)
        payload = {"content": [{"type": "text", "text": text}], "structuredContent": result}
        if isinstance(result, dict) and result.get("ok") is False:
            payload["isError"] = True
        return payload

    # -- resources -----------------------------------------------------------

    def read_resource(self, params):
        uri = str((params or {}).get("uri") or "")
        if uri == "censurado://guide/operating":
            text = INSTRUCTIONS
        elif uri == "censurado://guide/layout":
            text = LAYOUT_GUIDE
        elif uri == "censurado://guide/style":
            text = self.runner.run(["style"]).get("stdout", "")
        elif uri.startswith("censurado://guide/rules/"):
            lang = uri.rsplit("/", 1)[-1] or "es"
            text = self.runner.run(["editorial-rules", lang]).get("stdout", "")
        else:
            raise JsonRpcError(INVALID_PARAMS, f"unknown resource: {uri}")
        return {"contents": [{"uri": uri, "mimeType": "text/markdown", "text": text}]}


class JsonRpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def _tool_error(text: str) -> dict:
    """An agent-correctable failure: a result with isError, not a protocol error, so the model
    gets the message back and can fix its call."""
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _envelope_text(result: dict) -> str:
    """The human-readable side of a verb result: what the verb printed, plus its notes."""
    parts = [(result.get("stdout") or "").rstrip(), (result.get("stderr") or "").rstrip()]
    text = "\n".join(p for p in parts if p)
    return text or f"(the {result.get('verb', 'verb')} produced no output; "\
                   f"exit {result.get('exit_code')})"


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--version" in argv:
        print(f"{SERVER_NAME} {SERVER_VERSION} (MCP {LATEST_PROTOCOL})")
        return 0
    if "--self-check" in argv:                  # wiring check that needs no client
        tools = public_tools()
        print(f"{SERVER_NAME} {SERVER_VERSION}: {len(tools)} tools, "
              f"repo {CliRunner().repo}, work dir {CliRunner().work_dir}")
        return 0
    return Server().serve()


if __name__ == "__main__":
    sys.exit(main())
