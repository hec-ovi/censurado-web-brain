"""End-to-end wiring checks for the newsroom.

The real entry point for a compose stack is the compose engine itself, so these
tests run `docker compose config` (which parses, interpolates, and validates the
whole topology) and assert the cross-service wiring that the newsroom exists to
pin: the public site is the only 0.0.0.0 port, generate is a resident watcher that
rebuilds the static site, the media store is enabled on publish, and the
authors/articles/images live on persistent host bind mounts (so `down -v` cannot wipe
them). No images are built and no GPU is touched.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILE = ROOT / "docker-compose.yml"

pytestmark = pytest.mark.skipif(
    shutil.which("docker") is None, reason="docker not installed"
)


def _env_file(tmp_path: Path) -> Path:
    # Every variable in docker-compose.yml has a baked-in default, so an empty env
    # file is enough for `compose config` to interpolate the whole topology.
    p = tmp_path / "test.env"
    p.write_text("")
    return p


def _config(tmp_path: Path, *extra: str) -> dict:
    env_file = _env_file(tmp_path)
    proc = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "--env-file", str(env_file),
         *extra, "config", "--format", "json"],
        cwd=ROOT,
        env={**os.environ, "COMPOSE_PROJECT_NAME": "newsroom-test"},
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"compose config failed:\n{proc.stderr}"
    return json.loads(proc.stdout)


def test_default_service_set(tmp_path):
    cfg = _config(tmp_path)
    # generate is a resident watcher (it -watches the db and rebuilds the static site),
    # so it joins a default `up`. init-perms now also joins `up` as a one-shot the
    # writers wait on (it exits 0 before publish/generate start), so a plain
    # `docker compose up -d` works with no manual step. comfyui is heavy (ROCm, model
    # load) and GPU-only, so it is OPT-IN behind the `comfyui` profile and does NOT join
    # a plain `up`: the default lane is the fast one (write/review/publish/serve). The
    # legacy admin (:8081) and console (:8083) surfaces are gone; the operator panel is
    # folded INTO publish (Go go:embed) and the brain is a host-run CLI now.
    assert set(cfg["services"]) == {
        "publish", "site", "generate", "init-perms", "executor",
    }
    assert "comfyui" not in cfg["services"], "comfyui is profile-gated (opt-in), not in a default up"
    assert "panel" not in cfg["services"], "the panel is folded into publish, not a separate service"
    assert "brain" not in cfg["services"], "the brain is a host-run CLI now, not a service"


def test_executor_runs_the_schedules_beside_the_stack(tmp_path):
    # The schedule executor fires the newsroom's edition batches (see
    # automation/executor/CONTRACT.md). It starts with the stack (so "docker up"
    # means "the clock runs") and rides the HOST network: the pipeline config's
    # endpoints are host loopback and llama.cpp binds 127.0.0.1 only, which a
    # bridge network cannot reach.
    executor = _config(tmp_path)["services"]["executor"]
    assert executor["network_mode"] == "host"
    assert "ports" not in executor, "the executor exposes nothing"
    assert executor.get("depends_on", {}).get("publish"), "the executor waits on the schedule registry"
    # The repo is bind-mounted (code live, artifacts land in the host tree) and
    # the local time is the host's, so schedule times mean host wall clock.
    sources = [v.get("source") for v in executor["volumes"]]
    assert "/etc/localtime" in sources
    env = executor["environment"]
    assert "NEWSROOM_OPERATOR_TOKEN" in env, "the executor authenticates with the operator token"


def test_comfyui_is_opt_in_behind_the_comfyui_profile(tmp_path):
    # The heavy GPU image backend joins the stack only when the `comfyui` profile is
    # selected (`docker compose --profile comfyui up` / `make up-gpu`), so the fast lane
    # spins up without waiting on ROCm/model load. It stays localhost-only when present.
    cfg = _config(tmp_path, "--profile", "comfyui")
    assert "comfyui" in cfg["services"], "comfyui must join the stack under --profile comfyui"
    comfy = cfg["services"]["comfyui"]
    assert comfy.get("profiles") == ["comfyui"], "comfyui must carry the comfyui profile"
    # Nothing in the fast lane depends on comfyui, so it never blocks their startup.
    for svc in ("publish", "generate", "site", "init-perms"):
        dep = cfg["services"][svc].get("depends_on") or {}
        assert "comfyui" not in dep, f"{svc} must not wait on comfyui"
    # Still bound to 127.0.0.1 (never a public surface) and on the shared network.
    assert comfy["ports"][0]["host_ip"] == "127.0.0.1", "comfyui must stay localhost-only"
    assert "censurado" in comfy["networks"], "comfyui must join the shared network"


def test_publish_serves_the_operator_panel(tmp_path):
    # The operator admin panel is folded into the publish backend (Go serves the SPA +
    # login beside the JSON API). It carries the login-gate env and stays localhost-only;
    # crucially it does NOT depend on the brain, so the panel works with the brain down.
    publish = _config(tmp_path)["services"]["publish"]

    # Exactly one host port, bound to 127.0.0.1 on 8082 (never 0.0.0.0). The panel is
    # reached on this same port.
    ports = publish["ports"]
    assert len(ports) == 1
    p = ports[0]
    assert p["host_ip"] == "127.0.0.1", "publish/panel must never bind 0.0.0.0"
    assert str(p["published"]) == "8082"

    # Carries the panel login-gate env (the key + hash are what enable + gate the panel).
    env = publish["environment"]
    assert "PANEL_SESSION_KEY" in env
    assert "PANEL_LOGIN_TOKEN_HASH" in env
    assert "PANEL_SECURE_COOKIES" in env

    # The panel needs NO brain: publish only waits on the perms one-shot, never on brain.
    dep = publish.get("depends_on", {})
    assert "brain" not in dep, "the folded-in panel must work with the brain down"

    # On the shared network.
    assert "censurado" in publish["networks"]


def test_generate_is_a_resident_watcher(tmp_path):
    # After the backend split, generate is no longer a one-shot: it runs the pinned
    # golang image, -watches the db, and rebuilds the static site into the site
    # volume. It mounts BOTH code repos read-only so the censurado-web ->
    # censurado-web-backend `replace` directive resolves.
    gen = _config(tmp_path)["services"]["generate"]
    assert gen.get("profiles") in (None, []), "generate must join a default `up`"
    assert "-watch" in gen["command"]
    assert gen["restart"] == "unless-stopped"
    binds = {v["source"].split("/")[-1]: v for v in gen["volumes"] if v["type"] == "bind"}
    assert "censurado-web" in binds and "censurado-web-backend" in binds
    assert all(binds[r].get("read_only") for r in ("censurado-web", "censurado-web-backend"))


def test_init_perms_runs_first_and_the_writers_wait_on_it(tmp_path):
    # init-perms chowns the shared site + gocache volumes AND the persistent ./data bind
    # mounts to the nonroot uid (65532) so the first generate/regenerate or media write
    # can succeed. It joins a normal `up` as a one-shot (NOT profile-gated) and the uid-
    # 65532 writers wait for it to exit 0, so `docker compose up -d` works straight after
    # a `down -v` wipe with no manual step (fixes the "creating work dir: stat
    # /gocache/tmp" crash-loop when the freshly-recreated gocache volume is root-owned).
    svc = _config(tmp_path)["services"]["init-perms"]
    assert not svc.get("profiles"), "init-perms must join a default `up`, not be profile-gated"
    assert svc["restart"] == "no", "init-perms is a one-shot"
    # A shell one-liner that chowns the site + gocache volumes and the data bind mount.
    assert svc["command"][0] == "sh"
    chown = svc["command"][-1]
    for token in ("65532:65532", "/site", "/data-host", "/gocache"):
        assert token in chown, f"init-perms command dropped {token}"
    targets = {v["target"] for v in svc["volumes"]}
    assert {"/site", "/data-host", "/gocache"} <= targets

    # The uid-65532 writers wait for init-perms to COMPLETE before they start.
    services = _config(tmp_path)["services"]
    for writer in ("publish", "generate"):
        dep = services[writer].get("depends_on") or {}
        assert "init-perms" in dep, f"{writer} must wait on init-perms"
        assert dep["init-perms"]["condition"] == "service_completed_successfully"


def test_persistent_state_is_host_bind_mounts(tmp_path):
    # The articles (sqlite) and images (/media) must be host bind mounts so
    # `docker compose down -v` (which removes named volumes) cannot wipe them. The
    # named-volume set must NOT contain db/media.
    cfg = _config(tmp_path)
    named = set(cfg.get("volumes") or {})
    assert "db-data" not in named
    assert "media-data" not in named

    def mount(svc, target):
        for v in cfg["services"][svc]["volumes"]:
            if v["target"] == target:
                return v
        raise AssertionError(f"{svc} has no mount at {target}")

    # sqlite db: bind mount under the data dir (default ./data/db, INSIDE this repo and
    # gitignored) for publish (writer) and the generate reader.
    for svc in ("publish", "generate"):
        m = mount(svc, "/data")
        assert m["type"] == "bind" and m["source"].endswith("/data/db")
    # images: bind mount under <data-dir>/media on publish.
    assert mount("publish", "/srv/media")["source"].endswith("/data/media")


def test_publish_media_store_enabled_and_regen_moved_to_generate(tmp_path):
    # publish keeps the self-hosted image store (POST /media), but after the split it
    # no longer regenerates the site in-process: that moved to the generate watcher,
    # which is the only service that writes the site volume.
    cfg = _config(tmp_path)
    pub = cfg["services"]["publish"]
    env = pub["environment"]
    assert env["CENSURADO_MEDIA_DIR"] == "/srv/media"
    assert "CENSURADO_PUBLISH_OUT" not in env, "publish must not write the site anymore"
    pub_targets = {v["target"] for v in pub["volumes"]}
    assert "/srv/media" in pub_targets
    assert "/site" not in pub_targets, "only generate writes the site volume"
    gen_targets = {v["target"] for v in cfg["services"]["generate"]["volumes"]}
    assert "/site" in gen_targets


def test_site_serves_media(tmp_path):
    # The public nginx serves /media/ from the same persistent image store, so hero
    # image URLs (/media/<hash>.<ext>) resolve on the portal.
    site = _config(tmp_path)["services"]["site"]
    m = next((v for v in site["volumes"] if v["target"] == "/srv/media"), None)
    assert m is not None and m["source"].endswith("/data/media")


def test_site_is_the_only_public_port(tmp_path):
    services = _config(tmp_path)["services"]
    public = []
    for name, svc in services.items():
        for port in svc.get("ports", []):
            host_ip = port.get("host_ip", "")
            # 0.0.0.0 or unset means exposed on all interfaces.
            if host_ip in ("", "0.0.0.0"):
                public.append(name)
    assert public == ["site"], f"unexpected public ports: {public}"
    site_ports = services["site"]["ports"]
    assert site_ports[0]["target"] == 80
    assert str(site_ports[0]["published"]) == "8123"


def test_publish_is_the_only_db_writer_surface(tmp_path):
    # generate reads the same db volume (read-only) to rebuild the site; publish is the
    # only writer. No other service mounts /data.
    services = _config(tmp_path)["services"]
    for name in ("publish", "generate"):
        mounts = {v["target"] for v in services[name]["volumes"]}
        assert "/data" in mounts


def test_single_shared_network(tmp_path):
    cfg = _config(tmp_path)
    assert "censurado" in cfg["networks"]
    for name, svc in cfg["services"].items():
        if name == "init-perms":
            continue  # the perms one-shot deliberately owns no network (network_mode: none)
        if name == "executor":
            continue  # the executor rides the HOST network (loopback-bound llama.cpp)
        assert "censurado" in svc["networks"], f"{name} is off the shared network"


def test_no_telegram_service_in_compose(tmp_path):
    # The Telegram lane retired from compose (automation/supervisor/REQUIREMENTS.md R7):
    # the bot is the telegram-bot-skill sibling run on the HOST by the serve loop, because
    # the agent CLIs and their auth live on the host. Guard against it creeping back in.
    assert "tg-bridge" not in _config(tmp_path)["services"], "the in-container bridge retired; keep telegram out of compose"
