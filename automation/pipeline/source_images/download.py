"""Bounded public downloads, including redirect destination checks."""
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import httpx


class PublicDownloader:
    def __init__(self, client: httpx.Client):
        self.client = client

    def get(self, url: str, limit: int) -> tuple[str, bytes, str]:
        for _ in range(5):
            parsed = urlsplit(url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username:
                raise ValueError("invalid source URL")
            addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
                raise ValueError("source must be public")
            with self.client.stream("GET", url, follow_redirects=False,
                                    headers={"User-Agent": "Censurado/1.0"}, timeout=12) as r:
                if r.is_redirect:
                    url = urljoin(url, r.headers["location"])
                    continue
                r.raise_for_status()
                data = bytearray()
                for chunk in r.iter_bytes():
                    data.extend(chunk)
                    if len(data) > limit:
                        raise ValueError("source exceeds download limit")
                return r.headers.get("content-type", "").split(";")[0], bytes(data), url
        raise ValueError("too many redirects")
