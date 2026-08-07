# The MCP layer

The whole newsroom as one MCP server. Point any MCP client at it and, with the stack up, that
agent can run the portal: write, edit and take down articles, arrange the front page, curate
the recommended rail, create and rewrite authors (voice, prompts, portrait), render heroes,
and push the site live. No shell, no repo checkout, no skill files on the agent's side.

It is a thin server over the operator CLI: every tool runs one `cli/censurado.py` verb as a
child process. That is deliberate. The CLI stays the single writer, so an agent on MCP and a
human at the terminal go through the same code and cannot drift apart.

Stdlib only, no install. Protocol revision 2025-11-25, negotiating down for older clients.

## Wire it up

Any MCP client that speaks stdio works. Claude Code:

    claude mcp add censurado -- python3 /path/to/censurado-web-brain/mcp/src/server.py

Or in a client config file:

```json
{
  "mcpServers": {
    "censurado": {
      "command": "python3",
      "args": ["/path/to/censurado-web-brain/mcp/src/server.py"]
    }
  }
}
```

The server must run on the host that has the repo and its `.env` (that is where the operator
token lives, and it never crosses the wire). Override the checkout with `CENSURADO_REPO` if
you launch it from elsewhere.

Check the wiring without a client:

    python3 mcp/src/server.py --self-check
    python3 mcp/src/mcp_doctor.py            # the full preflight, as JSON

## The surface

33 tools, one per operation:

| group | tools |
|---|---|
| health | `doctor`, `stack_status`, `stack_up` |
| articles | `article_list`, `article_get`, `article_create`, `article_update`, `article_delete`, `sections_list` |
| front page | `portada_get`, `portada_set`, `recomendado_get`, `recomendado_set` |
| authors | `author_list`, `author_get`, `author_create`, `author_update`, `author_sync_byline`, `author_delete`, `author_sources_get`, `author_sources_set`, `source_catalog` |
| topics | `topics_inventory`, `topic_remove` |
| media | `media_upload`, `image_generate` |
| recipe | `editorial_style`, `editorial_rules`, `prompt_get`, `prompt_set`, `workflow_step`, `workflow_save` |
| going live | `site_publish` |

Two things are gated twice, here and at the CLI: anything destructive (`article_delete`,
`author_delete`, `topic_remove`) and the one public action (`site_publish`)
refuse without `confirm: true`.

Every tool returns the same envelope: `{ok, verb, exit_code, stdout, stderr, data}`, with
`data` present when the verb printed JSON. Nothing is truncated, so an article body comes back
whole. A failed verb comes back as a tool error carrying the CLI's own actionable line, which
is what lets a model correct itself instead of looping.

## What the tools refuse, and what they say out loud

These came out of driving the server with a small model that had nothing but these tools. Each
one is a mistake it actually made:

- Re-staging a piece to change its title. `article_create` refuses when the author already has
  a piece with nearly the same title or standfirst, and points at `article_update`. Without it
  the site carries the story twice under two permalinks.
- Front-page slugs from another day. `portada_set` checks the day's real slugs and refuses,
  because a wrong slug is dropped at render and everything below it shifts up, silently
  changing the lead.
- Forgetting the recommended rail. Arranging a day reports where the rail stands and asks
  whether it should change too; replacing the rail says which slugs fell off it.
- Deleting and leaving the wreckage. A delete reports every place that still references the
  slug (the rail, any day's plan) with the call that fixes each one.
- Believing a local change is public. Every mutation ends with a line saying the public site is
  untouched until `site_publish`, and a delete says the reverse trap out loud: gone locally,
  still served publicly.
- Stalling at the walk's artifact gate. The workflow's nodes are written for an operator at a
  terminal, so `workflow_step` translates them: verbs become tool names, and the file the gate
  wants becomes a `workflow_save` call.

## What the agent knows

An agent wired to this server has nothing else to read, so the operating knowledge travels
with the connection: the `initialize` instructions carry the two-world rule (staging is local,
`site_publish` is the internet), the writing path, the front-page layout model, and the
compliance line about fictional personas. The tool descriptions carry the rest. There are also
three resources (`censurado://guide/operating`, `.../layout`, `.../style`) for clients that
surface those.

Writing well still means walking the gated workflow: `workflow_step` serves one node at a time
and `workflow_save` writes the artifact each node asks for, so the sourcing floor and the
headline gate fire exactly as they do for a terminal operator.

## The doctor

`stack_status` answers "are the ports answering". `doctor` answers the question you actually
have before a shift: config and operator token, every service, the authenticated content reads
the tools depend on, the image lane, the media store, the deploy lane, and the editorial
recipe. One row per check, plus a verdict and one concrete next move.

`deep: true` stops checking that things are wired and exercises them: it renders a real image
and uploads it, round-trips a probe file through the media store, and checks the deploy login.
It is slower and it writes a probe image, so it is opt-in.

A rejected token is the case a port probe misses: the site is up, the agent can read the public
pages, and every write fails. That is why the content-plane checks run through real
authenticated verbs.

## Tests

    .venv/bin/python -m pytest mcp/tests -q

They drive the real server (including one spawned stdio session) against a stub CLI that
echoes the argv it was handed, so they assert the exact verb each agent call turns into. No
stack, no GPU, no network.
