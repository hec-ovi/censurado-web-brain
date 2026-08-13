"""Resolve a node's declared context sources into prompt placeholder values."""
import json
import os
from pathlib import Path

import httpx

from .errors import AdapterError
from .feeds import fetch_feed, fresh
from .websearch import WebSearcher


class ContextFetcher:
    def __init__(self, cfg: dict):
        backend = cfg["backend"]
        self.base = backend["base_url"].rstrip("/")
        self.token = os.environ[backend["token_env"]]
        self.timeout = backend.get("timeout_s", 60)
        self.websearch_cfg = cfg.get("websearch", {})

    def resolve(self, spec: dict, inputs: dict, outputs: dict) -> dict:
        out = {}
        for key, src in spec.items():
            if "file" in src:
                out[key] = Path(src["file"]).read_text()
            elif "persona" in src:
                out[key] = self._persona(inputs["author"])
            elif "editorial" in src:
                out[key] = self._editorial(src["editorial"])
            elif "feeds" in src:
                out[key] = self._feeds(inputs["author"], src["feeds"])
            elif "websearch" in src:
                out[key] = self._websearch(src["websearch"], outputs)
        return out

    def _get(self, path: str):
        try:
            r = httpx.get(self.base + path,
                          headers={"Authorization": f"Bearer {self.token}"},
                          timeout=self.timeout)
        except httpx.HTTPError as e:
            raise AdapterError(f"context read {path} unreachable: {e}") from e
        if r.status_code != 200:
            raise AdapterError(f"context read {path} -> {r.status_code}")
        return r.json()

    def _persona(self, handle: str) -> str:
        data = self._get("/authors")
        rows = data if isinstance(data, list) else data.get("authors", [])
        for a in rows:
            if a.get("handle") == handle:
                meta = a.get("metadata") or {}
                parts = [f"Nombre: {a.get('name', '')}",
                         f"Bio: {a.get('bio', '')}",
                         f"Carta de estilo:\n{a.get('style', '')}"]
                if meta.get("who_i_am"):
                    parts.append(f"Quien soy:\n{meta['who_i_am']}")
                if meta.get("profile_topics"):
                    parts.append("Temas del perfil: " + ", ".join(meta["profile_topics"]))
                for key, label in (("few_shots_pos", "Ejemplos de tu voz (asi SI)"),
                                   ("few_shots_neg", "Ejemplos de lo que NO sos (asi NO)")):
                    shots = meta.get(key) or []
                    if shots:
                        texts = [s.get("good") or s.get("bad") or "" for s in shots]
                        parts.append(f"{label}:\n" + "\n---\n".join(t for t in texts if t))
                return "\n\n".join(parts)
        raise AdapterError(f"author '{handle}' not found in the backend")

    def _editorial(self, lang: str) -> str:
        data = self._get(f"/editorial-text?lang={lang}")
        return "\n".join(f"- {e['key']}: {e['value']}"
                         for e in data.get("entries", []) if not e.get("deleted"))

    def _feeds(self, handle: str, opts: dict) -> str:
        """Fresh titulars from the author's registered sources, grouped per source.

        Sources with a feed are fetched and windowed; a source registered as
        site_search is listed for the search lane instead. One broken feed is
        reported inline; every feed failing is an error.
        """
        hours = opts.get("hours", 48)
        per_source = opts.get("max_per_source", 6)
        attached = self._get(f"/authors/{handle}/sources").get("sources", [])
        registry = self._get("/sources")
        rows = registry.get("sources", registry) if isinstance(registry, dict) else registry
        by_slug = {s["slug"]: s for s in rows}

        lines, search_only, worked = [], [], 0
        for slug in attached:
            src = by_slug.get(slug)
            if not src or not src.get("enabled", True):
                continue
            if not src.get("feed_urls"):
                search_only.append(f"- {slug} ({src.get('domain', '')}), lean {src.get('lean', '')}")
                continue
            entries, note = [], ""
            for url in src["feed_urls"]:
                try:
                    _, got = fetch_feed(url)
                    entries.extend(got)
                except AdapterError as e:
                    note = f" (feed caido: {e})"
            top = fresh(entries, hours)[:per_source]
            lines.append(f"\n## {slug} (lean {src.get('lean', '')}){note}")
            if top:
                worked += 1
                for e in top:
                    when = e.published.strftime(" - %Y-%m-%d %H:%M") if e.published else ""
                    lines.append(f"- [{e.title}]({e.link}){when}")
            else:
                lines.append(f"- sin titulares en las ultimas {hours}h")
        if not worked and not search_only:
            raise AdapterError(f"feeds: no source of author '{handle}' produced any titular")
        out = [f"Titulares frescos (ultimas {hours}h) de las fuentes del autor:"] + lines
        if search_only:
            out += ["\n## Fuentes sin feed (cubrir por busqueda web)"] + search_only
        return "\n".join(out)

    def _websearch(self, opts: dict, outputs: dict) -> str:
        """Run the searches a prior node proposed, read its picked source pages directly,
        and inline everything fenced."""
        source_node = opts["queries_from"]
        raw = outputs.get(source_node)
        if raw is None:
            raise AdapterError(f"websearch: node '{source_node}' produced no output yet")
        try:
            proposed = json.loads(raw)
        except json.JSONDecodeError as e:
            raise AdapterError(f"websearch: node '{source_node}' output is not JSON") from e
        queries = proposed.get("queries") if isinstance(proposed, dict) else None
        if not isinstance(queries, list) or not all(isinstance(q, str) and q for q in queries):
            raise AdapterError(f"websearch: node '{source_node}' has no \"queries\" string list")
        urls = []
        if opts.get("urls_from"):
            picked = outputs.get(opts["urls_from"])
            try:
                chosen = json.loads(picked).get("read_urls") if picked else None
            except (json.JSONDecodeError, AttributeError):
                chosen = None    # read_urls is an enhancement; a malformed pick drops to search-only
            if isinstance(chosen, list):
                urls = [u for u in chosen
                        if isinstance(u, str) and u.startswith(("http://", "https://"))]
        return WebSearcher(self.websearch_cfg).research(
            queries, urls=urls,
            max_results=opts.get("max_results", 5),
            fetch_top=opts.get("fetch_top", 4),
            fetch_urls_top=opts.get("fetch_urls_top", 3),
            page_tokens=opts.get("page_tokens", 3000))
