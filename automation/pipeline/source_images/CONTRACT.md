# Source photographs

Find photographs on source pages, choose one relevant to the article, and upload it.

`SourceImages(backend, client=None)` takes [backend settings](schema.json#/$defs/backend) and an optional HTTP client. `attach(urls, article, choose)` takes [input](schema.json#/$defs/input) and returns [image metadata](schema.json#/$defs/output), or `{}` when no image fits or retrieval fails. `choose(prompt, images)` returns a dict with a candidate `id` or null and an `alt` description. It runs once with the article, source titles, captions, and candidate photographs. The caller decides whether to send pixels to its model. Unknown IDs are discarded.

Candidates come from Open Graph, article JSON-LD, and article images. Downloads accept public HTTP(S) only, follow checked redirects, and have byte/time limits. Source URLs, captions, and credits travel with the selected image. No suitable photograph means a text card.

Dependencies: HTTP source pages, model selection callback, backend `POST /media` ([contract](../../../../censurado-web-backend/contracts/CONTRACT.md)). Tests: `.venv/bin/pytest automation/pipeline/source_images/tests`.
