"""The cli adapter: one stateless headless agent-CLI run, prompt in argv, text on stdout."""
import subprocess

from .errors import AdapterError


class CliAdapter:
    def __init__(self, cfg: dict):
        self.cmd = cfg["cmd"]
        self.timeout = cfg.get("timeout_s", 3600)

    def complete(self, prompt: str, want_json: bool) -> str:
        argv = [a.replace("{prompt}", prompt) for a in self.cmd]
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=self.timeout)
        except subprocess.TimeoutExpired as e:
            raise AdapterError(f"cli timed out after {self.timeout}s") from e
        except FileNotFoundError as e:
            raise AdapterError(f"cli not found: {e}") from e
        if p.returncode != 0:
            raise AdapterError(f"cli exit {p.returncode}: {(p.stderr or p.stdout)[-300:]}")
        return p.stdout.strip()
