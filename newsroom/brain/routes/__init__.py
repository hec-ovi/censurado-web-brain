"""Versioned-by-chunk routers mounted onto the brain app.

Each module here defines a tagged ``APIRouter`` that ``newsroom.brain.app.create_app``
includes. Routers read their dependencies off ``request.app.state`` (the shared store,
the single connection lock) exactly like the inline routes do, so mounting one adds no
new wiring. They import the shared ``_problem`` helper from ``newsroom.brain.problems``
(a neutral module) rather than from ``app``, which keeps the import one-directional
(``app -> routes``) with no cycle.

Auth is wired in ONE place, not here: ``create_app`` hangs the shared
``newsroom.brain.auth.require_auth`` seam on ``FastAPI(dependencies=[...])``, which
covers every inline route AND every router below at once, so enabling auth never means
editing each router.
"""

from __future__ import annotations

from newsroom.brain.routes.admin import router as admin_router
from newsroom.brain.routes.editorial import router as editorial_router
from newsroom.brain.routes.personas import router as personas_router
from newsroom.brain.routes.portals import router as portals_router
from newsroom.brain.routes.runs import router as runs_router
from newsroom.brain.routes.status import router as status_router

__all__ = [
    "admin_router",
    "editorial_router",
    "personas_router",
    "portals_router",
    "runs_router",
    "status_router",
]
