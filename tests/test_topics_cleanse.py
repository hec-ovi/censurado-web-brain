"""The agentic topic cleanse: the clustering decision + the remap arithmetic + the
CLI verb driven end to end through injected seams (no network, no model call).
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout

from newsroom.cleanse import (
    ArticleTopics,
    collect_topics,
    remap_plan,
)
from newsroom.cleanse import topics as topics_mod
from newsroom.cli import _topics_main
from newsroom.inference.adapter import ChatResponse


def _articles() -> list[ArticleTopics]:
    return [
        ArticleTopics(slug="a", topics=["ia", "modelos-abiertos"]),
        ArticleTopics(slug="b", topics=["inteligencia-artificial", "china"]),
        ArticleTopics(slug="c", topics=["china"]),  # unchanged under the map below
    ]


def test_collect_topics_is_distinct_and_sorted():
    tags = collect_topics(_articles())
    assert tags == ["china", "ia", "inteligencia-artificial", "modelos-abiertos"]


def test_cluster_topics_parses_fenced_json_and_fills_identity(monkeypatch):
    # A fenced reply that omits one tag: the omitted tag maps to itself (identity), so
    # the cleanse never silently loses a tag to a parse gap.
    reply = (
        "```json\n"
        '{"ia": "inteligencia artificial", "modelos-abiertos": "inteligencia artificial",'
        ' "inteligencia-artificial": "inteligencia artificial"}\n'
        "```"
    )
    monkeypatch.setattr(topics_mod, "chat", lambda req, **kw: ChatResponse(content=reply))
    out = topics_mod.cluster_topics(["ia", "modelos-abiertos", "inteligencia-artificial", "china"])
    assert out["ia"] == "inteligencia artificial"
    assert out["modelos-abiertos"] == "inteligencia artificial"
    assert out["inteligencia-artificial"] == "inteligencia artificial"
    assert out["china"] == "china"  # omitted by the model -> identity


def test_cluster_topics_bad_json_degrades_to_identity(monkeypatch):
    monkeypatch.setattr(topics_mod, "chat", lambda req, **kw: ChatResponse(content="not json at all"))
    out = topics_mod.cluster_topics(["ia", "china"])
    assert out == {"ia": "ia", "china": "china"}


def test_remap_plan_only_lists_changed_articles_and_dedupes():
    canon = {
        "ia": "inteligencia artificial",
        "modelos-abiertos": "inteligencia artificial",  # collapses with 'ia' on article a
        "inteligencia-artificial": "inteligencia artificial",
        "china": "china",
    }
    plan = remap_plan(_articles(), canon)
    by_slug = {rm.slug: rm for rm in plan}
    # Article a: ['ia','modelos-abiertos'] -> both 'inteligencia artificial' -> deduped to one.
    assert by_slug["a"].after == ["inteligencia artificial"]
    # Article b: ['inteligencia-artificial','china'] -> ['inteligencia artificial','china'].
    assert by_slug["b"].after == ["inteligencia artificial", "china"]
    # Article c: ['china'] is unchanged, so it is NOT in the plan.
    assert "c" not in by_slug


def _run(argv, **seams) -> dict:
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = _topics_main(argv, **seams)
    return json.loads(buf.getvalue()), code


def test_cli_dry_run_emits_plan_and_writes_nothing():
    applied_calls = []
    summary, code = _run(
        ["cleanse"],
        fetch=lambda: _articles(),
        cluster=lambda tags: {
            "ia": "inteligencia artificial",
            "modelos-abiertos": "inteligencia artificial",
            "inteligencia-artificial": "inteligencia artificial",
            "china": "china",
        },
        apply=lambda plan: applied_calls.append(plan) or (len(plan), []),
    )
    assert code == 0
    assert summary["dry_run"] is True
    assert summary["tags_before"] == 4
    assert summary["tags_after"] == 2  # 'inteligencia artificial' + 'china'
    assert summary["articles_changed"] == 2  # a and b change, c does not
    assert applied_calls == []  # dry-run never applies


def test_cli_apply_invokes_the_apply_seam():
    calls = {}

    def fake_apply(plan):
        calls["plan"] = plan
        return (len(plan), [])

    summary, code = _run(
        ["cleanse", "--apply"],
        fetch=lambda: _articles(),
        cluster=lambda tags: {
            t: ("inteligencia artificial" if t in ("ia", "modelos-abiertos", "inteligencia-artificial") else t)
            for t in tags
        },
        apply=fake_apply,
    )
    assert code == 0
    assert summary["dry_run"] is False
    assert summary["applied"] >= 1
    assert "plan" in calls  # the apply seam ran


def test_cli_usage_error_on_unknown_subverb():
    summary, code = _run(["wat"], fetch=lambda: [], cluster=lambda t: {}, apply=lambda p: (0, []))
    assert code == 1
    assert "usage" in summary["error"]


def _raise(_):
    raise RuntimeError("429 Too Many Requests")


def test_cli_inference_failure_exits_cleanly_not_a_traceback():
    summary, code = _run(["cleanse"], fetch=lambda: _articles(), cluster=_raise, apply=lambda p: (0, []))
    assert code == 1
    assert "clustering failed" in summary["error"]


def test_cli_map_file_bypasses_the_model(tmp_path):
    # An agent (e.g. Claude) supplies the canonical map directly; the model cluster seam
    # must NOT be called, and every corpus tag maps to itself unless the file overrides it.
    mapfile = tmp_path / "map.json"
    mapfile.write_text(
        json.dumps(
            {
                "ia": "inteligencia artificial",
                "modelos-abiertos": "inteligencia artificial",
                "inteligencia-artificial": "inteligencia artificial",
            }
        ),
        encoding="utf-8",
    )

    def _must_not_run(_):
        raise AssertionError("the model cluster must NOT be called when --map-file is given")

    summary, code = _run(
        ["cleanse", "--map-file", str(mapfile)],
        fetch=lambda: _articles(),
        cluster=_must_not_run,
        apply=lambda p: (len(p), []),
    )
    assert code == 0
    assert summary["tags_after"] == 2  # 'inteligencia artificial' + 'china' (identity)
    assert summary["articles_changed"] == 2  # a and b change; c ('china') unchanged
