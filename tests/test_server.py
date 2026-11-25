import asyncio

from vast_hls_mcp.server import server

from .conftest import SPLICE_INSERT_OUT


def _call(name: str, args: dict) -> dict:
    result = asyncio.run(server.call_tool(name, args))
    assert result.is_error is False
    return result.structured_content


def test_all_tools_registered_and_read_only():
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    assert names == {
        "validate_vast",
        "unwrap_vast",
        "inspect_hls",
        "find_ad_breaks",
        "decode_scte35",
    }
    for tool in tools:
        assert tool.annotations.read_only_hint is True
        assert tool.description


def test_decode_scte35_tool():
    out = _call("decode_scte35", {"payload": SPLICE_INSERT_OUT})
    assert out["ok"] is True
    assert out["splice_command_name"] == "splice_insert"


def test_validate_vast_tool_inline(fixture_text):
    out = _call("validate_vast", {"source": fixture_text("vast4_inline.xml")})
    assert out["ok"] is True and out["status"] == "valid"
    assert out["fetch"] == {"source": "inline"}


def test_find_ad_breaks_rejects_master(fixture_text):
    out = _call("find_ad_breaks", {"source": fixture_text("master.m3u8")})
    assert out["ok"] is False
    assert "master playlist" in out["error"]


def test_find_ad_breaks_tool(fixture_text):
    out = _call("find_ad_breaks", {"source": fixture_text("cue_out_vod.m3u8")})
    assert out["ok"] is True and out["break_count"] == 2


def test_errors_are_structured_not_raised():
    out = _call("decode_scte35", {"payload": "zzz"})
    assert out["ok"] is False and out["error_type"] == "Scte35Error"
    out = _call("unwrap_vast", {"url": "<VAST/>"})
    assert out["ok"] is False
    out = _call("inspect_hls", {"source": "not a playlist"})
    assert out["ok"] is False and out["error_type"] == "HlsError"
