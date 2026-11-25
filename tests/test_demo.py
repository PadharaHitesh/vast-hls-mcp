import importlib
import json
import subprocess
import sys

import pytest

from vast_hls_mcp import demo

from .conftest import SPLICE_INSERT_OUT


def _run(tool: str, text: str) -> dict:
    return json.loads(demo.run(tool, text))


def test_decode_scte35():
    out = _run("decode_scte35", SPLICE_INSERT_OUT)
    assert out["ok"] is True
    assert out["summary"].startswith("splice_insert, OUT")


def test_find_ad_breaks(fixture_text):
    out = _run("find_ad_breaks", fixture_text("cue_out_vod.m3u8"))
    assert out["ok"] is True
    assert out["break_count"] == 2 and out["healthy"] is False


def test_find_ad_breaks_rejects_master(fixture_text):
    out = _run("find_ad_breaks", fixture_text("master.m3u8"))
    assert out["ok"] is False and "master playlist" in out["error"]


def test_inspect_hls_and_validate_vast(fixture_text):
    assert _run("inspect_hls", fixture_text("master.m3u8"))["variant_count"] == 3
    assert _run("validate_vast", fixture_text("vast_broken.xml"))["status"] == "invalid"


@pytest.mark.parametrize(
    ("tool", "text", "message"),
    [
        ("nope", "x", "unknown tool"),
        ("decode_scte35", "   ", "nothing to analyse"),
        ("validate_vast", "<html/>", "expected <VAST>"),
        ("inspect_hls", "hello", "#EXTM3U"),
    ],
)
def test_errors_are_structured(tool, text, message):
    out = _run(tool, text)
    assert out["ok"] is False and message in out["error"]


def test_demo_imports_without_httpx():
    """Pyodide has no httpx/network stack: the demo must import without it."""
    code = (
        "import sys; sys.modules['httpx'] = None; "
        "import vast_hls_mcp.demo as d; print(d.run('decode_scte35', sys.argv[1]))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code, SPLICE_INSERT_OUT],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(proc.stdout)["ok"] is True
    importlib.import_module("vast_hls_mcp.demo")  # and normally, too
