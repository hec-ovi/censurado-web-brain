"""Select and upload a source photograph without publishing an article."""
import argparse
import json
from pathlib import Path

from .config import PipelineConfig
from .errors import ConfigError
from .photographs import attach_photograph


def image_command(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--piece-file", required=True, help="JSON article with title and body")
    parser.add_argument("--source-url", required=True)
    args = parser.parse_args(argv)
    try:
        config = PipelineConfig.load(args.config)
        article = json.loads(Path(args.piece_file).read_text())
        if not isinstance(article, dict) or not all(isinstance(article.get(k), str) and article[k]
                                                     for k in ("title", "body")):
            raise ValueError("piece-file needs title and body strings")
        if not (config.data.get("source_images") or {}).get("enabled"):
            raise ValueError("source_images.enabled must be true")
    except (ConfigError, ValueError, OSError) as exc:
        print(f"CONFIG_INVALID: {exc}")
        return 2
    result = attach_photograph(config.data, {"source_url": args.source_url}, {}, article)
    print(json.dumps(result, ensure_ascii=False))
    return 0
