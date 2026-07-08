"""End-to-end tests for the Telegram -> headless agent bridge (router v2).

Drives the real entry points (Bridge.process, run_agent, the admin HTTP server, the
live ConfigStore) with a fake Telegram client and REAL agent subprocesses. Covers: the
default-deny allowlist, agent selection (codex/agy) and switching, the Telegram-mode
preamble, robust error catching (not installed / nonzero exit / auth hint), token
isolation, and the admin panel (auth + state + add/remove/switch). Compose hardening is
pinned separately.
"""

import importlib.util
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRIDGE = ROOT / "bridge" / "telegram"


def _load_router():
    spec = importlib.util.spec_from_file_location("tg_router", BRIDGE / "router.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


router = _load_router()


class FakeTG:
    def __init__(self):
        self.sent = []
        self.typing = []

    def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))

    def send_typing(self, chat_id):
        self.typing.append(chat_id)

    def get_me(self):
        return {"ok": True, "result": {"username": "ElCensuradoWeb_bot"}}


def _agent(tmp_path, name, body):
    p = tmp_path / name
    p.write_text("import sys, os\n" + body + "\n")
    return p


def _cfg(tmp_path, *, codex="echo {msg}", agy="echo {msg}", allowed=(1,), preamble="",
         admin_token="atok", port=0, timeout=60):
    return router.Config(
        token="btok",
        admin_token=admin_token,
        repo_dir=str(tmp_path),
        data_dir=tmp_path / "data",
        timeout=timeout,
        typing_interval=0.02,
        admin_host="127.0.0.1",
        admin_port=port,
        preamble=preamble,
        default_agent="codex",
        seed_allowed=list(allowed),
        agents={"codex": {"label": "Codex", "cmd": codex},
                "agy": {"label": "agy", "cmd": agy}},
    )


def _store(cfg):
    return router.ConfigStore(cfg.data_dir, cfg.default_agent, cfg.seed_allowed)


def _bridge(cfg, store=None):
    store = store or _store(cfg)
    return router.Bridge(cfg, store, tg=FakeTG(), sessions=router.SessionStore(cfg.data_dir))


# --------------------------------------------------------------- pure helpers
def test_chunk_text():
    assert router.chunk_text("hola") == ["hola"]
    assert router.chunk_text("") == []
    pieces = router.chunk_text("a" * 5000)
    assert len(pieces) == 2 and "".join(pieces) == "a" * 5000


def test_build_invocation_repo_token():
    argv, stdin = router.build_invocation(
        "agy --add-dir {repo} -p {msg}", "SID", "/w", "/therepo", "hi there")
    assert argv == ["agy", "--add-dir", "/therepo", "-p", "hi there"]
    assert stdin is None
    argv2, stdin2 = router.build_invocation("cli -r {session}", "SID", "/w", "/r", "hi")
    assert argv2 == ["cli", "-r", "SID"] and stdin2 == "hi"


# --------------------------------------------------------------- live config store
def test_configstore_seed_edit_persist(tmp_path):
    cfg = _cfg(tmp_path, allowed=(5,))
    s = _store(cfg)
    assert s.agent() == "codex"
    assert s.allowed() == [5]
    s.add(9)
    s.add(5)  # idempotent
    s.remove(5)
    s.set_agent("agy")
    # reload from disk -> persisted
    s2 = router.ConfigStore(cfg.data_dir, "codex", [])
    assert s2.agent() == "agy"
    assert s2.allowed() == [9]


def test_configstore_rejects_unknown_agent(tmp_path):
    s = _store(_cfg(tmp_path))
    try:
        s.set_agent("bogus")
        assert False, "should have raised"
    except ValueError:
        pass


# --------------------------------------------------------------- core message path
def test_denies_unlisted_sender(tmp_path):
    marker = tmp_path / "ran.flag"
    ag = _agent(tmp_path, "mark.py", f"open(r'{marker}','w').write('x')")
    br = _bridge(_cfg(tmp_path, codex=f"{sys.executable} {ag}", allowed=(1,)))
    assert br.process(chat_id=9, user_id=999, text="hi") is False
    assert not marker.exists()
    assert br.tg.sent == []


def test_runs_selected_agent_with_preamble(tmp_path):
    ag = _agent(tmp_path, "echo.py", "sys.stdout.write(sys.stdin.read())")
    br = _bridge(_cfg(tmp_path, codex=f"{sys.executable} {ag}", allowed=(1,), preamble="PRE"))
    assert br.process(chat_id=7, user_id=1, text="hola") is True
    assert br.tg.sent == [(7, "PRE\n\nhola")]
    assert br.tg.typing


def test_default_preamble_is_spanish_and_maps_publicar_to_production():
    # The bot must default to Spanish, act without asking, and treat the operator's
    # "publicá/publicalo" as going live via `publicar` (push to the public site), never local preview.
    p = router.DEFAULT_PREAMBLE.lower()
    assert "español" in p                            # Spanish by default
    assert "publicá" in p                            # maps the operator's "publicá" to going live
    assert "cli/censurado.py publicar" in p          # names the exact live-push (production) command
    assert "cli/censurado.py deploy" not in p        # the old confusing "deploy" verb is gone from the surface
    assert "sin pedir confirmación" in p             # act directly, no questioning


def test_from_env_includes_claude_opus_agent(tmp_path):
    # Full-capacity news uses the claude CLI on Opus; it must be a selectable agent.
    cfg = router.Config.from_env({"TELEGRAM_BOT_TOKEN": "btok", "DATA_DIR": str(tmp_path)})
    assert set(cfg.agents) >= {"claude", "codex", "agy"}
    cmd = cfg.agents["claude"]["cmd"]
    assert "claude -p" in cmd and "--model opus" in cmd and "{msg}" in cmd
    store = router.ConfigStore(cfg.data_dir, cfg.default_agent, cfg.seed_allowed)
    store.set_agent("claude")
    assert store.agent() == "claude"


def test_agent_switch_changes_command(tmp_path):
    ag = _agent(tmp_path, "which.py", "sys.stdout.write(sys.argv[1])")
    cfg = _cfg(tmp_path, codex=f"{sys.executable} {ag} CODEX {{msg}}",
               agy=f"{sys.executable} {ag} AGY {{msg}}", allowed=(1,))
    store = _store(cfg)
    br = _bridge(cfg, store)
    br.process(chat_id=7, user_id=1, text="x")
    store.set_agent("agy")
    br.process(chat_id=7, user_id=1, text="x")
    assert [t for _c, t in br.tg.sent] == ["CODEX", "AGY"]


def test_message_is_argv_not_shell(tmp_path):
    ag = _agent(tmp_path, "argv.py", "sys.stdout.write(sys.argv[1])")
    br = _bridge(_cfg(tmp_path, codex=f"{sys.executable} {ag} {{msg}}", allowed=(1,)))
    payload = "hi; rm -rf / && echo $(whoami)"
    br.process(chat_id=7, user_id=1, text=payload)
    assert br.tg.sent == [(7, payload)]


def test_token_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "btok")
    monkeypatch.setenv("TG_ADMIN_TOKEN", "atok")
    monkeypatch.setenv("NEWSROOM_OPERATOR_TOKEN", "optok")
    ag = _agent(tmp_path, "env.py",
                "sys.stdout.write('BOT=%s ADMIN=%s OP=%s' % ("
                "'TELEGRAM_BOT_TOKEN' in os.environ, 'TG_ADMIN_TOKEN' in os.environ, "
                "os.environ.get('NEWSROOM_OPERATOR_TOKEN','')))")
    br = _bridge(_cfg(tmp_path, codex=f"{sys.executable} {ag}", allowed=(1,)))
    br.process(chat_id=7, user_id=1, text="go")
    assert br.tg.sent[0][1] == "BOT=False ADMIN=False OP=optok"


def test_per_chat_censurado_work(tmp_path):
    ag = _agent(tmp_path, "work.py",
                "sys.stdout.write(os.environ.get('CENSURADO_WORK',''))")
    cfg = _cfg(tmp_path, codex=f"{sys.executable} {ag}", allowed=(1,))
    br = _bridge(cfg)
    br.process(chat_id=7, user_id=1, text="go")
    assert br.tg.sent[0][1] == str(cfg.data_dir / "chats" / "7")


# --------------------------------------------------------------- error catching
def test_agent_not_installed(tmp_path):
    cfg = _cfg(tmp_path, codex="definitely-not-a-real-binary-xyz {msg}", allowed=(1,))
    st = router.Status()
    rec = {"session": "s", "workdir": str(tmp_path)}
    reply, ok = router.run_agent(cfg, "codex", rec, "hi", st)
    assert ok is False
    assert "not installed" in reply
    assert st.snapshot()["last_error"]


def test_agent_nonzero_exit_reports_stderr(tmp_path):
    ag = _agent(tmp_path, "boom.py", "sys.stderr.write('kaboom detail'); sys.exit(3)")
    cfg = _cfg(tmp_path, codex=f"{sys.executable} {ag}", allowed=(1,))
    reply, ok = router.run_agent(cfg, "codex", {"session": "s", "workdir": str(tmp_path)},
                                 "hi", router.Status())
    assert ok is False
    assert "exit 3" in reply and "kaboom" in reply


def test_agent_auth_hint(tmp_path):
    ag = _agent(tmp_path, "auth.py", "sys.stderr.write('Error: not logged in'); sys.exit(1)")
    cfg = _cfg(tmp_path, codex=f"{sys.executable} {ag}", allowed=(1,))
    reply, ok = router.run_agent(cfg, "codex", {"session": "s", "workdir": str(tmp_path)},
                                 "hi", router.Status())
    assert ok is False
    assert "logged out" in reply.lower() or "unauthorized" in reply.lower()


# --------------------------------------------------------------- admin panel
def _admin(cfg, store, bot=None):
    status = router.Status()
    bot = bot or {"username": "ElCensuradoWeb_bot", "link": "https://t.me/ElCensuradoWeb_bot"}
    srv = router.start_admin(cfg, store, status, bot)
    port = srv.server_address[1]
    return srv, port


def _req(port, path, token=None, method="GET", body=None):
    headers = {"X-Admin-Token": token} if token else {}
    data = json.dumps(body).encode() if body is not None else None
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data,
                                 headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as ex:
        return ex.code, ex.read().decode()


def test_admin_requires_token(tmp_path):
    cfg = _cfg(tmp_path, admin_token="secret")
    srv, port = _admin(cfg, _store(cfg))
    try:
        code, _ = _req(port, "/")
        assert code == 401
        code2, body = _req(port, "/", token="secret")
        assert code2 == 200 and "Telegram bridge" in body
    finally:
        srv.shutdown()


def test_admin_state_and_edits(tmp_path):
    cfg = _cfg(tmp_path, allowed=(1,), admin_token="secret")
    store = _store(cfg)
    srv, port = _admin(cfg, store)
    try:
        code, body = _req(port, "/api/state", token="secret")
        assert code == 200
        st = json.loads(body)
        assert st["agent"] == "codex" and st["allowed_ids"] == [1]
        assert set(st["agents"]) == {"codex", "agy"}

        # add / remove / switch, all reflected in the store
        _req(port, "/api/allow", token="secret", method="POST", body={"id": 42})
        assert 42 in store.allowed()
        _req(port, "/api/deny", token="secret", method="POST", body={"id": 1})
        assert 1 not in store.allowed()
        _req(port, "/api/agent", token="secret", method="POST", body={"agent": "agy"})
        assert store.agent() == "agy"

        # auth enforced on writes
        code3, _ = _req(port, "/api/allow", method="POST", body={"id": 99})
        assert code3 == 401 and 99 not in store.allowed()
    finally:
        srv.shutdown()


def test_admin_rejects_bad_agent(tmp_path):
    cfg = _cfg(tmp_path, admin_token="secret")
    store = _store(cfg)
    srv, port = _admin(cfg, store)
    try:
        code, _ = _req(port, "/api/agent", token="secret", method="POST",
                       body={"agent": "bogus"})
        assert code == 400 and store.agent() == "codex"
    finally:
        srv.shutdown()


def test_qr_svg_degrades_or_renders():
    assert router.qr_svg("") == ""
    out = router.qr_svg("https://t.me/ElCensuradoWeb_bot")
    assert out == "" or out.lstrip().startswith("<svg")
