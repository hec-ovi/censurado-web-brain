"""Tests for the isolated author contract + the single-pass corpus normalizer.

Covers ``newsroom.contracts.author`` (the AuthorInput write model + projection), the pure
``newsroom.normalize`` validators, and the ``censurado-brain normalize`` verb driven end to end with
injected fetchers (no network). This is the "one pass over the whole DB" entry point tied to the
isolated contracts."""

import io
import json
from contextlib import redirect_stdout

import pytest
from pydantic import ValidationError

from newsroom import cli
from newsroom.contracts.author import AuthorInput, author_write_payload
from newsroom.contracts.schema import load_authors_schema
from newsroom.normalize import normalize_corpus, validate_article, validate_author


def _author(**over):
    base = {
        "handle": "ada", "name": "Ada", "bio": "", "avatar": "", "gender": "",
        "about": "", "style": "", "topics": [], "sources": [], "metadata": {},
        # server-owned read fields the write contract must NOT accept:
        "deleted": False, "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
    }
    base.update(over)
    return base


def _article(**over):
    base = {
        "slug": "s1", "title": "T", "body": "b", "author": "ada", "section": "politics",
        "topics": ["x"], "published_at": "2026-01-01T00:00:00Z", "metadata": {},
        # server-owned read fields:
        "id": "1", "content_hash": "abc", "created_at": "2026-01-01T00:00:00Z", "card_type": "text",
    }
    base.update(over)
    return base


# ---------------------------------------------------------------- the isolated author contract
def test_authorinput_fields_match_the_vendored_schema():
    schema = load_authors_schema()
    assert set(AuthorInput.model_fields) == set(schema["properties"])
    assert schema["required"] == ["handle"]


def test_author_write_payload_drops_server_fields_and_conforms():
    payload = author_write_payload(_author())
    assert "deleted" not in payload and "created_at" not in payload and "updated_at" not in payload
    assert set(payload) <= set(load_authors_schema()["properties"])
    AuthorInput(**payload)  # a projected read record is a valid write payload


def test_authorinput_is_strict_and_requires_handle():
    with pytest.raises(ValidationError):
        AuthorInput(handle="ada", bogus="x")   # unknown field rejected (additionalProperties:false)
    with pytest.raises(ValidationError):
        AuthorInput(handle="")                  # handle minLength 1


# ---------------------------------------------------------------- per-record validation
def test_validate_flags_only_nonconforming_records():
    assert validate_author(_author()) == []
    assert validate_article(_article()) == []
    assert validate_article(_article(body="")) != []                 # empty body
    assert validate_article(_article(section="economics")) != []     # legacy section, off the pinned vocab
    assert validate_author(_author(handle="")) != []                 # empty handle


# ---------------------------------------------------------------- the whole-DB pass
def test_normalize_corpus_reports_every_violation_in_one_pass():
    authors = [_author(), _author(handle="")]
    articles = [_article(), _article(slug="s2", section="economics")]
    verdict = normalize_corpus(fetch_authors_fn=lambda: authors, fetch_articles_fn=lambda: articles)
    assert verdict["authors"]["checked"] == 2
    assert verdict["articles"]["checked"] == 2
    assert verdict["ok"] is False
    assert any(v["handle"] == "" for v in verdict["authors"]["violations"])
    assert any(v["slug"] == "s2" for v in verdict["articles"]["violations"])


def test_normalize_verb_emits_verdict_and_branchable_exit_code():
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli._normalize_main([], fetch_authors_fn=lambda: [_author()],
                                 fetch_articles_fn=lambda: [_article()])
    assert rc == 0
    assert json.loads(buf.getvalue())["ok"] is True

    buf2 = io.StringIO()
    with redirect_stdout(buf2):
        rc2 = cli._normalize_main([], fetch_authors_fn=lambda: [_author(handle="")],
                                  fetch_articles_fn=lambda: [])
    assert rc2 == 2  # done_with_errors: some records violate their contract
