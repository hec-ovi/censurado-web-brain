"""The imagery seam end to end, driven against the shared fake.

Two layers are exercised through their REAL entry points:

  * the ``ComfyClient`` against the fake ComfyUI routes (submit -> poll -> fetch, the
    reference upload, a bad graph, and a render timeout); and
  * the full ``Illustrator`` against the fake (collect references -> art-direct via the
    chat fake -> upload references -> render -> upload the PNG to the media endpoint),
    proving a finished article turns into a hosted ``/media/...`` URL.

The art-director call rides the same ``/v1/chat/completions`` fake as every other
model call, so the global no-output-cap guard (conftest teardown) covers it too.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from newsroom.config import load_settings
from newsroom.imagery import ComfyError, Illustrator, collect_reference_images
from newsroom.imagery.comfy_client import ComfyClient
from newsroom.imagery.graph import build_graph
from newsroom.pipeline.artdirect import art_direct
from newsroom.inference.provider import DIALECTS, ProviderConfig
from newsroom.personas import Persona
from newsroom.pipeline import ArticleBudget
from newsroom.contracts.article import PublishArticleInput
from newsroom.research.ledger import Ledger


def _cfg(fake, model: str) -> ProviderConfig:
    return ProviderConfig(
        role="art_director", provider="local", base_url=f"{fake.base_url}/v1", model=model,
        **DIALECTS["local"],
    )


def _big_budget() -> ArticleBudget:
    return ArticleBudget(token_budget=10_000_000, wall_clock_s=1e9, clock=lambda: 0.0)


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


# ----- the art director's budget debit and reference extraction -----


def test_art_director_call_debits_the_per_article_budget(fake):
    persona = Persona(id="ada", display_name="Ada", beat="tech", who_i_am="chips", style="dry")
    fake.state.script_chat(
        json.dumps({"prompt": "an illustration", "alt": "a", "references": []}),
        usage={"prompt_tokens": 10, "completion_tokens": 40, "total_tokens": 50},
    )
    budget = ArticleBudget(token_budget=10_000, wall_clock_s=1e9, clock=lambda: 0.0)

    art_direct(title="T", body="B", section="tech", persona=persona, references=[],
               cfg=_cfg(fake, "art-model"), prompts_dir=load_settings().prompts_dir, budget=budget)

    assert budget.spent_tokens >= 50  # the art-director call charged the shared ceiling


def test_collect_reference_images_extracts_og_image_and_degrades():
    led = _ledger()  # one source row: https://src.test/a
    refs = collect_reference_images(
        led, limit=2, fetch=lambda u: '<meta property="og:image" content="https://src.test/p.jpg">'
    )
    assert [r.image_url for r in refs] == ["https://src.test/p.jpg"]

    # A literal '>' inside the quoted URL must not truncate the tag (the regex fix).
    refs2 = collect_reference_images(
        led, limit=1, fetch=lambda u: '<meta property="og:image" content="https://x/a>b.png"><title>t</title>'
    )
    assert refs2 and refs2[0].image_url == "https://x/a>b.png"

    # No og:image and a fetch that raises both degrade to no reference (never raise).
    assert collect_reference_images(led, limit=1, fetch=lambda u: "<html></html>") == []

    def boom(_u):
        raise RuntimeError("network down")

    assert collect_reference_images(led, limit=1, fetch=boom) == []


# ----- the full Illustrator: article -> hosted /media URL -----


def _article() -> PublishArticleInput:
    return PublishArticleInput(
        title="Chips ship early", body="The chip shipped today. https://src.test/a",
        author="ada", section="tech", metadata={"newsroom": {"run_id": "r1"}},
    )


def _ledger() -> Ledger:
    led = Ledger(clock=lambda: datetime(2026, 6, 23, tzinfo=timezone.utc))
    led.add(claim="the chip shipped", url="https://src.test/a", snippet="it shipped")
    return led


def test_illustrator_turns_an_article_into_a_hosted_image_with_a_reference(fake):
    persona = Persona(id="ada", display_name="Ada", beat="tech", who_i_am="I cover chips.",
                      style="dry and precise")
    # The art director's brief (parsed by pydantic-ai from the chat fake's reply).
    fake.state.script_chat(json.dumps(
        {"prompt": "a symbolic screen-print of a microchip leaving a factory",
         "alt": "an illustrated microchip", "references": [0]}
    ))

    illustrate = Illustrator(
        comfy=ComfyClient(base_url=fake.comfy_url),
        art_director_cfg=_cfg(fake, "art-model"),
        prompts_dir=load_settings().prompts_dir,
        media_base_url=fake.media_url,
        operator_token="op-token",
        # In-process doubles for the two network reads, so the test never leaves the box.
        fetch_page=lambda url: '<meta property="og:image" content="https://src.test/hero.jpg">',
        download_image=lambda url: b"\x89PNG source-photo bytes",
    )

    result = illustrate(article=_article(), persona=persona, ledger=_ledger(), budget=_big_budget())

    assert result is not None
    assert result.url.startswith("/media/") and result.url.endswith(".png")
    assert result.references == ["https://src.test/hero.jpg"]  # the chosen source image
    # The reference was uploaded to ComfyUI and folded into the graph as a ReferenceLatent.
    assert len(fake.state.upload_requests) == 1
    graph = fake.state.image_requests[-1]["body"]["prompt"]
    assert any(node.get("class_type") == "ReferenceLatent" for node in graph.values())
    # The rendered PNG was uploaded to the platform media endpoint.
    assert len(fake.state.media_requests) == 1
    assert fake.state.media_requests[0]["url"] == result.url


def test_illustrator_renders_text_only_when_no_reference_is_found(fake):
    persona = Persona(id="ada", display_name="Ada", beat="tech", who_i_am="chips", style="dry")
    fake.state.script_chat(json.dumps({"prompt": "an abstract collage", "alt": "art", "references": []}))

    illustrate = Illustrator(
        comfy=ComfyClient(base_url=fake.comfy_url),
        art_director_cfg=_cfg(fake, "art-model"),
        prompts_dir=load_settings().prompts_dir,
        media_base_url=fake.media_url,
        operator_token="op-token",
        fetch_page=lambda url: "<html><head></head></html>",  # no og:image
        download_image=lambda url: b"unused",
    )

    result = illustrate(article=_article(), persona=persona, ledger=_ledger(), budget=_big_budget())

    assert result is not None and result.references == []
    assert fake.state.upload_requests == []  # no reference uploaded
    graph = fake.state.image_requests[-1]["body"]["prompt"]
    assert not any(node.get("class_type") == "ReferenceLatent" for node in graph.values())


def test_illustrator_raises_on_a_hard_render_failure(fake):
    # The Illustrator does NOT swallow a hard failure: it raises, and the pipeline's
    # _art_direct_image is what catches it and degrades to no image (proven separately in
    # test_article_pipeline). This is the producing half of that two-part contract.
    fake.state.image_ready.clear()  # the render never completes -> the client times out
    persona = Persona(id="ada", display_name="Ada", beat="tech", who_i_am="chips", style="dry")
    fake.state.script_chat(json.dumps({"prompt": "art", "alt": "a", "references": []}))

    illustrate = Illustrator(
        comfy=ComfyClient(base_url=fake.comfy_url, timeout=0.4, poll_interval=0.05),
        art_director_cfg=_cfg(fake, "art-model"),
        prompts_dir=load_settings().prompts_dir,
        media_base_url=fake.media_url,
        operator_token="op-token",
        fetch_page=lambda url: "<html></html>",
        download_image=lambda url: b"x",
    )

    with pytest.raises(ComfyError):
        illustrate(article=_article(), persona=persona, ledger=_ledger(), budget=_big_budget())
