"""MCP server exposing video ad-delivery debugging tools to AI assistants."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from . import __version__, adbreaks, hls, scte35, vast
from .fetch import FetchError, load

INSTRUCTIONS = """\
Tools for debugging video ad delivery (VAST ad tags, HLS streams, SCTE-35 ad breaks).
Typical flows:
- "Why is this ad tag not filling?" -> validate_vast, then unwrap_vast if it is a wrapper.
- "Are the ad breaks in this stream OK?" -> find_ad_breaks on the media playlist
  (use inspect_hls first on a master playlist to pick a variant URL).
- "What does this cue mean?" -> decode_scte35 with the base64/hex payload.
Every tool accepts either a URL or the raw document text."""

server = MCPServer(
    name="vast-hls-mcp",
    title="VAST / HLS / SCTE-35 ad debugger",
    instructions=INSTRUCTIONS,
    version=__version__,
)

_READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True, idempotentHint=True)


def _fail(exc: Exception) -> dict[str, Any]:
    return {"ok": False, "error": str(exc), "error_type": type(exc).__name__}


@server.tool(annotations=_READ_ONLY)
def validate_vast(source: str) -> dict[str, Any]:
    """Validate a VAST ad response (2.0-4.3) given as a URL or raw XML.

    Returns the version, each ad (InLine or Wrapper), creatives, media files,
    tracking events, and lists of errors / warnings / info that explain why a
    player might reject or under-count the ad (missing Impression, bad
    Duration, http:// media, missing UniversalAdId, no-fill without Error URL...).
    """
    try:
        text, meta = load(source)
        return {"ok": True, **vast.analyze(text), "fetch": meta}
    except (FetchError, vast.VastError) as exc:
        return _fail(exc)


@server.tool(annotations=_READ_ONLY)
def unwrap_vast(url: str, max_depth: int = vast.MAX_WRAPPER_DEPTH) -> dict[str, Any]:
    """Follow a VAST wrapper chain from an ad tag URL down to the InLine ad.

    Reports every hop (ad system, status, latency, impression count, errors)
    and the outcome: inline, no_fill, loop, too_deep, http_error, fetch_error
    or parse_error. Use it when an ad tag returns a Wrapper and you need to
    know which demand partner breaks the chain.
    """
    if not url.lower().startswith(("http://", "https://")):
        return _fail(ValueError("unwrap_vast needs an http(s) ad tag URL"))
    return {"ok": True, **vast.unwrap(url, max_depth=max(1, min(max_depth, 10)))}


@server.tool(annotations=_READ_ONLY)
def inspect_hls(source: str) -> dict[str, Any]:
    """Inspect an HLS playlist (URL or raw .m3u8 text).

    For a master playlist: variants (bandwidth, resolution, codecs), audio and
    subtitle renditions, I-frame playlists. For a media playlist: live vs VOD,
    target duration, segment count, total duration, discontinuities,
    encryption. Both include spec warnings (RFC 8216).
    """
    try:
        text, meta = load(source)
        return {"ok": True, **hls.inspect(text), "fetch": meta}
    except (FetchError, hls.HlsError) as exc:
        return _fail(exc)


@server.tool(annotations=_READ_ONLY)
def find_ad_breaks(source: str) -> dict[str, Any]:
    """Find SCTE-35 ad breaks in an HLS media playlist (URL or raw .m3u8 text).

    Supports EXT-X-CUE-OUT/CUE-IN, EXT-X-DATERANGE SCTE35-OUT/IN and
    EXT-OATCLS-SCTE35. For each break: start time, planned vs actual duration,
    decoded cue. Flags unmatched CUE-OUT/CUE-IN, duration mismatches, breaks
    that never close and corrupted cues.
    """
    try:
        text, meta = load(source)
        lines = hls.lines_of(text)
        if hls.is_master(lines):
            return _fail(
                hls.HlsError(
                    "this is a master playlist; ad markers live in media playlists. "
                    "Call inspect_hls to list variants, then pass one variant URL."
                )
            )
        return {"ok": True, **adbreaks.find(text), "fetch": meta}
    except (FetchError, hls.HlsError) as exc:
        return _fail(exc)


@server.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def decode_scte35(payload: str) -> dict[str, Any]:
    """Decode a SCTE-35 cue given as base64 (e.g. '/DAvAAAA...') or hex ('0xFC30...').

    Returns the splice command (splice_insert, time_signal...), splice time,
    break duration, segmentation descriptors (type, UPID, duration), CRC check
    and a one-line summary.
    """
    try:
        return {"ok": True, **scte35.decode(payload)}
    except scte35.Scte35Error as exc:
        return _fail(exc)


def main() -> None:
    server.run()


if __name__ == "__main__":
    main()
