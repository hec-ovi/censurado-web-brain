"""The ComfyUI client end to end, driven against the shared fake.

The ``ComfyClient`` is exercised through its REAL entry point against the fake ComfyUI
routes: submit -> poll -> fetch the PNG, the reference upload, a bad graph (400), a render
timeout, and an execution error. The art direction (deciding what to render) is the
operator's CLI agent's job now, not the brain's, so only the render client lives here.
"""

from __future__ import annotations

import pytest

from newsroom.imagery import ComfyError
from newsroom.imagery.comfy_client import ComfyClient
from newsroom.imagery.graph import build_graph


# ----- the ComfyUI client against the fake -----


def test_comfy_client_submits_polls_and_fetches_the_png(fake):
    client = ComfyClient(base_url=fake.comfy_url)
    graph = build_graph(workflow="flux2_klein", prompt="a quiet illustration", seed=7,
                        width=512, height=512, steps=4)

    png = client.generate(graph)

    assert png.startswith(b"\x89PNG")  # real image bytes round-tripped via /view
    assert len(fake.state.image_requests) == 1
    record = fake.state.image_requests[0]
    assert record["status"] == 200
    # The submitted graph carries the prompt + steps (render params), never a length cap.
    assert record["body"]["prompt"]["4"]["inputs"]["text"] == "a quiet illustration"
    assert record["body"]["prompt"]["7"]["inputs"]["steps"] == 4


def test_comfy_client_uploads_a_reference_image(fake):
    client = ComfyClient(base_url=fake.comfy_url)
    name = client.upload_image(b"\x89PNG reference bytes", filename="ref.png")
    assert name  # a usable LoadImage name
    assert len(fake.state.upload_requests) == 1
    assert fake.state.upload_requests[0]["size"] > 0


def test_comfy_client_raises_on_a_rejected_graph(fake):
    client = ComfyClient(base_url=fake.comfy_url)
    with pytest.raises(ComfyError):
        client.generate({})  # an empty graph is a 400 at /prompt, surfaced loudly


def test_comfy_client_times_out_when_the_render_never_completes(fake):
    fake.state.image_ready.clear()  # /history reports never-complete
    client = ComfyClient(base_url=fake.comfy_url, timeout=0.4, poll_interval=0.05)
    graph = build_graph(workflow="flux2_klein", prompt="x", seed=1, width=256, height=256, steps=4)
    with pytest.raises(ComfyError):
        client.generate(graph)


def test_comfy_client_raises_on_an_execution_error(fake):
    # The graph is accepted at /prompt but the executor reports a failure: /history
    # carries status_str "error". This is distinct from a bad graph (400) and a timeout.
    fake.state.image_error = True
    client = ComfyClient(base_url=fake.comfy_url, timeout=2, poll_interval=0.02)
    graph = build_graph(workflow="flux2_klein", prompt="x", seed=1, width=256, height=256, steps=4)
    with pytest.raises(ComfyError):
        client.generate(graph)
