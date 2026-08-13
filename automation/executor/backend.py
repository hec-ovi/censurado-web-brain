"""The backend client: list schedules, record run outcomes. Stdlib only, like the
toolkit, so the executor's HTTP surface has no dependency of its own."""
import json
import urllib.error
import urllib.request


class BackendError(RuntimeError):
    pass


class Backend:
    def __init__(self, base_url: str, token: str, timeout_s: int = 20):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_s = timeout_s

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            raise BackendError(f"{method} {path}: {e.code} {detail}") from e
        except urllib.error.URLError as e:
            raise BackendError(f"{method} {path}: {e.reason}") from e
        return json.loads(raw) if raw else {}

    def schedules(self) -> list[dict]:
        """The active schedule registry (tombstoned rows are already excluded)."""
        return self._request("GET", "/schedules").get("schedules", [])

    def settings(self) -> dict:
        """The automation-settings singleton the panel's Models section edits
        ({} when never set). The executor asks for the raw secrets explicitly:
        it is the one consumer that feeds them into the derived pipeline config."""
        return self._request("GET", "/automation-settings?include_secrets=true").get("settings", {})

    def put_status(self, status: dict) -> None:
        """Replace the automation-status heartbeat singleton the panel reads."""
        self._request("PUT", "/automation-status", {"settings": status})

    def record_run(self, slug: str, run: dict) -> None:
        """Record one firing on the schedule's run strip. A repeated run_id
        replaces its entry, which is how "running" becomes the outcome."""
        self._request("POST", f"/schedules/{slug}/runs", run)
