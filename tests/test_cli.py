"""The newsroom maintenance CLI (``newsroom.cli.main``), driven end to end.

These tests exercise the REAL entry point through to its side effects for the three
surviving verbs: ``status`` (a backend health probe), ``topics cleanse`` (the agent-
supplied topic remap), and ``embeds recheck`` (the snapshot availability sweep). The
backend probe and the fetch/apply seams are injected so a verb is driven with no network.
"""

from __future__ import annotations

import json

from newsroom.cleanse import ArticleTopics
from newsroom.cli import _embeds_main, _topics_main, main
from newsroom.status import BackendProbe


# ----- status (the backend health probe) -----


def test_cli_status_reachable_and_authorized_exits_0(capsys):
    # A 2xx probe: the backend is reachable and the token is accepted, so status exits 0
    # and reports the live author count.
    def probe(base_url, token):
        assert token == "op.tok"
        return BackendProbe(reachable=True, authorized=True, status=200, author_count=6)

    import os
    os.environ["NEWSROOM_OPERATOR_TOKEN"] = "op.tok"
    try:
        code = main(["status"], backend_probe=probe)
    finally:
        os.environ.pop("NEWSROOM_OPERATOR_TOKEN", None)
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reachable"] is True and out["authorized"] is True
    assert out["author_count"] == 6 and out["token_configured"] is True


def test_cli_status_unreachable_exits_1(capsys):
    # A transport fault: not reachable, so status exits non-zero and carries the reason.
    def probe(base_url, token):
        return BackendProbe(reachable=False, authorized=False, status=0, detail="Connection refused")

    code = main(["status"], backend_probe=probe)
    assert code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["reachable"] is False and out["detail"] == "Connection refused"


def test_cli_status_unauthorized_exits_1(capsys):
    # A 401: reachable but the token was rejected, so status exits non-zero.
    def probe(base_url, token):
        return BackendProbe(reachable=True, authorized=False, status=401, detail="unauthorized")

    code = main(["status"], backend_probe=probe)
    assert code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["reachable"] is True and out["authorized"] is False and out["status_code"] == 401


# ----- topics cleanse (the agent-supplied map-file lane, no inference) -----


def test_cli_topics_cleanse_map_file_dry_run_plans_the_remap(tmp_path, capsys):
    # `topics cleanse --map-file` is the non-LLM lane: a CLI agent supplies the canonical
    # {tag: canonical} map, and the verb fetches the corpus and PLANS the remap (dry-run by
    # default, writing nothing). The fetch + apply seams are injected so no network is hit.
    articles = [
        ArticleTopics(slug="a", topics=["ia", "inteligencia-artificial"]),
        ArticleTopics(slug="b", topics=["gpu"]),
    ]
    mapping = {"ia": "inteligencia artificial", "inteligencia-artificial": "inteligencia artificial"}
    map_file = tmp_path / "map.json"
    map_file.write_text(json.dumps(mapping), encoding="utf-8")
    applied: list = []

    code = _topics_main(
        ["cleanse", "--map-file", str(map_file)],
        fetch=lambda: articles,
        apply=lambda plan: (applied.extend(plan), (len(plan), []))[1],
    )

    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is True
    assert out["mapping"]["ia"] == "inteligencia artificial"
    # Article a's two synonymous tags collapse to one canonical (it changes); b is untouched.
    changed = {p["slug"] for p in out["plan"]}
    assert changed == {"a"}
    assert out["plan"][0]["after"] == ["inteligencia artificial"]
    assert applied == []  # dry-run wrote nothing


def test_cli_topics_cleanse_map_file_apply_calls_the_apply_seam(tmp_path, capsys):
    # With --apply the verb runs the apply seam on the planned remap and reports the count.
    articles = [ArticleTopics(slug="a", topics=["ia"])]
    map_file = tmp_path / "m.json"
    map_file.write_text(json.dumps({"ia": "inteligencia artificial"}), encoding="utf-8")
    seen: dict = {}

    def fake_apply(plan):
        seen["n"] = len(plan)
        return len(plan), []

    code = _topics_main(
        ["cleanse", "--map-file", str(map_file), "--apply"],
        fetch=lambda: articles,
        apply=fake_apply,
    )

    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is False and out["applied"] == 1 and out["failed"] == []
    assert seen["n"] == 1


def test_cli_topics_cleanse_without_a_map_is_an_identity_noop(tmp_path, capsys):
    # No --map-file means every tag maps to itself: the plan is empty (a no-op), so the
    # apply seam is never even reached.
    articles = [ArticleTopics(slug="a", topics=["ia", "gpu"])]
    called: list = []

    code = _topics_main(
        ["cleanse"],
        fetch=lambda: articles,
        apply=lambda plan: (called.append(1), (0, []))[1],
    )

    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["plan"] == [] and out["articles_changed"] == 0
    assert called == []  # dry-run, nothing applied


def test_cli_topics_cleanse_unknown_subverb_exits_1(capsys):
    # An unknown `topics` sub-verb prints a JSON usage error (not a crash) and exits 1.
    code = _topics_main(["teleport"])
    assert code == 1
    assert "usage" in json.loads(capsys.readouterr().out)["error"]


# ----- embeds recheck (the snapshot availability sweep) -----


def test_cli_embeds_recheck_dry_run_counts_without_applying(capsys):
    # Default is a dry-run: the apply seam runs with apply=False and the summary reports
    # what WOULD change, writing nothing.
    seen: dict = {}

    def fake_apply(slugs, do):
        seen["do"] = do
        return (2, 0, [])  # changed, applied, failed

    code = _embeds_main(
        ["recheck"],
        list_slugs=lambda: ["a", "b", "c"],
        apply=fake_apply,
    )
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is True and out["scanned"] == 3
    assert out["changed"] == 2 and out["applied"] == 0
    assert seen["do"] is False


def test_cli_embeds_recheck_apply_writes_back(capsys):
    # With --apply the seam runs with apply=True and the summary reports the applied count.
    def fake_apply(slugs, do):
        assert do is True
        return (2, 2, [])

    code = _embeds_main(
        ["recheck", "--apply"],
        list_slugs=lambda: ["a", "b"],
        apply=fake_apply,
    )
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is False and out["applied"] == 2 and out["failed"] == []


def test_cli_embeds_recheck_reports_partial_failure_with_code_2(capsys):
    # A failed write flips the exit to 2 (a partial apply), so alerting fires.
    code = _embeds_main(
        ["recheck", "--apply"],
        list_slugs=lambda: ["a"],
        apply=lambda slugs, do: (1, 0, ["a"]),
    )
    assert code == 2
    out = json.loads(capsys.readouterr().out)
    assert out["failed"] == ["a"]


def test_cli_embeds_unknown_subverb_exits_1(capsys):
    # An unknown `embeds` sub-verb prints a JSON usage error and exits 1.
    code = _embeds_main(["teleport"])
    assert code == 1
    assert "usage" in json.loads(capsys.readouterr().out)["error"]


def test_cli_top_level_usage_on_unknown_command(capsys):
    # An unknown top-level command prints the usage line and exits 1.
    code = main(["teleport"])
    assert code == 1
    assert json.loads(capsys.readouterr().out)["error"] == "usage: status|topics|embeds|normalize"
