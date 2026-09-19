"""Exercise selection and upload through the photograph box's public entry point."""
import socket
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from source_images import SourceImages  # noqa: E402

HTML = '''<title>Webb observa Centaurus A</title>
<meta property="og:image" content="/centaurus.jpg">
<script type="application/ld+json">{"@type":"NewsArticle","image":{
 "@type":"ImageObject","contentUrl":"/centaurus.jpg",
 "caption":"Centaurus A, vista infrarroja de Webb", "creditText":"NASA / ESA"}}</script>
<figure><img src="/football.jpg" alt="Un partido de fútbol"><figcaption>La final del torneo</figcaption></figure>'''


@pytest.fixture
def media(monkeypatch):
    monkeypatch.setenv("TEST_MEDIA_TOKEN", "test-token")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))])
    uploads = []

    def serve(request):
        if request.method == "POST":
            uploads.append(request)
            return httpx.Response(201, json={"url": "/media/photo.jpg"})
        if request.url.path.endswith(".jpg"):
            return httpx.Response(200, headers={"Content-Type": "image/jpeg"}, content=b"photo")
        return httpx.Response(200, headers={"Content-Type": "text/html"}, text=HTML)

    with httpx.Client(transport=httpx.MockTransport(serve)) as client:
        yield SourceImages({"base_url": "https://backend.test", "token_env": "TEST_MEDIA_TOKEN"}, client), uploads


def test_context_selection_uploads_only_the_chosen_photo(media):
    selector, uploads = media
    calls = []

    def choose(prompt, images):
        calls.append(prompt)
        assert "Centaurus A, vista infrarroja de Webb" in prompt
        assert "La final del torneo" in prompt
        assert len(images) == 2
        return {"id": "1", "alt": "Centaurus A observada por Webb"}

    photo = selector.attach(["https://source.test/article"],
                            {"title": "Webb revela Centaurus A", "body": "Nueva observación de la galaxia."}, choose)
    assert len(calls) == len(uploads) == 1
    assert uploads[0].headers["Authorization"] == "Bearer test-token"
    assert photo == {"image": "/media/photo.jpg", "image_alt": "Centaurus A observada por Webb",
                     "image_caption": "Centaurus A, vista infrarroja de Webb", "image_credit": "NASA / ESA",
                     "image_source": "https://source.test/article",
                     "image_original": "https://source.test/centaurus.jpg"}


@pytest.mark.parametrize("selected", [None, "invented-url"])
def test_no_matching_candidate_is_text_only(media, selected):
    selector, uploads = media
    photo = selector.attach(["https://source.test/article"], {"title": "Otro tema", "body": "Texto"},
                            lambda *args: {"id": selected})
    assert photo == {}
    assert uploads == []


def test_unreachable_sources_do_not_call_model_or_upload(media, monkeypatch):
    selector, uploads = media
    def unavailable(*args, **kwargs):
        raise socket.gaierror("unavailable")
    monkeypatch.setattr(socket, "getaddrinfo", unavailable)
    def choose(*args):
        pytest.fail("no image candidates should skip inference")
    assert selector.attach(["https://unavailable.test"], {"title": "T", "body": "B"}, choose) == {}
    assert uploads == []
