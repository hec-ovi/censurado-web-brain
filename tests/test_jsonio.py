"""The shared JSON extractor: tolerant of a local model's prose-wrapped output.

These pin the brittleness the evaluator/planner/synthesis all depend on: clean
JSON, a code fence, AND prose before/after the JSON must all parse, while a
genuinely JSON-free reply still raises (callers map that to their error contract).
"""

from __future__ import annotations

import json

import pytest

from newsroom.jsonio import extract_json


def test_clean_object_and_array():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json("[1, 2, 3]") == [1, 2, 3]


def test_code_fence_is_stripped():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_preamble_prose_before_json():
    assert extract_json('Sure, here is the result:\n{"verdict": "PASS"}') == {"verdict": "PASS"}


def test_trailer_prose_after_json():
    assert extract_json('{"verdict": "REVISE"}\n\nHope that helps!') == {"verdict": "REVISE"}


def test_prose_around_an_array():
    assert extract_json('The sub-questions are: ["who", "what"]. Good luck.') == ["who", "what"]


def test_nested_object_with_array_and_a_brace_in_a_string():
    text = 'Here you go: {"a": [1, 2], "b": "a } brace in a string"} -- done'
    assert extract_json(text) == {"a": [1, 2], "b": "a } brace in a string"}


def test_genuinely_no_json_raises():
    with pytest.raises(json.JSONDecodeError):
        extract_json("there is no json here at all")


def test_empty_reply_raises():
    with pytest.raises(json.JSONDecodeError):
        extract_json("   ")
