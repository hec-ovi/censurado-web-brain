"""Reconcile the brain's local personas against the platform's author registry.

The platform owns author EXISTENCE and the public display fields; the brain owns
the private identity prompt. On every bootstrap the brain pulls the live author
list and makes its personas match, WITHOUT ever touching a prompt:

  * a platform handle the brain already has as a persona -> refresh the public
    fields (name/about/avatar) from the platform, and mark it active when it
    carries a usable prompt (a real writing persona), else leave it inactive.
  * a platform handle the brain does not have -> create a SHELL persona (public
    fields only, empty prompt) and leave it INACTIVE: it cannot write until an
    operator gives it a local identity prompt.
  * a local persona whose handle is GONE from the platform -> soft-deactivate it
    (active=0). Never delete: the assignments foreign key forbids it and the prompt
    is kept, so re-adding the author on the platform reactivates the same persona.

One safety rule keeps a fresh or unreachable platform from emptying the newsroom:
an EMPTY author list is a NO-OP (the pre-backfill state; the caller also skips on a
fetch error), so deactivation only ever runs against a populated registry.

Only ``display_name``/``about``/``avatar_path`` (the exact three fields the publish
path stamps onto an article) and the ``active`` flag are ever written here; the
prompt fields (who_i_am/style/language/few_shots/sources/beat) are never in the
change set passed to ``update``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from newsroom.mirror.client import WebAuthor
from newsroom.personas.store import Persona, PersonaStore

__all__ = ["ReconcileResult", "reconcile_personas", "SHELL_BEAT"]

# A web-created author arrives with no beat (the beat is a private editorial choice).
# The shell gets a valid placeholder so the NOT NULL/CHECK hold; the operator sets the
# real beat with the prompt. The shell is inactive, so this beat never drives a run.
SHELL_BEAT = "world"


@dataclass
class ReconcileResult:
    """What one reconcile did, per category, so a bootstrap summary is honest about
    mirror effects. ``skipped`` is True when the registry was empty (no-op)."""

    created: list[str] = field(default_factory=list)
    refreshed: list[str] = field(default_factory=list)
    reactivated: list[str] = field(default_factory=list)
    deactivated: list[str] = field(default_factory=list)
    skipped: bool = False

    def as_dict(self) -> dict:
        return {
            "created": self.created,
            "refreshed": self.refreshed,
            "reactivated": self.reactivated,
            "deactivated": self.deactivated,
            "skipped": self.skipped,
        }


def _public_changes(persona: Persona, web: WebAuthor) -> dict:
    """The public fields to update: a NON-EMPTY platform value that differs from the
    local one. An empty platform field never blanks a good local value, so a half-
    filled web author cannot wipe an existing bio or avatar."""
    changes: dict = {}
    if web.name and web.name != persona.display_name:
        changes["display_name"] = web.name
    if web.bio and web.bio != persona.about:
        changes["about"] = web.bio
    if web.avatar and web.avatar != persona.avatar_path:
        changes["avatar_path"] = web.avatar
    return changes


def reconcile_personas(store: PersonaStore, web_authors: list[WebAuthor]) -> ReconcileResult:
    """Make the local personas match the platform author list. See the module
    docstring for the full contract. An empty ``web_authors`` is a no-op."""
    result = ReconcileResult()
    if not web_authors:
        result.skipped = True
        return result

    web_by_handle = {a.handle: a for a in web_authors}

    for handle, web in web_by_handle.items():
        existing = store.get(handle)
        if existing is None:
            store.create(
                Persona(
                    id=handle,
                    display_name=web.name or handle,
                    beat=SHELL_BEAT,
                    who_i_am="",
                    style="",
                    about=web.bio,
                    avatar_path=web.avatar,
                    active=False,
                )
            )
            result.created.append(handle)
            continue

        changes = _public_changes(existing, web)
        # A persona may write only with a real prompt; on the platform AND prompted
        # means it should be active again if a prior reconcile had deactivated it.
        if existing.who_i_am.strip() and not existing.active:
            changes["active"] = True
            result.reactivated.append(handle)
            if changes:
                store.update(handle, **changes)
            continue
        if changes:
            store.update(handle, **changes)
            result.refreshed.append(handle)

    # Local personas whose handle is gone from a POPULATED registry -> soft-deactivate.
    for persona in store.list(include_inactive=True):
        if persona.id not in web_by_handle and persona.active:
            store.update(persona.id, active=False)
            result.deactivated.append(persona.id)

    return result
