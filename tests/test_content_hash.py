"""Content-hash conformance.

The golden vector below was produced by an INDEPENDENT Go program that replicates
``internal/domain/article.go`` ``ContentHash`` exactly, run against the platform's
own hash construction. Python and Go agree on it byte for byte, which is what lets
the newsroom mint an idempotency key the platform will accept as identical.
"""

from __future__ import annotations

from newsroom.contracts.hashing import content_hash

# Independently produced by the Go reference (golang:1.23) for the same inputs.
GOLDEN = "35dc117a20ee9cb7a2f9bdb40713cb950e4ccb886f54b99ea25673d56f3fbf0a"


def test_matches_go_reference_vector():
    assert content_hash("Hello World", "Body text here", "agent-a", "tech") == GOLDEN


def test_is_64_hex_chars():
    h = content_hash("t", "b", "au", "s")
    assert len(h) == 64
    int(h, 16)  # raises if not hex


def test_deterministic():
    assert content_hash("t", "b", "au", "s") == content_hash("t", "b", "au", "s")


def test_trims_fields_before_hashing():
    assert content_hash(" t ", "  b", "au  ", " s ") == content_hash("t", "b", "au", "s")


def test_whitespace_input_matches_trimmed_go_golden():
    # The platform trims (NewArticle) BEFORE hashing, and content_hash trims
    # internally, so feeding raw whitespace-bearing values must reproduce the Go
    # golden computed from the already-trimmed inputs. This pins where
    # normalization happens and proves end-to-end parity for messy input: a caller
    # may pass untrimmed fields and still mint the key the platform will accept.
    assert content_hash("  Hello World ", "\tBody text here\n", " agent-a ", "tech ") == GOLDEN


def test_length_prefix_prevents_boundary_collision():
    # "ab"+"c" vs "a"+"bc": a naive concat would collide; the length prefix must not.
    assert content_hash("ab", "c", "x", "y") != content_hash("a", "bc", "x", "y")


def test_distinct_fields_distinct_hash():
    base = content_hash("t", "b", "au", "s")
    assert content_hash("t", "b2", "au", "s") != base
    assert content_hash("t", "b", "au", "s2") != base
