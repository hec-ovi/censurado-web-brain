"""The sole-fan-out structural invariant (Step 6 build-plan contract d).

The manager is the ONLY place the harness fans out to journalists, and a journalist
node has no edge that constructs another journalist or manager (architecture doc
A.3). Runaway recursion is therefore structurally impossible, not merely unlikely.
We prove it by static analysis of the real package source, so the invariant cannot
silently rot as the code grows:

  * ``newsroom.pipeline`` (the journalist) never imports ``newsroom.manager``;
  * ``run_article_pipeline`` is reachable from exactly one module, the dispatch;
  * the journalist never CREATES an assignment or a run (only transitions its own
    status), so it cannot mint work for another agent.

The scan resolves every identifier a module mentions, by IMPORTED SYMBOL NAME and
by attribute name, not just by call-site spelling, so an aliased import
(``... as rap``) or an indirect call (``ca = store.create_assignment; ca()``)
cannot slip a fan-out edge past the guard.
"""

from __future__ import annotations

import ast
from pathlib import Path

import newsroom

_ROOT = Path(newsroom.__file__).resolve().parent


def _py_files(*relparts: str) -> list[Path]:
    return sorted(_ROOT.joinpath(*relparts).rglob("*.py"))


def _identifiers(tree: ast.AST) -> set[str]:
    """Every identifier a module mentions: bare names, attribute names, and imported
    symbol names (regardless of an ``as`` alias). This is alias- and indirection-
    proof: ``from m import f as g`` still records ``f``; ``x.create_assignment``
    records ``create_assignment`` whether it is called or merely bound to a local."""
    ids: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            ids.add(node.id)
        elif isinstance(node, ast.Attribute):
            ids.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                ids.add(alias.name)
                if alias.asname:
                    ids.add(alias.asname)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                ids.add(alias.name.split(".")[0])
                if alias.asname:
                    ids.add(alias.asname)
    return ids


def _imported_modules(tree: ast.AST) -> set[str]:
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    return mods


def _parse(path: Path) -> ast.AST:
    return ast.parse(path.read_text())


def test_pipeline_never_imports_the_manager():
    # Dependency direction is one-way: manager -> pipeline, never the reverse.
    for path in _py_files("pipeline"):
        mods = _imported_modules(_parse(path))
        offenders = {m for m in mods if m.startswith("newsroom.manager")}
        assert not offenders, f"{path.name} imports the manager: {offenders}"


def test_run_article_pipeline_is_reachable_from_exactly_one_module():
    # Outside the pipeline package itself (which defines and re-exports the symbol),
    # exactly one module may reach the journalist entry point: the dispatch.
    users = {
        p.relative_to(_ROOT).as_posix()
        for p in _py_files()
        if not p.relative_to(_ROOT).as_posix().startswith("pipeline/")
        and "run_article_pipeline" in _identifiers(_parse(p))
    }
    assert users == {"manager/dispatch.py"}, f"unexpected fan-out users: {users}"


def test_journalist_never_creates_an_assignment_or_run():
    # A journalist may only transition its OWN assignment's status; minting a new
    # assignment/run would be spawning work for another agent.
    for path in _py_files("pipeline"):
        ids = _identifiers(_parse(path))
        assert "create_assignment" not in ids, f"{path.name} references create_assignment"
        assert "create_run" not in ids, f"{path.name} references create_run"


def test_dispatch_is_wired_to_the_pipeline():
    # Sanity on the allowed direction: the dispatch DOES reach the journalist.
    mods = _imported_modules(_parse(_ROOT / "manager" / "dispatch.py"))
    assert "newsroom.pipeline" in mods


def test_identifier_scan_catches_aliased_and_indirect_forms():
    # The guard's own robustness: an aliased import and an indirect (bound) call must
    # both still surface the guarded symbol, or the structural check would be a sieve.
    aliased = "from newsroom.pipeline import run_article_pipeline as rap\nrap(payload)\n"
    assert "run_article_pipeline" in _identifiers(ast.parse(aliased))
    indirect = "ca = store.create_assignment\nca(run_id=1)\n"
    assert "create_assignment" in _identifiers(ast.parse(indirect))
