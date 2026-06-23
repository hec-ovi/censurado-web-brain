"""Content hash: a faithful port of the platform's ``domain.ContentHash``.

The platform dedups articles and keys idempotency on a SHA-256 over the article's
normalized (trimmed) title, body, author, and section, each LENGTH-PREFIXED by
its UTF-8 byte length so a shifted field boundary cannot collide. The Go source
(``internal/domain/article.go``) is:

    func ContentHash(title, body, author, section string) string {
        h := sha256.New()
        for _, f := range []string{title, body, author, section} {
            fmt.Fprintf(h, "%d:%s", len(f), f)
        }
        return hex.EncodeToString(h.Sum(nil))
    }

with the four fields already trimmed by ``NewArticle`` before hashing. ``len(f)``
in Go is the BYTE length of the UTF-8 string, reproduced here as
``len(field.encode("utf-8"))``. Go's ``strings.TrimSpace`` and Python's
``str.strip`` both trim Unicode whitespace, so they agree on the article fields.

This is the single source of truth for the hash inside the harness: the publish
client and the test fake both import it, so the idempotency key the harness mints
and the dedup the platform performs agree by construction. Cross-repo equality
with the live Go implementation is exercised end to end at the publish seam
(Step 7).
"""

from __future__ import annotations

import hashlib

__all__ = ["content_hash"]


def content_hash(title: str, body: str, author: str, section: str) -> str:
    """Return the 64-char hex SHA-256 identity of an article.

    Fields are trimmed (matching the platform's ``strings.TrimSpace`` before
    hashing) and length-prefixed by UTF-8 byte length, exactly as the Go
    implementation does.
    """
    h = hashlib.sha256()
    for field in (title.strip(), body.strip(), author.strip(), section.strip()):
        raw = field.encode("utf-8")
        h.update(f"{len(raw)}:".encode("ascii"))
        h.update(raw)
    return h.hexdigest()
