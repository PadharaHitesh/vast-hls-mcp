"""Entry point for the in-browser demo (Pyodide).

Runs the same parsers the MCP tools use, on pasted text only: browsers block
cross-site fetches, so URL input and ``unwrap_vast`` are MCP-only.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from . import adbreaks, hls, scte35, vast


def _find_ad_breaks(text: str) -> dict[str, Any]:
    if hls.is_master(hls.lines_of(text)):
        raise hls.HlsError(
            "this is a master playlist; ad markers live in media playlists. "
            "Paste one variant (media) playlist instead."
        )
    return adbreaks.find(text)


TOOLS: dict[str, Callable[[str], dict[str, Any]]] = {
    "decode_scte35": scte35.decode,
    "find_ad_breaks": _find_ad_breaks,
    "inspect_hls": hls.inspect,
    "validate_vast": vast.analyze,
}

_EXPECTED_ERRORS = (scte35.Scte35Error, hls.HlsError, vast.VastError)


def run(tool: str, text: str) -> str:
    """Run one tool on pasted text and return the result as JSON."""
    fn = TOOLS.get(tool)
    if fn is None:
        result: dict[str, Any] = {"ok": False, "error": f"unknown tool {tool!r}"}
    elif not text.strip():
        result = {"ok": False, "error": "nothing to analyse: paste some input first"}
    else:
        try:
            result = {"ok": True, **fn(text)}
        except _EXPECTED_ERRORS as exc:
            result = {"ok": False, "error": str(exc), "error_type": type(exc).__name__}
    return json.dumps(result)
