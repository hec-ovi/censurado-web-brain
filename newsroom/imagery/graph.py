"""Build a ComfyUI API-format prompt graph from a checked-in template.

The base template (``templates/flux2_klein_t2i.json``) is the proven FLUX.2 klein
text-to-image graph, taken verbatim from a known-good ComfyUI render (node-id keyed
``{class_type, inputs}`` with links expressed as ``[node_id, slot]``). ``build_graph``
fills the few parameterized inputs by node id (the positive prompt, the seed, the
size, the step count) and, when reference images are supplied, augments the graph
with a FLUX.2 reference chain so the illustration is conditioned on the source
imagery.

Reference conditioning uses FLUX.2 klein's native support: each reference is loaded
(``LoadImage``), VAE-encoded (``VAEEncode``) and folded into the positive
conditioning (``ReferenceLatent``); the chained conditioning then feeds the guider.
The init latent stays the empty FLUX.2 latent, so the reference steers the *content*
without copying the source pixel-for-pixel. The reference node wiring is built in
code (not a second static template) so any number of references chains cleanly; the
class names below are the only thing to adjust if a ComfyUI build renames a node.

No output-length cap lives anywhere here: ``steps``/``width``/``height`` are render
parameters, not generation-length caps.
"""

from __future__ import annotations

import copy
import json
from importlib import resources

__all__ = ["build_graph", "load_template", "TEMPLATES"]

# Node ids in the base template that carry parameters (see flux2_klein_t2i.json).
_POSITIVE = "4"        # CLIPTextEncode (positive prompt text)
_LATENT = "6"          # EmptyFlux2LatentImage (width/height)
_SCHEDULER = "7"       # Flux2Scheduler (steps/width/height)
_NOISE = "9"           # RandomNoise (noise_seed)
_GUIDER = "10"         # CFGGuider (positive conditioning input)
_VAE = "3"             # VAELoader (reused by VAEEncode of each reference)
_SAVE = "13"           # SaveImage (filename_prefix)

# Reference-chain node class names (FLUX.2 native reference conditioning). Built-in
# upstream ComfyUI nodes; adjust here if a build renames them.
_LOAD_IMAGE = "LoadImage"
_VAE_ENCODE = "VAEEncode"
_REFERENCE_LATENT = "ReferenceLatent"

# Workflow family -> base template filename. One family today (FLUX.2 klein); a new
# model is a new template file plus an entry here, no code change in the pipeline.
TEMPLATES = {"flux2_klein": "flux2_klein_t2i.json"}


def load_template(workflow: str) -> dict:
    """Load a base API graph template by workflow-family name."""
    try:
        name = TEMPLATES[workflow]
    except KeyError as exc:
        raise ValueError(f"unknown image workflow {workflow!r}; known: {sorted(TEMPLATES)}") from exc
    text = resources.files("newsroom.imagery").joinpath("templates", name).read_text("utf-8")
    return json.loads(text)


def build_graph(
    *,
    workflow: str,
    prompt: str,
    seed: int,
    width: int,
    height: int,
    steps: int,
    reference_names: list[str] | None = None,
    filename_prefix: str = "censurado",
) -> dict:
    """Return a ready-to-POST ComfyUI graph: the template with the prompt, seed, size,
    and steps filled in, plus a reference chain when ``reference_names`` is non-empty.

    ``reference_names`` are filenames already uploaded to the ComfyUI server (via the
    client's ``upload_image``); each is loaded and folded into the positive
    conditioning. An empty/omitted list yields the plain text-to-image graph."""
    graph = copy.deepcopy(load_template(workflow))

    graph[_POSITIVE]["inputs"]["text"] = prompt
    graph[_NOISE]["inputs"]["noise_seed"] = int(seed)
    graph[_LATENT]["inputs"]["width"] = int(width)
    graph[_LATENT]["inputs"]["height"] = int(height)
    graph[_SCHEDULER]["inputs"]["width"] = int(width)
    graph[_SCHEDULER]["inputs"]["height"] = int(height)
    graph[_SCHEDULER]["inputs"]["steps"] = int(steps)
    if _SAVE in graph:
        graph[_SAVE]["inputs"]["filename_prefix"] = filename_prefix

    refs = reference_names or []
    if refs:
        _attach_references(graph, refs)
    return graph


def _attach_references(graph: dict, reference_names: list[str]) -> None:
    """Chain LoadImage -> VAEEncode -> ReferenceLatent for each reference and repoint
    the guider's positive input at the chained conditioning. Added node ids start at
    100 to never collide with the template's 1..13."""
    conditioning = [_POSITIVE, 0]  # start from the text conditioning
    next_id = 100
    for name in reference_names:
        load_id, encode_id, ref_id = str(next_id), str(next_id + 1), str(next_id + 2)
        next_id += 3
        graph[load_id] = {"class_type": _LOAD_IMAGE, "inputs": {"image": name}}
        graph[encode_id] = {
            "class_type": _VAE_ENCODE,
            "inputs": {"pixels": [load_id, 0], "vae": [_VAE, 0]},
        }
        graph[ref_id] = {
            "class_type": _REFERENCE_LATENT,
            "inputs": {"conditioning": conditioning, "latent": [encode_id, 0]},
        }
        conditioning = [ref_id, 0]
    graph[_GUIDER]["inputs"]["positive"] = conditioning
