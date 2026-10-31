"""Outbound HTTP with SSRF protection: only public hosts, every redirect re-checked, size/time capped.

The validated IP is pinned for the connection (no DNS-rebinding window) for web_fetch / http_request.
shortcut: browser_read re-checks every sub-request URL but cannot pin Chromium's own connections, so it keeps a
DNS-rebinding window; prefer web_fetch for untrusted hosts.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from html.parser import HTMLParser
from typing import ClassVar
from urllib.parse import urljoin, urlparse

import httpx

from personal_agent.policy.engine import PolicyEngine

MAX_BYTES = 2_000_000
MAX_REDIRECTS = 5
TEXT_TYPES = ("text/", "application/json", "application/xml", "application/xhtml", "application/rss", "application/atom")


class UnsafeUrl(ValueError):
    pass


def check_url(url: str, policy: PolicyEngine) -> str:
    """Validate the URL and return the public IP that was checked (callers connect to THAT address)."""
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password:
        raise UnsafeUrl("only plain http(s) URLs without credentials are allowed")
    allow = policy.cfg.get("network", {}).get("domain_allowlist", [])
    if allow and not policy.check_domain(url).allowed:
        raise UnsafeUrl("the domain is not on the allowlist")
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except OSError as e:
        raise UnsafeUrl(f"cannot resolve host: {e}") from e
    ips: list[str] = []
    for info in infos:
        try:
            ip = ipaddress.ip_address(str(info[4][0]).split("%")[0])
        except ValueError as e:
            raise UnsafeUrl("the host resolves to an unparseable address") from e
        mapped = ip.ipv4_mapped if isinstance(ip, ipaddress.IPv6Address) else None
        # loopback, private, link-local (cloud metadata 169.254.169.254), CGNAT, reserved... also IPv4-in-IPv6
        if not ip.is_global or (mapped is not None and not mapped.is_global):
            raise UnsafeUrl("the host resolves to a non-public address")
        ips.append(str(ip))
    if not ips:
        raise UnsafeUrl("the host did not resolve")
    return ips[0]


def fetch(url: str, policy: PolicyEngine, *, method: str = "GET", headers: dict[str, str] | None = None,
          content: bytes | None = None, timeout: float = 20.0, follow: bool = True,
          transport: httpx.BaseTransport | None = None) -> tuple[int, str, bytes, str]:
    """Return (status, content_type, body (<= MAX_BYTES), final_url)."""
    with httpx.Client(timeout=timeout, follow_redirects=False, transport=transport) as client:
        for _ in range(MAX_REDIRECTS + 1):
            ip = check_url(url, policy)
            u = urlparse(url)
            # Connect to the address we validated (a second DNS lookup could return a private one: DNS rebinding),
            # but keep the original Host header and TLS server name so virtual hosting and certificates still work.
            host = f"[{ip}]" if ":" in ip else ip
            pinned = u._replace(netloc=f"{host}:{u.port}" if u.port else host).geturl()
            hdrs = {**(headers or {}), "Host": u.netloc.rsplit("@", 1)[-1]}
            ext = {"sni_hostname": u.hostname} if u.scheme == "https" and u.hostname else {}
            with client.stream(method, pinned, headers=hdrs, content=content, extensions=ext) as r:
                if r.is_redirect and follow and (loc := r.headers.get("location")):
                    url, method, content = urljoin(url, loc), "GET", None
                    continue
                ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
                body = b""
                if ctype == "" or ctype.startswith(TEXT_TYPES):
                    for part in r.iter_bytes():
                        body += part
                        if len(body) > MAX_BYTES:
                            body = body[:MAX_BYTES]
                            break
                return r.status_code, ctype, body, url
    raise UnsafeUrl("too many redirects")


class _Text(HTMLParser):
    SKIP: ClassVar[set[str]] = {"script", "style", "noscript", "svg", "template", "head"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag in ("p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "section", "article"):
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> tuple[str, str]:
    p = _Text()
    p.feed(html)
    text = re.sub(r"[ \t]+", " ", "".join(p.parts))
    return p.title.strip(), re.sub(r"\n\s*\n+", "\n\n", text).strip()
