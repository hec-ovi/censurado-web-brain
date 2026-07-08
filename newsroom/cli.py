"""The newsroom maintenance CLI: agentic sweeps + a backend health probe.

``censurado-brain <subcommand>`` runs the periodic maintenance passes over the platform
backend (the single content store). It writes no articles and holds no data of its own;
every subcommand talks to the backend over HTTP:

  * ``status``   probe the CLI's connection to the backend (reachable + token authorized)
  * ``topics``   remap topic tags onto an agent-supplied canonical map (topic cleanse)
  * ``embeds``   re-check stored tweet/youtube snapshots and refresh their availability

Run it directly (``python -m newsroom <subcommand>``) or via the installed console script
(``censurado-brain <subcommand>``). Each subcommand prints a one-line JSON result to stdout
and sets an exit status a caller can branch on (0 ok, 2 partial apply, 1 on a failure).

The article-authoring surface is a SEPARATE, dependency-free CLI (``cli/censurado.py``): it
publishes articles and curates authors/sources over the same backend. This module is only the
maintenance sweeps, which need the ``newsroom`` package (httpx + the corpus helpers).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable

from newsroom.config import load_settings
from newsroom.status import BackendProbe, probe_backend

__all__ = ["main"]

# Exit codes a caller can branch on. A subcommand that did not fully succeed is non-zero
# so an operator's failure alerting fires on a partial apply, not only on a crash.
_EXIT = {"done": 0, "done_with_errors": 2, "failed": 1}


def _emit(summary: dict) -> None:
    json.dump(summary, sys.stdout)
    sys.stdout.write("\n")


# A probe takes the backend base URL + operator token and returns a typed connection
# verdict. Injected so the `status` verb tests without contacting a real backend.
BackendProbeFn = Callable[[str, str], BackendProbe]


def _status_main(argv: list[str], *, probe: BackendProbeFn | None = None) -> int:
    """``censurado-brain status``: print the CLI's connection diagnostic to the platform
    backend. Reads the backend base URL + operator token from the environment and probes
    the backend read API (GET /authors) with a bounded timeout, NEVER raising. Prints the
    base URL, whether the token is configured, and the reachable / authorized / remote
    author-count verdict via ``_emit``. Returns 0 when the backend is reachable AND the
    token is authorized, else 1, so a health check can branch on the exit code. ``probe``
    is injectable for tests."""
    parser = argparse.ArgumentParser(
        prog="censurado-brain status",
        description="Report the CLI's connection to the platform backend (live probe).",
    )
    parser.parse_args(argv)  # no flags; surfaces -h and rejects stray args

    settings = load_settings()
    base_url = str(settings.publish_base_url).rstrip("/")
    probe = probe or probe_backend
    result = probe(base_url, settings.operator_token)
    _emit({
        "backend_base_url": base_url,
        "token_configured": bool(settings.operator_token),
        "reachable": result.reachable,
        "authorized": result.authorized,
        "author_count": result.author_count,
        "status_code": result.status,
        "detail": result.detail,
    })
    return 0 if (result.reachable and result.authorized) else _EXIT["failed"]


# A topics-cleanse seam bundle, injected by tests so the verb is driven end to end
# without a network (fetch the corpus, apply the remap). In production each defaults to
# the real implementation over the live APIs.
TopicsFetch = Callable[[], list]
TopicsApply = Callable[[list], tuple]


def _topics_main(
    argv: list[str],
    *,
    fetch: TopicsFetch | None = None,
    apply: TopicsApply | None = None,
) -> int:
    """``censurado-brain topics cleanse --map-file PATH [--apply]``: remap topic tags onto
    a canonical set. Reads the corpus tag set over the read API, applies the canonical
    ``{tag: canonical}`` map an agent supplies via ``--map-file`` (a file path, or ``-`` for
    stdin), and (with ``--apply``) remaps each changed article in place over the operator
    edit lane (``PUT /articles``, admin:write). Without ``--map-file`` every tag maps to
    itself (a no-op). Default is a DRY-RUN that prints the canonical map + the per-article
    plan and writes nothing. Prints a JSON summary via ``_emit``; returns 0 on success, 2 if
    some applies failed, 1 on a read/usage error. The fetch + apply seams are injectable so
    a test drives the verb without a network."""
    if not argv or argv[0] != "cleanse":
        _emit({"error": "usage: topics cleanse --map-file PATH [--apply]"})
        return _EXIT["failed"]
    parser = argparse.ArgumentParser(prog="censurado-brain topics cleanse")
    parser.add_argument(
        "--apply", action="store_true",
        help="apply the remap (default: dry-run, print the plan and write nothing)",
    )
    parser.add_argument(
        "--map-file", default=None, metavar="PATH",
        help="the canonical map (JSON {tag: canonical}) from PATH (or '-' for stdin). This "
        "is how a CLI agent supplies the clustering itself, with no inference backend in "
        "the loop. Omit to map every tag to itself (a no-op).",
    )
    args = parser.parse_args(argv[1:])

    from newsroom.cleanse import apply_remap, collect_topics, fetch_articles, remap_plan

    settings = load_settings()
    base = str(settings.publish_base_url).rstrip("/")
    read_token = settings.operator_token
    edit_token = settings.admin_token or settings.operator_token

    fetch = fetch or (lambda: fetch_articles(base, read_token))
    apply = apply or (lambda plan: apply_remap(plan, base_url=base, read_token=read_token, edit_token=edit_token))

    try:
        articles = fetch()
    except Exception as exc:  # a read failure is a clean usage error, not a crash
        _emit({"error": f"failed to read the corpus: {exc}"})
        return _EXIT["failed"]

    tags = collect_topics(articles)
    # The agent-supplied path: read a {tag: canonical} map (file or '-' stdin) and use it
    # directly, so the clustering comes from a CLI agent rather than a backend model. Every
    # corpus tag maps to itself unless the map overrides it; with no map, the whole pass is
    # a no-op (identity).
    raw: dict = {}
    if args.map_file:
        try:
            src = sys.stdin.read() if args.map_file == "-" else open(args.map_file, encoding="utf-8").read()
            raw = json.loads(src)
            if not isinstance(raw, dict):
                raise ValueError("map must be a JSON object {tag: canonical}")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            _emit({"error": f"failed to read --map-file: {exc}"})
            return _EXIT["failed"]
    canon = {t: (str(raw.get(t, t)).strip() or t) for t in tags}
    plan = remap_plan(articles, canon)
    summary = {
        "tags_before": len(tags),
        "tags_after": len({c for c in canon.values()}),
        "articles_changed": len(plan),
        "applied": 0,
        "failed": [],
        "dry_run": not args.apply,
        "mapping": canon,
        "plan": [{"slug": rm.slug, "before": rm.before, "after": rm.after} for rm in plan],
    }
    if args.apply:
        try:
            applied, failed = apply(plan)
        except Exception as exc:
            _emit({"error": f"apply failed: {exc}"})
            return _EXIT["failed"]
        summary["applied"] = applied
        summary["failed"] = failed
    _emit(summary)
    return _EXIT["done_with_errors"] if summary["failed"] else _EXIT["done"]


# An embeds-recheck seam bundle, injected by tests so the verb is driven end to end
# without a network: list the corpus, recheck each article's snapshots, apply the changes.
# In production each defaults to the real implementation over the live APIs.
EmbedsList = Callable[[], list]
EmbedsApply = Callable[[list, bool], tuple]


def _embeds_main(
    argv: list[str],
    *,
    list_slugs: EmbedsList | None = None,
    apply: EmbedsApply | None = None,
) -> int:
    """``censurado-brain embeds recheck [--apply]``: re-validate every stored tweet/youtube
    snapshot and refresh its availability flag. A deleted tweet flips to ``erased`` (its
    captured text is kept), a pulled video flips ``available`` to false, so the site then
    renders the "eliminado" states. Default is a DRY-RUN that counts what would change and
    writes nothing; ``--apply`` writes the changed articles back over the operator edit lane
    (``PUT /articles``, admin:write). The two seams are injectable so a test drives the verb
    without a network."""
    if not argv or argv[0] != "recheck":
        _emit({"error": "usage: embeds recheck [--apply]"})
        return _EXIT["failed"]
    parser = argparse.ArgumentParser(prog="censurado-brain embeds recheck")
    parser.add_argument(
        "--apply", action="store_true",
        help="write back the changed articles (default: dry-run, only count what would change)",
    )
    args = parser.parse_args(argv[1:])

    from newsroom.embeds import apply_rechecks, fetch_article_slugs

    settings = load_settings()
    base = str(settings.publish_base_url).rstrip("/")
    read_token = settings.operator_token
    edit_token = settings.admin_token or settings.operator_token

    list_slugs = list_slugs or (lambda: fetch_article_slugs(base, read_token))
    apply = apply or (
        lambda slugs, do: apply_rechecks(
            slugs, base_url=base, read_token=read_token, edit_token=edit_token, apply=do
        )
    )

    try:
        slugs = list_slugs()
    except Exception as exc:  # a read failure is a clean usage error, not a crash
        _emit({"error": f"failed to read the corpus: {exc}"})
        return _EXIT["failed"]

    try:
        changed, applied, failed = apply(slugs, args.apply)
    except Exception as exc:
        _emit({"error": f"recheck failed: {exc}"})
        return _EXIT["failed"]

    _emit({
        "scanned": len(slugs),
        "changed": changed,
        "applied": applied,
        "failed": failed,
        "dry_run": not args.apply,
    })
    return _EXIT["done_with_errors"] if failed else _EXIT["done"]


# A normalize seam bundle, injected by tests so the verb is driven end to end without a network:
# fetch every author + every full article. In production each defaults to the real read API.
NormalizeFetch = Callable[[], list]


def _normalize_main(
    argv: list[str],
    *,
    fetch_authors_fn: NormalizeFetch | None = None,
    fetch_articles_fn: NormalizeFetch | None = None,
) -> int:
    """``censurado-brain normalize``: one contract-validated pass over the WHOLE corpus. Reads every
    author and every article and checks each against its isolated write contract
    (``contracts.author.AuthorInput`` / ``contracts.article.PublishArticleInput``), then prints a
    JSON verdict listing any record that would not conform (a legacy section value, a missing required
    field, ...). It is READ-ONLY, so it is always safe to run; a rewrite is a separate, deliberate
    step. The fetch seams are injectable so a test drives the verb without a network. Returns 0 when
    the whole corpus conforms, 2 when some records violate their contract, 1 on a read error."""
    parser = argparse.ArgumentParser(prog="censurado-brain normalize")
    parser.parse_args(argv)  # no flags yet; surfaces -h and rejects stray args

    from newsroom.normalize import fetch_articles_full, fetch_authors, normalize_corpus

    settings = load_settings()
    base = str(settings.publish_base_url).rstrip("/")
    token = settings.operator_token

    fetch_authors_fn = fetch_authors_fn or (lambda: fetch_authors(base, token))
    fetch_articles_fn = fetch_articles_fn or (lambda: fetch_articles_full(base, token))

    try:
        verdict = normalize_corpus(
            fetch_authors_fn=fetch_authors_fn, fetch_articles_fn=fetch_articles_fn
        )
    except Exception as exc:  # a read failure is a clean usage error, not a crash
        _emit({"error": f"failed to read the corpus: {exc}"})
        return _EXIT["failed"]

    _emit(verdict)
    return _EXIT["done"] if verdict["ok"] else _EXIT["done_with_errors"]


def main(argv: list[str] | None = None, *, backend_probe: BackendProbeFn | None = None) -> int:
    """Dispatch a maintenance subcommand, print a JSON result, return an exit code.

    The backend probe defaults to production; a test injects a double to drive ``status``
    without contacting a real backend. ``topics`` and ``embeds`` take their own injectable
    seams when called directly (``_topics_main`` / ``_embeds_main``)."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "status":
        # Report the CLI<->backend connection (live probe).
        return _status_main(argv[1:], probe=backend_probe)
    if argv and argv[0] == "topics":
        # Remap topic tags onto an agent-supplied canonical map.
        return _topics_main(argv[1:])
    if argv and argv[0] == "embeds":
        # Re-check stored tweet/youtube snapshots and refresh their availability flags
        # (deleted tweet -> erased, pulled video -> unavailable).
        return _embeds_main(argv[1:])
    if argv and argv[0] == "normalize":
        # Validate the whole corpus (authors + articles) against the isolated write contracts.
        return _normalize_main(argv[1:])
    _emit({"error": "usage: status|topics|embeds|normalize"})
    return _EXIT["failed"]
