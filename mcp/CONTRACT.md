# MCP layer contract

contractVersion: 1.0.0

## Purpose

Expose the whole newsroom (articles, authors, front-page layout, images, deploy, preflight) as one MCP tool surface, so an agent given only this server can run the portal.

## Inputs

- **`tools/call` params**: schema [schema/tool-call.schema.json](schema/tool-call.schema.json). Preconditions: `name` is a tool from `tools/list`; `arguments` conforms to that tool's `inputSchema`. Validation is fail-closed and happens here, not only in the client: a missing required argument, an unknown key, or a wrong type comes back as a tool result with `isError` and the correction to make, never as a subprocess call with unchecked argv.
- **`initialize` params**: standard MCP. The requested `protocolVersion` is echoed when it is one of `2024-11-05`, `2025-03-26`, `2025-06-18`, `2025-11-25`; otherwise the response carries `2025-11-25`.
- **`resources/read` params**: `uri` is one of `censurado://guide/operating`, `censurado://guide/layout`, `censurado://guide/style`, or `censurado://guide/rules/<lang>`. Any other uri is a `-32602` error.
- **Environment** (host config, not agent input): `CENSURADO_REPO` (default: this repo), `CENSURADO_WORK` (default: `<repo>/.mcp-work`), `CENSURADO_PYTHON`, `CENSURADO_MCP_TIMEOUT` (default 180s), `CENSURADO_MCP_SLOW_TIMEOUT` (default 1800s, for `stack_up` and `site_publish`). The operator token is read from the repo's `.env` by the CLI; this layer never reads, prints, or accepts it.

## Outputs

- **`tools/list` result**: schema [schema/tool-catalog.schema.json](schema/tool-catalog.schema.json). Postconditions: every tool carries a name, a title, a description, an `inputSchema` with `additionalProperties: false`, and an `outputSchema`.
- **Tool result (`structuredContent`) for every tool except `doctor`**: schema [schema/tool-result.schema.json](schema/tool-result.schema.json). Postconditions: the envelope is validated before it is sent (an off-contract envelope raises here rather than reaching the agent); `data` is present exactly when the verb printed JSON; streams are never truncated. `isError` is set on the result when `ok` is false.
- **`doctor` result (`structuredContent`)**: schema [schema/doctor-report.schema.json](schema/doctor-report.schema.json). Postconditions: `ready` is false if and only if some check is `FAIL`; `next_action` is present exactly when `ready` is false; no secret value ever appears in a row.
- **Side effects**: content changes land in the backend through the CLI. The only file this layer writes is under `CENSURADO_WORK`: the walk artifacts from `workflow_save`, the rendered-hero handoff, and the temp files it makes for long arguments (deleted after the call).

## Events

None. Every tool is request and response; there are no notifications from this server, and `listChanged` is false for both tools and resources.

## Errors (closed set)

Protocol errors (JSON-RPC `error`):
- `-32700` a line of input was not JSON (the connection stays open)
- `-32600` the message was not JSON-RPC 2.0
- `-32601` unknown method, or unknown tool name
- `-32602` unknown resource uri
- `-32603` a bug in this layer (never a traceback on the wire, and never a dropped connection)

Tool errors (a result with `isError: true`, which the agent can correct and retry):
- an argument that fails validation, reported with what the tool does accept
- a refusal: a destructive or public action called without `confirm: true`, or an argument combination the tool will not run (`exit_code` 2)
- a verb that exited non-zero, with the CLI's own actionable line in `stderr` (`exit_code` 124 means the verb hit its timeout)

## Dependencies

- The operator CLI (`cli/censurado.py`) as an argv-in, exit-code-and-streams-out boundary. This layer runs verbs; it never reimplements publishing, payload building, or auth.
- Nothing else. It talks to no service directly, except the doctor's read-only HTTP probe of a media URL it just uploaded.

## Invariants

- **One writer, one path.** Every content change goes through a CLI verb, so an agent on this server and an operator at the terminal cannot drift apart.
- **No secret ever crosses the boundary.** The token is not an input, not an output, and not a row in the doctor report; presence is reported, value never.
- **The filesystem is one directory.** `workflow_save` writes plain filenames under `CENSURADO_WORK` and nothing else; a name with a path separator is refused.
- **Public action is double-gated.** `site_publish` needs `confirm: true` here and `--yes` at the CLI. Nothing else in the surface is outward-facing.
- **Nothing is truncated.** Article bodies and verb output cross whole.
- **The manual travels with the connection.** An agent with no repo learns the two-world rule, the layout model, the writing path, and the compliance line from `initialize.instructions` and the tool descriptions.

## How to modify this blackbox safely

- Adding a tool: add the entry to `TOOLS` in `src/mcp_tools.py` with a description that teaches a bare agent when to reach for it, keep `additionalProperties: false`, and add a test in `tests/test_tools.py` asserting the exact argv it produces.
- Changing a result shape: it is the contract, so edit `schema/tool-result.schema.json` and `validate_envelope` together, and bump `contractVersion`.
- Changing a CLI verb this layer calls: the tests pin the argv, so a renamed flag fails here first. Fix the mapping, not the test.
- Run `.venv/bin/python -m pytest mcp/tests -q` from the repo root. The tests need no stack, no GPU, and no network: they drive the real server against a stub CLI.
