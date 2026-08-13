"""Run the websearch CLI (multi-engine search + fenced page reads) for a research context."""
import json
import subprocess

from .errors import AdapterError


class WebSearcher:
    def __init__(self, cfg: dict):
        self.cmd = cfg.get("cmd", ["websearch"])
        self.timeout = cfg.get("timeout_s", 180)

    def _run(self, *args: str) -> dict:
        argv = [*self.cmd, *args, "--json"]
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=self.timeout)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise AdapterError(f"websearch {args[0]} did not run: {e}") from e
        try:
            env = json.loads(p.stdout)
        except json.JSONDecodeError as e:
            raise AdapterError(
                f"websearch {args[0]} returned no JSON envelope: {(p.stderr or p.stdout)[:200]}") from e
        if not env.get("ok"):
            raise AdapterError(f"websearch {args[0]} error: {json.dumps(env.get('error'))[:200]}")
        return env.get("data") or {}

    def research(self, queries: list[str], max_results: int = 5,
                 fetch_top: int = 4, page_tokens: int = 3000) -> str:
        """Search every query, dedup by URL, read the top pages; one markdown block.

        A single dead query or unreadable page is skipped; zero results overall is an error
        (research must not proceed on an empty ground).
        """
        found: list[dict] = []
        seen: set[str] = set()
        for q in queries:
            try:
                data = self._run("web-search", q, "--max-results", str(max_results))
            except AdapterError:
                continue
            for r in data.get("results", []):
                url = r.get("url")
                if url and url not in seen:
                    seen.add(url)
                    found.append({"url": url, "title": r.get("title", ""),
                                  "snippet": r.get("snippet", ""),
                                  "published": r.get("published") or ""})
        if not found:
            raise AdapterError("websearch: no results for any query")

        lines = ["### Resultados de busqueda"]
        for r in found:
            when = f" ({r['published']})" if r["published"] else ""
            lines.append(f"- [{r['title']}]({r['url']}){when} - {r['snippet']}")

        read = 0
        for r in found:
            if read >= fetch_top:
                break
            try:
                data = self._run("web-fetch", r["url"], "--page-size-tokens", str(page_tokens))
            except AdapterError:
                continue
            pages = data.get("pages") or []
            if not pages or pages[0].get("blocked"):
                continue
            lines.append(f"\n### Pagina leida: {r['url']}")
            lines.append(pages[0].get("content", ""))
            read += 1
        if not read:
            raise AdapterError("websearch: found results but could not read any page")
        return "\n".join(lines)
