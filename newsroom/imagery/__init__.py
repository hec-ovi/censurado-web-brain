"""Image generation: the art-director's rendering backend.

The brain's text inference talks to a local LLM; this package is the parallel seam for
imagery, talking to a local ComfyUI server running FLUX.2 klein. It holds the ComfyUI
HTTP client, the parameterized workflow graph builder, the source reference-image
collector, and the ``Illustrator`` that composes them with the art-director LLM step
(``newsroom.pipeline.artdirect``) and the platform media upload
(``newsroom.publish.media``).

Like the rest of the harness, nothing here sets an output-length cap; image size and
step count are render parameters, not generation-length caps.
"""

from __future__ import annotations

from newsroom.imagery.comfy_client import ComfyClient, ComfyError
from newsroom.imagery.graph import build_graph, load_template
from newsroom.imagery.illustrator import Illustrator, ImageResult
from newsroom.imagery.references import ReferenceImage, collect_reference_images

__all__ = [
    "ComfyClient",
    "ComfyError",
    "build_graph",
    "load_template",
    "Illustrator",
    "ImageResult",
    "ReferenceImage",
    "collect_reference_images",
]
