"""Async persona synthesis over the real brain HTTP route.

POST /personas returns 202 immediately and runs the model OFF the request; the
client polls a job to completion and reads the finished draft. The headline test
proves non-blocking by HOLDING the model at the fake: the 202 comes back and the
job stays pending while the model is blocked, then completes once released, with
the negative few-shots intact.

Every test drives the brain over HTTP (httpx client from the ``brain`` fixture)
against the shared fake; nothing patches the brain's internals. The ``brain``
fixture depends on ``fake``, so the global no-length-cap teardown guard runs over
the synthesis request too.
"""

from __future__ import annotations

import json
import time

import pytest

# A complete synthesized persona, as the model is scripted to return it. Nested
# structure in the few-shots must survive the JSON round-trip end to end.
PERSONA_JSON = {
    "who_i_am": "I am Ada Rez. I cover machine learning the way a mechanic covers engines.",
    "about": "Ada Rez is a technology reporter focused on applied machine learning.",
    "style": "Short declaratives. Name the system, the claim, and the evidence. No hype.",
    "few_shots_pos": [
        {"prompt": "a new model launch", "good": "The lab released a 12B model; benchmarks are unverified."},
    ],
    "few_shots_neg": [
        {"prompt": "a new model launch", "bad": "This GAME-CHANGING AI will BLOW YOUR MIND!"},
        {"prompt": "a funding round", "bad": "In a seismic shift, the startup is poised to disrupt everything."},
    ],
    "sources": ["arxiv.org", "thestack.technology"],
}


def _seed(**over) -> dict:
    base = {
        "display_name": "Ada Rez",
        "beat": "tech",
        "seed": "A wire-style ML reporter who hates hype and grounds every claim.",
    }
    base.update(over)
    return base


def _poll_job(brain, job_id: str, *, want: str = "done", tries: int = 100, delay: float = 0.05) -> dict:
    """Poll a synthesis job until it reaches a terminal state, then return the body."""
    for _ in range(tries):
        body = brain.get(f"/personas/jobs/{job_id}").json()
        if body["status"] in ("done", "failed"):
            assert body["status"] == want, f"job ended {body['status']!r}: {body.get('error')}"
            return body
        time.sleep(delay)
    pytest.fail(f"job {job_id} did not reach a terminal state")


def test_health(brain):
    assert brain.get("/health").json() == {"ok": True}


def test_post_returns_202_with_job_and_location(brain, fake):
    fake.state.script_chat(json.dumps(PERSONA_JSON))
    r = brain.post("/personas", json=_seed())
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "pending"
    assert body["persona_id"] == "ada-rez"
    assert body["job_id"]
    assert r.headers["location"] == f"/personas/jobs/{body['job_id']}"


def test_synthesis_is_async_then_completes_with_negative_few_shots(brain, fake):
    # Hold the model: the route must answer before the completion returns.
    fake.state.hold_chat()
    fake.state.script_chat(json.dumps(PERSONA_JSON))

    r = brain.post("/personas", json=_seed())
    assert r.status_code == 202
    job_id = r.json()["job_id"]

    # While the model is held, the job is still pending and the persona is absent:
    # proof the 202 did not wait for synthesis.
    assert brain.get(f"/personas/jobs/{job_id}").json()["status"] == "pending"
    assert brain.get("/personas/ada-rez").status_code == 404

    fake.state.release_chat()
    job = _poll_job(brain, job_id)
    assert job["persona_id"] == "ada-rez"

    persona = brain.get("/personas/ada-rez").json()
    assert persona["display_name"] == "Ada Rez"
    assert persona["beat"] == "tech"
    assert persona["who_i_am"].startswith("I am Ada Rez")
    assert persona["sources"] == ["arxiv.org", "thestack.technology"]
    # The negative exemplars survive the model -> store -> HTTP round trip verbatim.
    assert persona["few_shots_neg"] == [
        {"prompt": "a new model launch", "bad": "This GAME-CHANGING AI will BLOW YOUR MIND!"},
        {"prompt": "a funding round", "bad": "In a seismic shift, the startup is poised to disrupt everything."},
    ]


def test_synthesis_request_carries_no_length_cap(brain, fake):
    fake.state.script_chat(json.dumps(PERSONA_JSON))
    job_id = brain.post("/personas", json=_seed()).json()["job_id"]
    _poll_job(brain, job_id)
    body = fake.state.chat_requests[-1]["body"]
    assert "max_tokens" not in body
    assert "max_completion_tokens" not in body
    # The fake's teardown guard also fails if any accepted chat carried a cap.


def test_invalid_beat_rejected_synchronously_without_calling_the_model(brain, fake):
    r = brain.post("/personas", json=_seed(beat="sports"))
    assert r.status_code == 422
    assert r.json()["code"] == "invalid_beat"
    assert fake.state.chat_requests == []  # no model call was made


def test_empty_seed_rejected(brain, fake):
    r = brain.post("/personas", json=_seed(seed="   "))
    assert r.status_code == 422
    assert r.json()["code"] == "validation_failed"
    assert fake.state.chat_requests == []


def test_non_json_model_output_fails_the_job(brain, fake):
    fake.state.script_chat("Sure! Here is the persona you asked for...")
    job_id = brain.post("/personas", json=_seed()).json()["job_id"]
    job = _poll_job(brain, job_id, want="failed")
    assert "JSON" in job["error"]
    assert brain.get("/personas/ada-rez").status_code == 404  # nothing persisted


def test_missing_required_field_fails_the_job(brain, fake):
    incomplete = {k: v for k, v in PERSONA_JSON.items() if k != "who_i_am"}
    fake.state.script_chat(json.dumps(incomplete))
    job_id = brain.post("/personas", json=_seed()).json()["job_id"]
    job = _poll_job(brain, job_id, want="failed")
    assert "who_i_am" in job["error"]


def test_fenced_json_is_parsed(brain, fake):
    fenced = "```json\n" + json.dumps(PERSONA_JSON) + "\n```"
    fake.state.script_chat(fenced)
    job_id = brain.post("/personas", json=_seed()).json()["job_id"]
    _poll_job(brain, job_id)
    assert brain.get("/personas/ada-rez").json()["who_i_am"].startswith("I am Ada Rez")


def test_unknown_job_and_persona_404(brain):
    assert brain.get("/personas/jobs/does-not-exist").status_code == 404
    assert brain.get("/personas/nobody").status_code == 404


def test_list_personas_after_synthesis(brain, fake):
    fake.state.script_chat(json.dumps(PERSONA_JSON))
    job_id = brain.post("/personas", json=_seed()).json()["job_id"]
    _poll_job(brain, job_id)
    listed = brain.get("/personas").json()["personas"]
    assert [p["id"] for p in listed] == ["ada-rez"]
    assert brain.get("/personas", params={"beat": "tech"}).json()["personas"][0]["id"] == "ada-rez"
    assert brain.get("/personas", params={"beat": "world"}).json()["personas"] == []
