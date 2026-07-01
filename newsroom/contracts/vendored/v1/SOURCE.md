# Vendored contracts (v1)

Pinned, byte-identical copies of the platform's publish contracts from the
`censurado-web-backend` repository. They are the cross-layer contracts for what the harness
publishes.

- `article.schema.json` (`$id` `https://censurado.local/contracts/article.schema.json`):
  the single-article payload (`POST /articles`).
- `batch-request.schema.json` (`$id` `https://censurado.local/contracts/batch-request.schema.json`):
  the `{"articles": [...]}` body of `POST /articles:batch`. Each item is the same
  article shape plus a required per-item `idempotency_key` (one HTTP header cannot
  carry N keys, so the key moves into the body).
- `batch-response.schema.json` (`$id` `https://censurado.local/contracts/batch-response.schema.json`):
  the `{"results": [{index, id, slug, status}]}` success body.

Source repo: `censurado-web-backend` (the backend's publish/write API, `POST /articles` and
`POST /articles:batch`); source paths: `contracts/*.json`. The publish/write contracts moved
to the backend in the 2026-06 backend split.

Do not edit these by hand. A drift test (`tests/test_schema_drift.py`) compares each
to the live platform schema and fails loudly when they diverge. To re-sync after an
intentional platform schema change, copy the platform file over this one and bump
`CONTRACT_VERSION` in `newsroom/contracts/schema.py` when the change is breaking.
