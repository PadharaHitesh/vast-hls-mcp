import socket

import httpx
import pytest

from vast_hls_mcp import fetch


def _resolve_to(ip: str):
    def fake(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    return fake


def test_is_url():
    assert fetch.is_url("https://a.example/x")
    assert fetch.is_url("  HTTP://a.example/x")
    assert not fetch.is_url("<VAST/>")


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "169.254.169.254", "192.168.1.1"])
def test_private_targets_are_refused(monkeypatch, ip):
    monkeypatch.setattr(socket, "getaddrinfo", _resolve_to(ip))
    with pytest.raises(fetch.FetchError, match="refusing"):
        fetch._check_host("http://internal.example/")


def test_private_allowed_with_env(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _resolve_to("127.0.0.1"))
    monkeypatch.setenv("VAST_HLS_MCP_ALLOW_PRIVATE", "1")
    fetch._check_host("http://localhost:8000/")


def test_non_http_scheme_refused():
    with pytest.raises(fetch.FetchError, match="only http/https"):
        fetch._check_host("file:///etc/passwd")


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def test_redirect_to_private_address_is_blocked(monkeypatch):
    def resolve(host, port, *a, **k):
        ip = "10.0.0.1" if host == "metadata.internal" else "93.184.216.34"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)

    def handler(request):
        return httpx.Response(302, headers={"location": "http://metadata.internal/latest"})

    with pytest.raises(fetch.FetchError, match="refusing"):
        fetch.fetch_text("https://ads.example.com/tag", client=_client(handler))


def test_redirects_are_followed_and_recorded(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _resolve_to("93.184.216.34"))

    def handler(request):
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/final"})
        return httpx.Response(200, text="<VAST version='4.0'/>")

    res = fetch.fetch_text("https://ads.example.com/start", client=_client(handler))
    assert res.final_url == "https://ads.example.com/final"
    assert res.redirects == ["https://ads.example.com/start"]
    assert "VAST" in res.text


def test_oversized_response_is_rejected(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _resolve_to("93.184.216.34"))
    monkeypatch.setattr(fetch, "MAX_BYTES", 10)

    def handler(request):
        return httpx.Response(200, content=b"x" * 100)

    with pytest.raises(fetch.FetchError, match="exceeds"):
        fetch.fetch_text("https://cdn.example.com/huge.m3u8", client=_client(handler))


def test_load_inline_and_http_error():
    text, meta = fetch.load("#EXTM3U")
    assert text == "#EXTM3U" and meta == {"source": "inline"}

    def failing(url):
        return fetch.FetchResult(url, url, 404, "text/plain", "", 5)

    with pytest.raises(fetch.FetchError, match="HTTP 404"):
        fetch.load("https://ads.example.com/missing", fetcher=failing)
