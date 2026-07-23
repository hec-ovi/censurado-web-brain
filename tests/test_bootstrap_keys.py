"""bootstrap.sh key rotation: re-running must revoke the prior operator key.

The keys.json updater is the inline python heredoc inside bootstrap.sh (the rest of the
script needs docker, so the tests extract and drive exactly that updater). Rotation is
the contract: a re-run replaces the previous entry minted for the same author instead of
appending forever, so a leaked old operator token dies on re-bootstrap; keys minted for
OTHER authors are never touched.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOOTSTRAP = ROOT / "bootstrap.sh"


def _updater_source() -> str:
    lines = BOOTSTRAP.read_text().splitlines()
    start = next(i for i, line in enumerate(lines)
                 if line.startswith('python3 - keys.json "$ENTRY"'))
    end = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == "PY")
    return "\n".join(lines[start + 1:end])


def _run(keys_path: Path, entry: dict) -> list:
    # `python3 -c <src> <path> <entry>` gives the heredoc the same sys.argv[1:] it
    # sees under `python3 - keys.json "$ENTRY"`.
    subprocess.run(
        [sys.executable, "-c", _updater_source(), str(keys_path), json.dumps(entry)],
        check=True,
    )
    return json.loads(keys_path.read_text())


def test_rerun_replaces_the_prior_operator_entry(tmp_path):
    keys = tmp_path / "keys.json"
    keys.write_text(json.dumps([
        {"prefix": "old", "hash": "h-old", "author": "newsroom", "scopes": ["articles:write"]},
        {"prefix": "oth", "hash": "h-oth", "author": "someone-else", "scopes": ["articles:write"]},
    ]))
    out = _run(keys, {"prefix": "new", "hash": "h-new", "author": "newsroom",
                      "scopes": ["articles:write", "articles:publish-any", "admin:write"]})
    assert [e["prefix"] for e in out] == ["oth", "new"], \
        "the old newsroom key must be dropped; other authors' keys must survive"


def test_first_run_seeds_from_the_empty_file(tmp_path):
    keys = tmp_path / "keys.json"
    keys.write_text("[]\n")
    out = _run(keys, {"prefix": "new", "hash": "h", "author": "newsroom", "scopes": []})
    assert [e["prefix"] for e in out] == ["new"]


def test_double_rerun_never_accumulates(tmp_path):
    keys = tmp_path / "keys.json"
    keys.write_text("[]\n")
    for n in ("a", "b", "c"):
        out = _run(keys, {"prefix": n, "hash": "h-" + n, "author": "newsroom", "scopes": []})
    assert [e["prefix"] for e in out] == ["c"]
