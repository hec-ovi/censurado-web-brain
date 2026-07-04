---
name: websearch
description: >-
  Search the web and read pages with no API keys, to ground a story before writing. Use when
  you need current facts, a real source URL, an arXiv paper, or a GitHub repo. It runs as a
  shell command, so any agent with a bash tool can call it (no web tool or MCP required).
---

# Web search and page reading (keyless, over bash)

Web search is a shell command, not a special tool. The `websearch-skill` package fans out
across many engines (Google, Brave, DuckDuckGo, Yandex, Mojeek, and more), fuses and dedups the
results, and extracts pages into clean Markdown. No API key, nothing to start.

- **Search** (ranked results, each with a URL):

      uv run --with websearch-skill websearch web-search "<your query>"

- **Read a page** (clean Markdown, paginated so it never floods context):

      uv run --with websearch-skill websearch web-fetch "<url>"

- **Papers / repos** when general search is not enough:

      uv run --with websearch-skill websearch arxiv "<query>"
      uv run --with websearch-skill websearch github "<query>"

Search first to find real sources, then fetch the one or two that carry the facts you need.
Use only what these commands return; never invent a URL, a statistic, or a quote.
