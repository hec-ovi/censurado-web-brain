"""Image generation: the ComfyUI rendering backend.

This package talks to a local ComfyUI server running FLUX.2 klein. It holds the ComfyUI
HTTP client and the parameterized workflow graph builder. The platform media upload lives
in ``newsroom.publish.media``. The art-direction step (deciding what to render) is the
operator's CLI agent's job, not the brain's.

Nothing here sets an output-length cap; image size and step count are render parameters,
not generation-length caps.
"""

from __future__ import annotations

from newsroom.imagery.comfy_client import ComfyClient, ComfyError
from newsroom.imagery.graph import build_graph, load_template

__all__ = [
    "ComfyClient",
    "ComfyError",
    "build_graph",
    "load_template",
]
