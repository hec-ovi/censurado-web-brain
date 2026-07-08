#!/usr/bin/env python3
"""Telegram -> headless agentic CLI bridge (stdlib only + optional segno for QR).

A production-minded bridge: a Telegram bot fronts a headless agent CLI. It receives a
message from an allow-listed user, runs the selected agent (codex or agy) as a plain
subprocess, and sends the reply back. It NEVER manages model auth: codex (OpenAI /
ChatGPT) and agy (Google Gemini) are expected to be logged in already in the
environment; the bridge just runs the CLI and reports clearly when it fails.

Highlights:
- Two agents, switchable at runtime: codex and agy. The active one is stored in a
  JSON config on disk and picked per message.
- A tiny admin panel (localhost, token-gated) shows the bot name + a QR to its t.me
  link, lets you flip the agent, and add/remove allow-listed Telegram user ids live.
- Default-deny allowlist, per-chat session + scratch dir, per-user serialization,
  chunking to Telegram's 4096 limit, a re-armed typing indicator, and robust error
  catching (not installed / logged out / timeout / nonzero exit) surfaced to both the
  chat and the panel.

Config is from the environment (see .env.example); the live agent + allowlist live in
DATA_DIR/config.json so the panel can edit them without a restart.
"""

from __future__ import annotations

import html
import http.server
import json
import logging
import os
import queue
import shlex
import signal
import socketserver
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("tg-bridge")

TG_TEXT_LIMIT = 4000
# Config/secrets that must never leak into the agent subprocess environment.
SECRET_ENV = {
    "TELEGRAM_BOT_TOKEN",
    "TG_ADMIN_TOKEN",
    "TG_ALLOWED_IDS",
    "AGENT_SYSTEM",
    "AGENT_CMD_CODEX",
    "AGENT_CMD_AGY",
    "AGENT_CMD_CLAUDE",
    "TG_API_BASE",
}

# The supported agents. Auth is external; we only run the CLI. Command templates are
# env-overridable but ship with working defaults. Tokens: {repo} {workdir} {session} {msg}.
BUILTIN_AGENTS = {
    "claude": {
        "label": "Claude (Opus, full capacity)",
        "default_cmd": "claude -p {msg} --model opus "
        "--add-dir {repo} --dangerously-skip-permissions",
    },
    "codex": {
        "label": "Codex (OpenAI ChatGPT)",
        "default_cmd": "codex exec -c model_reasoning_effort=low "
        "--dangerously-bypass-approvals-and-sandbox {msg}",
    },
    "agy": {
        "label": "agy (Google Gemini)",
        "default_cmd": "agy --add-dir {repo} --dangerously-skip-permissions -p {msg}",
    },
}

# Prepended to every message. Practical Telegram framing (shape by content, not a length
# cap) plus the newsroom operator guardrail. Override wholesale with AGENT_SYSTEM.
DEFAULT_PREAMBLE = (
    "Respondé siempre en español rioplatense (voseo), el registro del portal Censurado, "
    "aunque te escriban en otro idioma. Estás contestando dentro de un chat de Telegram en "
    "un celular: andá directo a la respuesta, sin saludos, preámbulos, despedidas ni "
    "relleno, y formateá en párrafos y viñetas que se lean bien en el celular. "
    "Operá el portal Censurado solo a través del skill censurado (python3 cli/censurado.py "
    "<verbo>), recorriendo el flujo editorial gateado paso a paso. Actuá directo: hacé lo "
    "que te piden sin pedir confirmación ni devolver preguntas. "
    "Cuando el operador diga «publicá», «publicalo», «publicalas», «subilo» o «mandalo al "
    "sitio», querés dejarlo EN VIVO en el sitio público: terminá el walk y corré "
    "`python3 cli/censurado.py publicar --yes`, que publica el snapshot al sitio público. "
    "«publicar» es SIEMPRE producción (el sitio público); «preview» es solo local (debug) y "
    "nunca sale a internet. Decile al operador «lo publiqué en el sitio»; no uses la palabra "
    "en inglés «deploy». Sin ese pedido, quedate en la vista previa local. "
    "Si un comando falla, decí en una línea qué falló."
)

# Substrings in agent output that signal an auth problem (best-effort, for a clearer reply).
AUTH_HINTS = ("not logged in", "unauthorized", "401", "authenticate", "login",
              "api key", "credential", "expired", "forbidden", "403")


# --------------------------------------------------------------------------- config
@dataclass
class Config:
    token: str
    admin_token: str
    repo_dir: str = "/repo"
    data_dir: Path = field(default_factory=lambda: Path("/data"))
    timeout: int = 1800
    api_base: str = "https://api.telegram.org"
    typing_interval: float = 4.0
    # A long agent turn (writing + publishing an article can take minutes) shows a typing dot,
    # which is not reassuring on its own. After progress_after seconds with no reply we post a
    # "still working" note and refresh it every progress_interval seconds (edited in place).
    progress_after: float = 45.0
    progress_interval: float = 30.0
    max_queue: int = 20
    admin_host: str = "127.0.0.1"
    admin_port: int = 8090
    preamble: str = DEFAULT_PREAMBLE
    default_agent: str = "codex"
    seed_allowed: list[int] = field(default_factory=list)
    agents: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls, env: dict | None = None) -> "Config":
        e = os.environ if env is None else env
        token = (e.get("TELEGRAM_BOT_TOKEN") or "").strip()
        if not token:
            raise SystemExit("FATAL: TELEGRAM_BOT_TOKEN is required")
        agents = {
            key: {"label": spec["label"],
                  "cmd": (e.get(f"AGENT_CMD_{key.upper()}") or "").strip() or spec["default_cmd"]}
            for key, spec in BUILTIN_AGENTS.items()
        }
        default_agent = (e.get("DEFAULT_AGENT") or "codex").strip().lower()
        if default_agent not in agents:
            default_agent = "codex"
        raw_ids = (e.get("TG_ALLOWED_IDS") or "").strip()
        seed = [int(x) for x in raw_ids.replace(";", ",").split(",") if x.strip()]
        return cls(
            token=token,
            admin_token=(e.get("TG_ADMIN_TOKEN") or "").strip(),
            repo_dir=(e.get("REPO_DIR") or "/repo").strip(),
            data_dir=Path(e.get("DATA_DIR") or "/data"),
            timeout=int(e.get("AGENT_TIMEOUT") or "1800"),
            api_base=(e.get("TG_API_BASE") or "https://api.telegram.org").rstrip("/"),
            typing_interval=float(e.get("TG_TYPING_INTERVAL") or "4"),
            admin_host=(e.get("TG_ADMIN_HOST") or "127.0.0.1").strip(),
            admin_port=int(e.get("TG_PANEL_PORT") or "8090"),
            preamble=(e.get("AGENT_SYSTEM") or DEFAULT_PREAMBLE),
            default_agent=default_agent,
            seed_allowed=seed,
            agents=agents,
        )


# ---------------------------------------------------------------- live config store
class ConfigStore:
    """Runtime-editable {agent, allowed_ids} persisted to DATA_DIR/config.json."""

    def __init__(self, data_dir: Path, default_agent: str, seed_allowed: list[int]):
        self._dir = data_dir
        self._file = data_dir / "config.json"
        self._lock = threading.RLock()
        data_dir.mkdir(parents=True, exist_ok=True)
        if self._file.exists():
            self._data = json.loads(self._file.read_text())
        else:
            self._data = {"agent": default_agent, "allowed_ids": sorted(set(seed_allowed))}
            self._save()

    def _save(self):
        tmp = self._file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2))
        tmp.replace(self._file)

    def agent(self) -> str:
        with self._lock:
            return self._data.get("agent", "codex")

    def set_agent(self, key: str) -> None:
        with self._lock:
            if key not in BUILTIN_AGENTS:
                raise ValueError(f"unknown agent {key!r}")
            self._data["agent"] = key
            self._save()

    def allowed(self) -> list[int]:
        with self._lock:
            return list(self._data.get("allowed_ids", []))

    def add(self, uid: int) -> None:
        with self._lock:
            ids = set(self._data.get("allowed_ids", []))
            ids.add(int(uid))
            self._data["allowed_ids"] = sorted(ids)
            self._save()

    def remove(self, uid: int) -> None:
        with self._lock:
            ids = set(self._data.get("allowed_ids", []))
            ids.discard(int(uid))
            self._data["allowed_ids"] = sorted(ids)
            self._save()


class Status:
    """Thread-safe last-activity snapshot for the admin panel."""

    def __init__(self):
        self._lock = threading.Lock()
        self.last_agent = ""
        self.last_error = ""
        self.last_ok_ts = 0.0
        self.last_error_ts = 0.0
        self.handled = 0

    def ok(self, agent: str):
        with self._lock:
            self.last_agent, self.last_error, self.handled = agent, "", self.handled + 1
            self.last_ok_ts = _now()

    def fail(self, agent: str, msg: str):
        with self._lock:
            self.last_agent, self.last_error = agent, msg
            self.last_error_ts = _now()

    def snapshot(self) -> dict:
        with self._lock:
            return {"last_agent": self.last_agent, "last_error": self.last_error,
                    "last_ok_ts": self.last_ok_ts, "last_error_ts": self.last_error_ts,
                    "handled": self.handled}


def _now() -> float:
    return time.time()


# ------------------------------------------------------------------------ pure utils
def chunk_text(text: str, limit: int = TG_TEXT_LIMIT) -> list[str]:
    text = text.rstrip("\n")
    if not text:
        return []
    pieces, buf = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            if buf:
                pieces.append(buf)
                buf = ""
            pieces.append(line[:limit])
            line = line[limit:]
        add = line if not buf else buf + "\n" + line
        if len(add) > limit:
            pieces.append(buf)
            buf = line
        else:
            buf = add
    if buf:
        pieces.append(buf)
    return pieces


def build_invocation(template: str, session: str, workdir: str, repo: str,
                     prompt: str) -> tuple[list[str], str | None]:
    """Template -> (argv, stdin). {msg} present -> prompt as an argv element, else stdin.
    Parsed with shlex (no shell), so message text can never inject arguments."""
    argv, msg_in_argv = [], False
    for tok in shlex.split(template):
        tok = (tok.replace("{session}", session).replace("{workdir}", workdir)
               .replace("{repo}", repo))
        if "{msg}" in tok:
            tok = tok.replace("{msg}", prompt)
            msg_in_argv = True
        argv.append(tok)
    return argv, (None if msg_in_argv else prompt)


def classify_failure(agent: str, rc: int, stderr: str) -> str:
    low = stderr.lower()
    if any(h in low for h in AUTH_HINTS):
        return (f"{agent} looks logged out or unauthorized. Re-auth it where the bridge "
                f"runs (auth is set up outside this bridge).")
    return f"{agent} failed (exit {rc}). {stderr.strip()[-300:]}".strip()


# -------------------------------------------------------------------- telegram client
class Telegram:
    def __init__(self, token: str, api_base: str = "https://api.telegram.org"):
        self._url = f"{api_base}/bot{token}"

    def call(self, method: str, params: dict, timeout: float = 65) -> dict:
        data = json.dumps(params).encode()
        req = urllib.request.Request(f"{self._url}/{method}", data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())

    def get_me(self) -> dict:
        return self.call("getMe", {}, timeout=15)

    def get_updates(self, offset: int, poll: int = 50) -> list[dict]:
        try:
            res = self.call("getUpdates",
                            {"offset": offset, "timeout": poll, "allowed_updates": ["message"]},
                            timeout=poll + 15)
        except urllib.error.HTTPError as ex:
            log.error("getUpdates HTTP %s: %s", ex.code, ex.read().decode()[:200])
            time.sleep(3)
            return []
        except (urllib.error.URLError, TimeoutError, OSError) as ex:
            log.warning("getUpdates transient error: %s", ex)
            time.sleep(2)
            return []
        return res.get("result", []) if res.get("ok") else []

    def send_message(self, chat_id: int, text: str) -> int | None:
        """Send text; return the new message_id (so a caller can edit it in place) or None."""
        try:
            res = self.call("sendMessage",
                            {"chat_id": chat_id, "text": text, "disable_web_page_preview": True})
        except urllib.error.HTTPError as ex:
            if ex.code == 429:
                retry = _retry_after(ex)
                log.warning("429 sendMessage, sleeping %ss", retry)
                time.sleep(retry)
                return self.send_message(chat_id, text)
            log.error("sendMessage HTTP %s", ex.code)
            return None
        except (urllib.error.URLError, OSError) as ex:
            log.error("sendMessage failed: %s", ex)
            return None
        if not res.get("ok"):
            log.error("sendMessage rejected: %s", res.get("description"))
            return None
        return (res.get("result") or {}).get("message_id")

    def edit_message_text(self, chat_id: int, message_id: int, text: str) -> None:
        """Rewrite an existing message in place (used for the long-run progress note)."""
        if not message_id:
            return
        try:
            self.call("editMessageText",
                      {"chat_id": chat_id, "message_id": message_id, "text": text,
                       "disable_web_page_preview": True})
        except Exception as ex:
            log.debug("editMessageText failed: %s", ex)

    def send_typing(self, chat_id: int) -> None:
        try:
            self.call("sendChatAction", {"chat_id": chat_id, "action": "typing"})
        except Exception as ex:
            log.debug("sendChatAction failed: %s", ex)


def _retry_after(ex: urllib.error.HTTPError) -> int:
    try:
        return int(json.loads(ex.read().decode()).get("parameters", {}).get("retry_after", 3))
    except Exception:
        return 3


# --------------------------------------------------------------------- session store
class SessionStore:
    def __init__(self, data_dir: Path):
        self._dir = data_dir
        self._file = data_dir / "sessions.json"
        self._lock = threading.Lock()
        self._map: dict[str, dict] = {}
        if self._file.exists():
            try:
                self._map = json.loads(self._file.read_text())
            except Exception:
                self._map = {}

    def get_or_create(self, chat_id: int) -> tuple[dict, bool]:
        key = str(chat_id)
        with self._lock:
            rec = self._map.get(key)
            if rec:
                return rec, False
            workdir = self._dir / "chats" / key
            workdir.mkdir(parents=True, exist_ok=True)
            rec = {"session": str(uuid.uuid4()), "workdir": str(workdir)}
            self._map[key] = rec
            self._save()
            return rec, True

    def _save(self):
        tmp = self._file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._map, indent=2))
        tmp.replace(self._file)


# --------------------------------------------------------------------------- agent
def run_agent(cfg: Config, agent_key: str, rec: dict, text: str,
              status: Status) -> tuple[str, bool]:
    """Run one turn of the selected agent. Returns (reply_text, ok)."""
    spec = cfg.agents.get(agent_key)
    if not spec:
        status.fail(agent_key, "unknown agent")
        return (f"Agent {agent_key!r} is not configured.", False)

    workdir = rec["workdir"]
    prompt = f"{cfg.preamble}\n\n{text}".strip() if cfg.preamble else text
    argv, stdin = build_invocation(spec["cmd"], rec["session"], workdir, cfg.repo_dir, prompt)

    child_env = {k: v for k, v in os.environ.items() if k not in SECRET_ENV}
    child_env["CENSURADO_WORK"] = workdir  # per-chat scratch for the newsroom write walk

    try:
        proc = subprocess.run(
            argv, input=(stdin.encode() if stdin is not None else None),
            cwd=cfg.repo_dir, env=child_env, capture_output=True, timeout=cfg.timeout)
    except subprocess.TimeoutExpired:
        msg = f"{agent_key} timed out after {cfg.timeout}s."
        status.fail(agent_key, msg)
        return (f"The agent timed out ({cfg.timeout}s). Try a shorter request.", False)
    except FileNotFoundError:
        msg = f"{agent_key} CLI not found ({argv[0]!r}). Install it where the bridge runs."
        log.error(msg)
        status.fail(agent_key, msg)
        return (f"{agent_key} is not installed in this environment.", False)
    except OSError as ex:
        status.fail(agent_key, f"{agent_key} OSError: {ex}")
        return (f"Could not start {agent_key}: {ex}", False)

    out = proc.stdout.decode(errors="replace").strip()
    if proc.returncode != 0 and not out:
        detail = classify_failure(agent_key, proc.returncode, proc.stderr.decode(errors="replace"))
        log.error("agent failure: %s", detail)
        status.fail(agent_key, detail)
        return (f"⚠️ {detail}", False)
    status.ok(agent_key)
    return (out or "(the agent produced no output)", True)


# ---------------------------------------------------------------- structured shortcuts
# Guided, turn-by-turn commands layered ON TOP OF the freeform agent (any non-command message
# still goes straight to the agent). Each command collects a few fields one question at a time,
# then hands the agent a FULLY-SPECIFIED task that names the exact censurado.py verbs to run, so
# the operator fills slots instead of freeforming and the injection surface stays small. The copy
# is Spanish/voseo, the portal's register. Article commands go straight to production (publicar),
# per the rule that a piece created from Telegram is published live, not left on the local preview.

def _step(slot: str, prompt: str) -> dict:
    return {"slot": slot, "prompt": prompt}


def _task_crear_autor(f: dict) -> str:
    return (
        "TAREA (funcion guiada, no converses): crea UN autor nuevo en el backend.\n"
        f"- Nombre / como firma: {f['nombre']}\n"
        f"- Beat / tema principal: {f['tema']}\n"
        f"- Quien es (voz, mirada): {f['quien']}\n"
        f"- Rasgos de estilo: {f['estilo']}\n"
        "Sintetiza la persona siguiendo `prompt persona/synthesize.md` y creala con "
        "`python3 cli/censurado.py create-author --file -` (pasa el JSON por stdin). Al terminar "
        "confirma en una linea el handle creado. No publiques ni escribas notas."
    )


def _task_modificar_autor(f: dict) -> str:
    return (
        "TAREA (funcion guiada, no converses): modifica UN campo de un autor existente.\n"
        f"- Autor (handle): {f['handle']}\n"
        f"- Campo a cambiar: {f['campo']}\n"
        f"- Nuevo valor: {f['valor']}\n"
        "Lee el autor con `persona <handle>`, cambia SOLO ese campo y guarda el registro completo "
        "con `create-author --file -` (es un upsert por handle). Para fuentes usa `sources --set`, "
        "para temas de perfil usa `profile-topics --set`. Confirma el cambio en una linea, nada mas."
    )


def _task_nota(f: dict) -> str:
    return (
        "TAREA (funcion guiada): escribi UNA sola nota y publicala.\n"
        f"- Autor que firma (handle): {f['autor']}\n"
        f"- Tema / angulo / link: {f['tema']}\n"
        "Recorre el walk `step --mode single-article` paso a paso (respeta los gates de fuentes, "
        "titular honesto y evaluacion), previsualiza con `preview`, y cuando quede lista PUBLICALA a "
        "produccion con `python3 cli/censurado.py publicar --yes` (desde Telegram las notas van "
        "directo al sitio publico). Devolve el link final."
    )


def _task_editar_nota(f: dict) -> str:
    return (
        "TAREA (funcion guiada): edita el CONTENIDO de una nota existente y republica.\n"
        f"- Nota (slug o link): {f['slug']}\n"
        f"- Cambio pedido: {f['cambio']}\n"
        "Aplica el cambio con `python3 cli/censurado.py edit <slug>` (--body-file para el cuerpo, "
        "--meta / --set para metadatos), verifica con `get <slug>`, y publica a produccion con "
        "`publicar --yes`. Confirma el link."
    )


COMMANDS = {
    "crear_autor": {
        "aliases": ("/crear_autor", "/crearautor", "/nuevo_autor", "/nuevoautor"),
        "title": "crear autor",
        "steps": [
            _step("nombre", "¿Como se llama / como firma el autor?"),
            _step("tema", "¿Cual es su beat o tema principal?"),
            _step("quien", "Describi quien es: voz, tono, mirada. Una o dos frases."),
            _step("estilo", "¿Algun rasgo de estilo o lexico particular? (o escribi «ninguno»)"),
        ],
        "build": _task_crear_autor,
    },
    "modificar_autor": {
        "aliases": ("/modificar_autor", "/modificarautor", "/editar_autor", "/editarautor"),
        "title": "modificar autor",
        "steps": [
            _step("handle", "¿Que autor queres modificar? Escribi su handle (o /autores para ver la lista)."),
            _step("campo", "¿Que campo? Opciones: nombre, bio, about, estilo, tema, avatar, fuentes, temas-perfil."),
            _step("valor", "¿Cual es el nuevo valor?"),
        ],
        "build": _task_modificar_autor,
    },
    "nota": {
        "aliases": ("/nota", "/nueva_nota", "/nuevanota", "/articulo"),
        "title": "nueva nota",
        "steps": [
            _step("autor", "¿Que autor firma la nota? (handle)"),
            _step("tema", "¿Sobre que? Pega el link, el tema o el angulo."),
        ],
        "build": _task_nota,
    },
    "editar_nota": {
        "aliases": ("/editar_nota", "/editarnota", "/corregir_nota", "/corregir"),
        "title": "editar nota",
        "steps": [
            _step("slug", "¿Que nota queres editar? Pega su slug o su link."),
            _step("cambio", "¿Que cambio de contenido? Describilo o pega el texto nuevo."),
        ],
        "build": _task_editar_nota,
    },
}

# messages handled inline (no wizard, no agent)
HELP_ALIASES = ("/comandos", "/ayuda", "/help", "/start")
CANCEL_ALIASES = ("/cancelar", "/cancel")
AUTHORS_ALIASES = ("/autores", "/authors")


def command_for(text: str) -> str | None:
    """The command key if the message opens a structured shortcut, else None."""
    parts = text.strip().split()
    head = parts[0].lower() if parts else ""
    for key, spec in COMMANDS.items():
        if head in spec["aliases"]:
            return key
    return None


def commands_help() -> str:
    lines = ["Comandos guiados (te pregunto los datos uno por uno):"]
    for spec in COMMANDS.values():
        lines.append(f"  {spec['aliases'][0]} — {spec['title']}")
    lines.append("  /autores — listar autores")
    lines.append("  /cancelar — cancelar el comando en curso")
    lines.append("Cualquier otro mensaje se lo paso directo al agente.")
    return "\n".join(lines)


# ------------------------------------------------------------------------- the bridge
class Bridge:
    def __init__(self, cfg: Config, store: ConfigStore, status: Status | None = None,
                 tg: Telegram | None = None, sessions: SessionStore | None = None):
        self.cfg = cfg
        self.store = store
        self.status = status or Status()
        self.tg = tg or Telegram(cfg.token, cfg.api_base)
        self.sessions = sessions or SessionStore(cfg.data_dir)
        self._queues: dict[int, queue.Queue] = {}
        self._qlock = threading.Lock()
        self._wizards: dict[int, dict] = {}  # chat_id -> {"cmd", "step", "fields"} for a guided command
        self._wlock = threading.Lock()
        self.bot = {"username": "", "link": ""}

    def process(self, chat_id: int, user_id: int, text: str) -> bool:
        if user_id not in self.store.allowed():
            log.warning("DENY message from %s (chat %s): not allow-listed", user_id, chat_id)
            return False
        rec, _is_new = self.sessions.get_or_create(chat_id)
        # Structured shortcuts run first: a guided command or an active wizard is handled inline
        # (returns None). Everything else falls through as a task the agent runs (a fully-specified
        # structured task, or the raw freeform message).
        task = self._route_command(chat_id, text, rec)
        if task is None:
            return True
        agent_key = self.store.agent()
        log.info("run %s for user %s (chat %s): %.60r", agent_key, user_id, chat_id, task)
        stop = threading.Event()
        progress = {"mid": None, "t0": _now()}
        threading.Thread(target=self._keep_typing, args=(chat_id, stop), daemon=True).start()
        threading.Thread(target=self._heartbeat, args=(chat_id, stop, progress), daemon=True).start()
        try:
            reply, _ok = run_agent(self.cfg, agent_key, rec, task, self.status)
        finally:
            stop.set()
        if progress["mid"]:  # close out the long-run progress note
            self.tg.edit_message_text(chat_id, progress["mid"], "Listo.")
        for piece in chunk_text(reply):
            self.tg.send_message(chat_id, piece)
        return True

    def _route_command(self, chat_id: int, text: str, rec: dict) -> str | None:
        """Handle guided commands + active wizards. Returns None when handled inline (a question
        asked, help shown, cancelled), or the task string for the agent to run otherwise."""
        low = text.strip().lower()
        if low in CANCEL_ALIASES:
            with self._wlock:
                had = self._wizards.pop(chat_id, None)
            self.tg.send_message(chat_id, "Cancelado." if had else "No hay ningun comando en curso.")
            return None
        if low in HELP_ALIASES:
            self.tg.send_message(chat_id, commands_help())
            return None
        if low in AUTHORS_ALIASES:
            return ("Ejecuta `python3 cli/censurado.py personas` y devolve la lista de autores tal "
                    "cual, sin agregar nada.")
        key = command_for(text)
        if key:  # start (or restart) a guided command
            spec = COMMANDS[key]
            with self._wlock:
                self._wizards[chat_id] = {"cmd": key, "step": 0, "fields": {}}
            self.tg.send_message(chat_id, f"[{spec['title']}] {spec['steps'][0]['prompt']}")
            return None
        with self._wlock:
            wiz = self._wizards.get(chat_id)
        if wiz:  # fill the current slot of the in-progress command (one worker per chat = no race)
            spec = COMMANDS[wiz["cmd"]]
            wiz["fields"][spec["steps"][wiz["step"]]["slot"]] = text.strip()
            wiz["step"] += 1
            if wiz["step"] < len(spec["steps"]):
                self.tg.send_message(chat_id, f"[{spec['title']}] {spec['steps'][wiz['step']]['prompt']}")
                return None
            with self._wlock:
                self._wizards.pop(chat_id, None)
            self.tg.send_message(chat_id, f"Dale, arranco con «{spec['title']}». Puede tardar; te aviso al terminar.")
            return spec["build"](wiz["fields"])
        return text  # no command, no wizard -> freeform, exactly as before

    def _keep_typing(self, chat_id: int, stop: threading.Event):
        while not stop.is_set():
            self.tg.send_typing(chat_id)
            stop.wait(self.cfg.typing_interval)

    def _heartbeat(self, chat_id: int, stop: threading.Event, progress: dict):
        """For a long turn: after progress_after seconds still running, post a 'still working' note
        and refresh it in place every progress_interval seconds so the user is never left guessing."""
        if stop.wait(self.cfg.progress_after):
            return  # finished before the threshold: no note
        progress["mid"] = self.tg.send_message(
            chat_id, "🕒 Sigo trabajando en esto, puede tardar unos minutos...")
        while not stop.wait(self.cfg.progress_interval):
            elapsed = int(_now() - progress["t0"])
            self.tg.edit_message_text(
                chat_id, progress["mid"], f"🕒 Sigo trabajando... ({elapsed // 60}m {elapsed % 60}s)")

    def _worker(self, chat_id: int, q: queue.Queue):
        while True:
            user_id, text = q.get()
            try:
                self.process(chat_id, user_id, text)
            except Exception as ex:
                log.exception("worker error for chat %s", chat_id)
                self.status.fail(self.store.agent(), f"bridge error: {ex}")
                self.tg.send_message(chat_id, "Something broke handling that. Try again.")
            finally:
                q.task_done()

    def dispatch(self, chat_id: int, user_id: int, text: str):
        with self._qlock:
            q = self._queues.get(chat_id)
            if q is None:
                q = queue.Queue(maxsize=self.cfg.max_queue)
                self._queues[chat_id] = q
                threading.Thread(target=self._worker, args=(chat_id, q), daemon=True).start()
        if user_id not in self.store.allowed():
            log.warning("DENY (pre-queue) user %s chat %s", user_id, chat_id)
            return
        try:
            q.put_nowait((user_id, text))
        except queue.Full:
            self.tg.send_message(chat_id, "Still working on your previous messages, hold on.")

    def run(self):
        me = self.tg.get_me()
        if me.get("ok"):
            u = me["result"].get("username", "")
            self.bot = {"username": u, "link": f"https://t.me/{u}" if u else ""}
        log.info("bridge up as @%s; agent=%s; %d allow-listed",
                 self.bot["username"] or "?", self.store.agent(), len(self.store.allowed()))
        offset, running = 0, {"v": True}

        def _stop(*_a):
            running["v"] = False
        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

        while running["v"]:
            for upd in self.tg.get_updates(offset):
                offset = max(offset, upd["update_id"] + 1)
                msg = upd.get("message") or {}
                text, frm = msg.get("text"), (msg.get("from") or {})
                chat = (msg.get("chat") or {}).get("id")
                if text and chat is not None and frm.get("id") is not None:
                    self.dispatch(chat, frm["id"], text)
        log.info("bridge shutting down")


# ---------------------------------------------------------------------- admin panel
def qr_svg(url: str) -> str:
    """Inline SVG QR for the t.me link, or '' if segno is unavailable (degrade to link)."""
    if not url:
        return ""
    try:
        import io
        import segno
        buff = io.BytesIO()
        segno.make(url, error="m").save(buff, kind="svg", scale=4, border=2, dark="#15110d")
        svg = buff.getvalue().decode()
        return svg[svg.find("<svg"):]  # strip the xml declaration
    except Exception:
        return ""


def admin_page(cfg: Config, store: ConfigStore, bot: dict) -> str:
    link = bot.get("link", "")
    qr = qr_svg(link)
    agent_opts = "".join(
        f'<option value="{k}"{" selected" if k == store.agent() else ""}>{html.escape(v["label"])}</option>'
        for k, v in cfg.agents.items())
    return _ADMIN_HTML.replace("__BOT__", html.escape("@" + bot.get("username", "") if bot.get("username") else "(connecting...)")) \
        .replace("__LINK__", html.escape(link)) \
        .replace("__QR__", qr or '<p class="muted">QR needs the <code>segno</code> package; use the link.</p>') \
        .replace("__AGENTS__", agent_opts)


_ADMIN_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Censurado Telegram bridge</title>
<style>
:root{--ink:#15110d;--paper:#f6f1e8;--red:#c0261f;--line:#ddd3c1;--muted:#7c7263}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:var(--paper);color:var(--ink)}
.wrap{max-width:640px;margin:24px auto;padding:0 16px}
h1{font-size:20px;margin:0 0 2px}.sub{color:var(--muted);margin:0 0 20px}
.card{background:#fff;border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:16px}
.card h2{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--red);margin:0 0 12px}
.bot{display:flex;gap:16px;align-items:center}.bot .qr{width:120px;height:120px;flex:none}.bot .qr svg{width:100%;height:100%}
.bot b{font-size:16px}a{color:var(--red)}
select,input{font:inherit;padding:8px 10px;border:1px solid var(--line);border-radius:8px;background:#fff}
button{font:inherit;font-weight:600;padding:8px 14px;border:none;border-radius:8px;background:var(--ink);color:#fff;cursor:pointer}
button.red{background:var(--red)}button:hover{opacity:.9}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
ul{list-style:none;margin:8px 0 0;padding:0}li{display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-top:1px dashed var(--line)}
li:first-child{border-top:none}.rm{background:none;color:var(--red);border:1px solid var(--line);padding:4px 10px}
.muted{color:var(--muted)}.err{color:var(--red)}.ok{color:#2f7d4e}code{background:var(--paper);padding:1px 5px;border-radius:4px}
.status{font-size:13px}
</style></head><body><div class="wrap">
<h1>El Censurado, Telegram bridge</h1><p class="sub">Local admin panel. Changes apply live.</p>

<div class="card"><h2>Bot</h2><div class="bot">
<div class="qr">__QR__</div>
<div><b>__BOT__</b><br><a href="__LINK__" target="_blank">__LINK__</a><br>
<span class="muted">Scan the QR or open the link to chat with the bot.</span></div>
</div></div>

<div class="card"><h2>Agent</h2><div class="row">
<select id="agent">__AGENTS__</select><button onclick="setAgent()">Use this agent</button>
<span id="agentmsg" class="muted"></span></div>
<p class="muted" style="margin:10px 0 0">Auth for each agent is set up outside the bridge; it just runs the CLI.</p></div>

<div class="card"><h2>Allowed Telegram user ids</h2>
<div class="row"><input id="newid" type="number" inputmode="numeric" placeholder="numeric id (from @userinfobot)">
<button onclick="addId()">Add</button></div>
<ul id="ids"></ul>
<p class="muted" style="margin:10px 0 0">Default-deny: only these ids get a reply. Everyone else is ignored.</p></div>

<div class="card"><h2>Status</h2><div id="status" class="status muted">loading...</div></div>
</div>
<script>
const TOKEN = new URLSearchParams(location.search).get("token") || "";
const H = {"X-Admin-Token": TOKEN, "Content-Type": "application/json"};
async function api(path, method="GET", body){
  const r = await fetch(path, {method, headers:H, body: body?JSON.stringify(body):undefined});
  if(!r.ok) throw new Error(await r.text()); return r.json();
}
function fmtTs(t){return t? new Date(t*1000).toLocaleString() : "never";}
async function refresh(){
  try{
    const s = await api("/api/state");
    document.getElementById("agent").value = s.agent;
    const ul = document.getElementById("ids"); ul.innerHTML = "";
    (s.allowed_ids||[]).forEach(id=>{
      const li=document.createElement("li");
      li.innerHTML = `<span>${id}</span>`;
      const b=document.createElement("button"); b.className="rm"; b.textContent="remove";
      b.onclick=()=>removeId(id); li.appendChild(b); ul.appendChild(li);
    });
    if(!(s.allowed_ids||[]).length) ul.innerHTML='<li class="muted">nobody yet</li>';
    const st=document.getElementById("status");
    st.className="status"; st.innerHTML =
      `agent <b>${s.agent}</b> &middot; handled ${s.handled} &middot; last ok ${fmtTs(s.last_ok_ts)}` +
      (s.last_error? `<br><span class="err">last error: ${s.last_error} (${fmtTs(s.last_error_ts)})</span>` : `<br><span class="ok">no errors</span>`);
  }catch(e){ document.getElementById("status").innerHTML='<span class="err">'+e.message+'</span>'; }
}
async function setAgent(){ const v=document.getElementById("agent").value;
  const m=document.getElementById("agentmsg");
  try{ await api("/api/agent","POST",{agent:v}); m.textContent="switched to "+v; refresh(); }
  catch(e){ m.innerHTML='<span class="err">'+e.message+'</span>'; } }
async function addId(){ const el=document.getElementById("newid"); const v=parseInt(el.value,10);
  if(!v) return; await api("/api/allow","POST",{id:v}); el.value=""; refresh(); }
async function removeId(id){ await api("/api/deny","POST",{id}); refresh(); }
refresh(); setInterval(refresh, 8000);
</script></body></html>"""


class _Admin(http.server.BaseHTTPRequestHandler):
    cfg: Config = None       # injected
    store: ConfigStore = None
    status: Status = None
    bot: dict = None

    def log_message(self, *a):  # quiet
        pass

    def _authed(self) -> bool:
        want = self.cfg.admin_token
        if not want:  # no token configured -> panel disabled for safety
            return False
        got = self.headers.get("X-Admin-Token", "")
        if not got:
            q = urllib.parse.urlparse(self.path).query
            got = urllib.parse.parse_qs(q).get("token", [""])[0]
        import hmac
        return hmac.compare_digest(got, want)

    def _json(self, code: int, obj: dict):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if not self._authed():
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b"unauthorized (append ?token=...)")
            return
        if path == "/" or path == "":
            page = admin_page(self.cfg, self.store, self.bot).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)
        elif path == "/api/state":
            self._json(200, {"agent": self.store.agent(), "allowed_ids": self.store.allowed(),
                             "agents": {k: v["label"] for k, v in self.cfg.agents.items()},
                             "bot": self.bot, **self.status.snapshot()})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if not self._authed():
            self._json(401, {"error": "unauthorized"})
            return
        path = urllib.parse.urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self._json(400, {"error": "bad json"})
            return
        try:
            if path == "/api/agent":
                self.store.set_agent(str(body.get("agent", "")).strip())
            elif path == "/api/allow":
                self.store.add(int(body["id"]))
            elif path == "/api/deny":
                self.store.remove(int(body["id"]))
            else:
                self._json(404, {"error": "not found"})
                return
        except (ValueError, KeyError, TypeError) as ex:
            self._json(400, {"error": str(ex)})
            return
        self._json(200, {"ok": True, "agent": self.store.agent(),
                         "allowed_ids": self.store.allowed()})


class _ThreadingHTTP(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def start_admin(cfg: Config, store: ConfigStore, status: Status, bot: dict) -> _ThreadingHTTP:
    handler = type("_AdminBound", (_Admin,),
                   {"cfg": cfg, "store": store, "status": status, "bot": bot})
    srv = _ThreadingHTTP((cfg.admin_host, cfg.admin_port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    if cfg.admin_token:
        log.info("admin panel on http://%s:%s/?token=<TG_ADMIN_TOKEN>",
                 cfg.admin_host, cfg.admin_port)
    else:
        log.warning("TG_ADMIN_TOKEN not set: admin panel is DISABLED (401 to all)")
    return srv


def main():
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s", stream=sys.stdout)
    cfg = Config.from_env()
    store = ConfigStore(cfg.data_dir, cfg.default_agent, cfg.seed_allowed)
    status = Status()
    bridge = Bridge(cfg, store, status)
    # get_me early so the panel shows the bot name + QR before any message arrives.
    me = bridge.tg.get_me()
    if me.get("ok"):
        u = me["result"].get("username", "")
        bridge.bot = {"username": u, "link": f"https://t.me/{u}" if u else ""}
    start_admin(cfg, store, status, bridge.bot)
    bridge.run()


if __name__ == "__main__":
    main()
