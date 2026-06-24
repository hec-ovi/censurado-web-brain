"""The illustrator: compose the art director, ComfyUI, and the media upload.

This is the production ``illustrate`` seam injected into the per-article pipeline (the
same way ``make_ledger`` and ``search_news`` are injected). Given a finalized article
it: collects reference images from the article's sources, asks the art director for an
illustration brief, uploads any chosen references to ComfyUI, builds and renders the
FLUX.2 graph, uploads the PNG to the platform media endpoint, and returns an
``ImageResult`` the pipeline stamps into ``metadata.image``.

The whole step is best-effort. References degrade to none; any hard failure raises and
the pipeline catches it and publishes the article WITHOUT an image (it never drops a
finalized article over a missing picture). The art director's LLM call debits the
shared per-article budget; the render respects the budget's wall clock via the client
timeout. No output-length cap is set anywhere.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from newsroom.contracts.article import PublishArticleInput
from newsroom.imagery.comfy_client import ComfyClient
from newsroom.imagery.graph import build_graph
from newsroom.imagery.references import ReferenceImage, collect_reference_images
from newsroom.inference.provider import ProviderConfig
from newsroom.personas import Persona
from newsroom.pipeline.artdirect import art_direct
from newsroom.publish.media import upload_media
from newsroom.research.ledger import Ledger

__all__ = ["ImageResult", "Illustrator"]


@dataclass
class ImageResult:
    """A generated hero image. ``url`` is the public path stamped into
    ``metadata.image``; the rest is provenance kept on the assignment and in
    ``metadata.newsroom.image``."""

    url: str
    alt: str
    prompt: str
    seed: int
    workflow: str
    references: list[str] = field(default_factory=list)


def _seed_for(text: str) -> int:
    """A stable seed derived from the brief, so a re-render of the same brief is
    reproducible (no wall-clock / RNG dependency)."""
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16)


def _default_download(timeout: float) -> Callable[[str], bytes]:
    def download(url: str) -> bytes:
        resp = httpx.get(url, timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
        return resp.content

    return download


@dataclass
class Illustrator:
    """Assembled in ``build_run_deps`` from settings; called by the pipeline as
    ``illustrate(article=..., persona=..., ledger=..., budget=...)``.

    The network seams (``download_image``, ``fetch_page``, ``upload``) are injectable
    so a test drives the whole path against the in-repo fake without leaving the box."""

    comfy: ComfyClient
    art_director_cfg: ProviderConfig
    prompts_dir: object
    media_base_url: str
    operator_token: str
    workflow: str = "flux2_klein"
    width: int = 1024
    height: int = 1024
    steps: int = 4
    reference_limit: int = 2
    download_image: Callable[[str], bytes] | None = None
    fetch_page: Callable[[str], str | None] | None = None
    upload: Callable[..., object] = upload_media

    def __call__(
        self, *, article: PublishArticleInput, persona: Persona, ledger: Ledger, budget=None
    ) -> ImageResult | None:
        download = self.download_image or _default_download(self.comfy.timeout)

        # 1) Candidate reference images from the article's own sources (best-effort).
        references = collect_reference_images(
            ledger, limit=self.reference_limit, fetch=self.fetch_page
        )

        # 2) The art director's brief (persona-aware, budget-debited).
        brief = art_direct(
            title=article.title,
            body=article.body,
            section=article.section,
            persona=persona,
            references=references,
            cfg=self.art_director_cfg,
            prompts_dir=self.prompts_dir,
            budget=budget,
        )

        # 3) Upload the chosen references to ComfyUI (skip any that fail to fetch).
        chosen = _select(references, brief.references, self.reference_limit)
        reference_names: list[str] = []
        used: list[str] = []
        for ref in chosen:
            try:
                data = download(ref.image_url)
                reference_names.append(self.comfy.upload_image(data, filename="reference.png"))
                used.append(ref.image_url)
            except Exception:
                continue  # a missing reference degrades to text-only conditioning

        # 4) Build and render the graph.
        seed = _seed_for(f"{article.title}\n{brief.prompt}")
        graph = build_graph(
            workflow=self.workflow,
            prompt=brief.prompt,
            seed=seed,
            width=self.width,
            height=self.height,
            steps=self.steps,
            reference_names=reference_names,
        )
        png = self.comfy.generate(graph)

        # 5) Upload the PNG to the platform and return the public reference.
        asset = self.upload(png, base_url=self.media_base_url, token=self.operator_token)
        return ImageResult(
            url=asset.url,
            alt=brief.alt or article.title,
            prompt=brief.prompt,
            seed=seed,
            workflow=self.workflow,
            references=used,
        )


def _select(references: list[ReferenceImage], indices: list[int], limit: int) -> list[ReferenceImage]:
    """The references the art director chose, clamped to what exists and the limit."""
    chosen: list[ReferenceImage] = []
    for i in indices:
        if isinstance(i, int) and 0 <= i < len(references) and references[i] not in chosen:
            chosen.append(references[i])
        if len(chosen) >= limit:
            break
    return chosen
