"""Shared test fixtures.

The ``fake`` fixture runs the one shared fake (testkit/fake_server.py) as a real
uvicorn server on an OS-assigned port, so every test drives it over real HTTP, the
same way the brain will. uvicorn binds port 0 itself and we read back the port it
chose, so there is no bind-then-rebind race. On teardown the fixture enforces the
global rule: no chat request the fake ACCEPTED (2xx) ever carried an
output-length cap.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import httpx
import pytest
import uvicorn

from testkit.assertions import length_cap_keys_in
from testkit.fake_server import FakeState, create_fake_app


def _serve(app) -> tuple[uvicorn.Server, threading.Thread, int]:
    """Run a FastAPI app on an OS-assigned port in a daemon thread; return the
    server, its thread, and the bound port (read back, so no bind race)."""
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("server did not start in time")
        time.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    return server, thread, port


@dataclass
class Fake:
    """A handle to the running fake: its base URL and its mutable state."""

    base_url: str
    state: FakeState

    @property
    def chat_url(self) -> str:
        return f"{self.base_url}/v1/chat/completions"

    @property
    def articles_url(self) -> str:
        return f"{self.base_url}/articles"


@pytest.fixture
def fake():
    state = FakeState()
    app, state = create_fake_app(state)
    server, thread, port = _serve(app)

    try:
        yield Fake(base_url=f"http://127.0.0.1:{port}", state=state)
    finally:
        server.should_exit = True
        thread.join(timeout=5)

    # Global guard: every ACCEPTED chat request must be cap-free.
    for record in state.chat_requests:
        if record["status"] < 300:
            assert not length_cap_keys_in(record["body"]), (
                f"an accepted /v1/chat/completions request carried a length cap: {record['body']}"
            )


@pytest.fixture
def brain(fake, tmp_path):
    """The brain HTTP app, running on its own port, with inference pointed at the
    fake and a fresh file-backed persona store. Yields an httpx client. Depends on
    ``fake`` so the no-cap teardown guard covers synthesis calls too."""
    from newsroom.brain import create_app
    from newsroom.config import Settings

    settings = Settings(
        persona_db_path=tmp_path / "brain.db",
        inference_base_url=f"{fake.base_url}/v1",
    )
    server, thread, port = _serve(create_app(settings=settings))
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10)
    try:
        yield client
    finally:
        client.close()
        server.should_exit = True
        thread.join(timeout=5)
