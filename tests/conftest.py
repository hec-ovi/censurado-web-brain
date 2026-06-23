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

import pytest
import uvicorn

from testkit.assertions import length_cap_keys_in
from testkit.fake_server import FakeState, create_fake_app


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
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("fake server did not start in time")
        time.sleep(0.01)

    # uvicorn bound port 0; read the actual port it chose (no TOCTOU window).
    port = server.servers[0].sockets[0].getsockname()[1]

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
