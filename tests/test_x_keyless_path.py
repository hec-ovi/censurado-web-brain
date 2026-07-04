"""Drift guard: the X-reading path the runbook documents must match what ships.

The newsroom reads X / Truth Social posts KEYLESS: web search finds a public post,
then the `tweet` / `truth` verbs snapshot it via fxtwitter. A live X {{tweet:}} needs
no capture at all, preview auto-fetches the card from the id. These tests pin that
contract so the docs cannot drift toward a paid integration.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_write_article_skill_documents_the_keyless_x_path():
    # The X-reading guidance lives in the write-article skill. The keyless capture
    # verb is documented (the pin-a-snapshot / Truth Social path).
    text = (ROOT / "cli" / "skills" / "write-article" / "SKILL.md").read_text()
    assert "censurado.py tweet" in text


def test_write_article_skill_treats_a_live_x_tweet_as_a_plain_marker():
    # A live X {{tweet:}} is a body marker like {{relacionado:}} / {{video:}}: preview
    # auto-fetches the card from the id, no separate capture. Capture is only for
    # pinning a deleted post or a Truth Social post (auto-fetch is X-only).
    text = (ROOT / "cli" / "skills" / "write-article" / "SKILL.md").read_text().lower()
    assert "{{tweet:" in text
    assert "auto-fetches the card" in text, "skill must say preview auto-fetches the X card"
    assert "live x post" in text, "skill must call out the live-X-post no-capture case"
