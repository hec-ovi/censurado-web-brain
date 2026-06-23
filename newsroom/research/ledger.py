"""The claim-source ledger: a journalist's grounding and audit trail.

Every factual claim a journalist will publish is backed by a ledger row
``{claim, url, snippet, fetched_at}`` (architecture doc A.7/B.1). The ledger is the
shared spine of the per-article pipeline: research fills it, the draft cites it,
fact-check verifies against it, and a digest of it is stamped into
``metadata.newsroom`` at publish time.

Rows are deduplicated BY URL: a source already in the ledger is not added again, so
``add`` returning False is exactly the signal the research-loop stall detector keys
on (a step that introduces no new URL made no progress, see ``research.loop``).
``fetched_at`` comes from an injectable clock so provenance is deterministic in
tests. ``digest`` is a stable content hash over the sources (URL + claim + snippet,
order-independent, clock-independent), suitable as the audit anchor.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone

__all__ = ["LedgerRow", "Ledger"]


@dataclass(frozen=True)
class LedgerRow:
    claim: str
    url: str
    snippet: str
    fetched_at: str


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Ledger:
    """An append-only, URL-deduplicated set of claim-source rows."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._rows: list[LedgerRow] = []
        self._urls: set[str] = set()
        self._clock = clock or _utcnow

    def add(self, *, claim: str, url: str, snippet: str, fetched_at: str | None = None) -> bool:
        """Add one source row. Returns True if the URL was new (a real new row),
        False if the URL was already present (skipped, counts as no progress)."""
        key = url.strip()
        if not key or key in self._urls:
            return False
        self._urls.add(key)
        self._rows.append(
            LedgerRow(
                claim=claim,
                url=key,
                snippet=snippet,
                fetched_at=fetched_at or self._clock().isoformat(),
            )
        )
        return True

    def add_results(self, results, *, claim: str) -> int:
        """Add every result (each needs ``url`` and ``snippet``) under one claim.
        Returns the count of NEW rows (new URLs), the research loop's progress
        signal. Accepts dataclasses/objects with attributes or plain dicts."""
        new = 0
        for r in results:
            url = r["url"] if isinstance(r, dict) else getattr(r, "url", "")
            snippet = r["snippet"] if isinstance(r, dict) else getattr(r, "snippet", "")
            if self.add(claim=claim, url=url, snippet=snippet):
                new += 1
        return new

    def has_url(self, url: str) -> bool:
        return url.strip() in self._urls

    @property
    def rows(self) -> list[LedgerRow]:
        return list(self._rows)

    def to_artifact(self) -> list[dict]:
        """A plain-data handoff for the drafter (artifact, not transcript)."""
        return [
            {"claim": r.claim, "url": r.url, "snippet": r.snippet, "fetched_at": r.fetched_at}
            for r in self._rows
        ]

    def digest(self) -> str:
        """A stable ``sha256:...`` over the sources, independent of insertion order
        and of ``fetched_at`` (provenance, not content)."""
        canon = sorted((r.url, r.claim, r.snippet) for r in self._rows)
        payload = json.dumps(canon, ensure_ascii=False, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def __len__(self) -> int:
        return len(self._rows)

    def __iter__(self) -> Iterator[LedgerRow]:
        return iter(self._rows)
