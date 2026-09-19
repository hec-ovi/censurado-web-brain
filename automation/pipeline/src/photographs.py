"""One source-image selection call per article, isolated in its durable step."""
import json

from source_images import SourceImages

from .adapter_api import ApiAdapter
from .render import parse_json_output


def attach_photograph(cfg: dict, inputs: dict, context: dict, piece: dict) -> dict:
    options = cfg.get("source_images") or {}
    if not options.get("enabled"):
        return {}
    urls = [inputs["source_url"]] if inputs.get("source_url") else []
    for node in cfg["nodes"]:
        for source in (node.get("context") or {}).values():
            opts = source.get("websearch") or {}
            reference = opts.get("urls_from") or opts.get("queries_from")
            if reference and context.get(reference):
                try:
                    picked = json.loads(context[reference]).get("read_urls", [])
                    urls.extend(u for u in picked if isinstance(u, str))
                except (ValueError, AttributeError, TypeError):
                    pass
    draft = next(n for n in cfg["nodes"] if n["role"] == "draft")
    adapter_cfg = dict(cfg["adapters"][options.get("adapter") or draft["adapter"]])
    if not options.get("adapter") and draft.get("model"):
        adapter_cfg["model"] = draft["model"]
    adapter = ApiAdapter(adapter_cfg)

    def choose(prompt, images):
        raw = adapter.complete(prompt, want_json=True,
                               images=images if options.get("vision", False) else None)
        return parse_json_output(raw)

    return SourceImages(cfg["backend"]).attach(urls, piece, choose)
