"""Safe HTTP fetching for ad tags and manifests.

An MCP server fetches whatever URL the model hands it, so this module guards
against server-side request forgery: http/https only, every redirect hop is
re-checked, private/loopback/link-local targets are refused, and responses are
size-capped and time-limited.
"""

from __future__ import annotations

import ipaddress
import os
import socket
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx

MAX_BYTES = 2 * 1024 * 1024
TIMEOUT_SECONDS = 10.0
MAX_REDIRECTS = 5
USER_AGENT = "vast-hls-mcp/0.1 (+https://github.com/PadharaHitesh/vast-hls-mcp)"


class FetchError(RuntimeError):
    """The URL could not be fetched safely."""


@dataclass
class FetchResult:
    url: str
    final_url: str
    status_code: int
    content_type: str
    text: str
    elapsed_ms: int
    redirects: list[str] = field(default_factory=list)


def is_url(source: str) -> bool:
    return source.strip().lower().startswith(("http://", "https://"))


def _allow_private() -> bool:
    return os.environ.get("VAST_HLS_MCP_ALLOW_PRIVATE", "").lower() in ("1", "true", "yes")


def _check_host(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise FetchError(f"only http/https URLs are allowed, got {parsed.scheme!r}")
    host = parsed.hostname
    if not host:
        raise FetchError("URL has no host")
    if _allow_private():
        return
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise FetchError(f"cannot resolve host {host!r}: {exc}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise FetchError(
                f"refusing to fetch {host} ({ip}): private, loopback or reserved address. "
                "Set VAST_HLS_MCP_ALLOW_PRIVATE=1 to allow local testing."
            )


def fetch_text(url: str, *, client: httpx.Client | None = None) -> FetchResult:
    """GET a URL with SSRF checks on every hop, a size cap and a timeout."""
    own_client = client is None
    client = client or httpx.Client(
        timeout=TIMEOUT_SECONDS,
        follow_redirects=False,
        headers={"User-Agent": USER_AGENT},
    )
    redirects: list[str] = []
    current = url.strip()
    try:
        for _ in range(MAX_REDIRECTS + 1):
            _check_host(current)
            started = time.monotonic()
            with client.stream("GET", current) as resp:
                if resp.is_redirect:
                    location = resp.headers.get("location")
                    if not location:
                        raise FetchError(f"redirect from {current} without Location header")
                    redirects.append(current)
                    current = urljoin(current, location)
                    continue
                chunks: list[bytes] = []
                size = 0
                for chunk in resp.iter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise FetchError(f"response from {current} exceeds {MAX_BYTES} bytes")
                    chunks.append(chunk)
                body = b"".join(chunks)
                encoding = resp.encoding or "utf-8"
                return FetchResult(
                    url=url,
                    final_url=current,
                    status_code=resp.status_code,
                    content_type=resp.headers.get("content-type", ""),
                    text=body.decode(encoding, errors="replace"),
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                    redirects=redirects,
                )
        raise FetchError(f"more than {MAX_REDIRECTS} redirects starting at {url}")
    except httpx.TimeoutException as exc:
        raise FetchError(f"timed out after {TIMEOUT_SECONDS}s fetching {current}") from exc
    except httpx.HTTPError as exc:
        raise FetchError(f"HTTP error fetching {current}: {exc}") from exc
    finally:
        if own_client:
            client.close()


def load(source: str, fetcher=fetch_text) -> tuple[str, dict]:
    """Return (text, meta). `source` is either a URL or the document itself."""
    if is_url(source):
        result = fetcher(source.strip())
        if result.status_code >= 400:
            raise FetchError(f"{result.final_url} returned HTTP {result.status_code}")
        meta = {
            "source": "url",
            "url": result.url,
            "final_url": result.final_url,
            "status_code": result.status_code,
            "content_type": result.content_type,
            "elapsed_ms": result.elapsed_ms,
        }
        if result.redirects:
            meta["redirects"] = result.redirects
        return result.text, meta
    return source, {"source": "inline"}
