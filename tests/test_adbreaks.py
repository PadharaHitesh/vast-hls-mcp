import pytest

from vast_hls_mcp import adbreaks


def test_cue_out_vod_breaks_and_issues(fixture_text):
    r = adbreaks.find(fixture_text("cue_out_vod.m3u8"))
    assert r["break_count"] == 2
    first, second = r["breaks"]

    assert first["style"] == "cue-out"
    assert first["start_seconds"] == 20.0
    assert first["planned_duration_seconds"] == pytest.approx(60.293567)
    assert first["actual_duration_seconds"] == pytest.approx(60.294)
    assert first["segments"] == 6
    assert first["closed"] is True
    assert first["scte35"]["is_ad_start"] is True
    assert first["scte35"]["crc_valid"] is True

    assert second["planned_duration_seconds"] == 30.0
    assert second["actual_duration_seconds"] == 20.0

    issues = " | ".join(r["issues"])
    assert "planned 30.0s but segments cover 20.0s" in issues
    assert "CUE-IN at 120.294s without a matching CUE-OUT" in issues
    assert r["healthy"] is False
    assert r["total_ad_seconds"] == pytest.approx(80.294)


def test_daterange_break_open_at_live_edge(fixture_text):
    r = adbreaks.find(fixture_text("daterange_live.m3u8"))
    (brk,) = r["breaks"]
    assert brk["style"] == "daterange"
    assert brk["id"] == "splice-1207959695"
    assert brk["start_seconds"] == 6.0
    assert brk["planned_duration_seconds"] == pytest.approx(60.293567)
    assert brk["scte35"]["summary"].startswith("splice_insert, OUT")
    assert brk["actual_duration_seconds"] == 18.0
    assert brk["closed"] is False
    assert any("never closes" in i for i in r["issues"])


def test_daterange_closed_by_scte35_in():
    playlist = """#EXTM3U
#EXT-X-TARGETDURATION:6
#EXT-X-DATERANGE:ID="b1",START-DATE="2026-01-01T00:00:00Z",PLANNED-DURATION=12,SCTE35-OUT=0xFC
#EXTINF:6.0,
a.ts
#EXTINF:6.0,
b.ts
#EXT-X-DATERANGE:ID="b1",START-DATE="2026-01-01T00:00:00Z",DURATION=12,SCTE35-IN=0xFC
#EXTINF:6.0,
c.ts
"""
    r = adbreaks.find(playlist)
    (brk,) = r["breaks"]
    assert brk["closed"] is True
    assert brk["actual_duration_seconds"] == 12.0
    assert brk["signalled_duration_seconds"] == 12.0
    # The truncated SCTE35-OUT payload is reported, not crashed on.
    assert any("did not decode" in i for i in r["issues"])


def test_joined_mid_break_on_live_edge(fixture_text):
    r = adbreaks.find(fixture_text("live_mid_break.m3u8"))
    (brk,) = r["breaks"]
    assert brk["joined_mid_break"] is True
    assert brk["start_seconds"] == -12.0
    assert brk["actual_duration_seconds"] == 30.0
    assert brk["closed"] is True
    assert r["healthy"] is True


def test_cue_out_while_open_is_flagged():
    playlist = """#EXTM3U
#EXT-X-TARGETDURATION:10
#EXT-X-CUE-OUT:10
#EXTINF:10.0,
a.ts
#EXT-X-CUE-OUT:10
#EXTINF:10.0,
b.ts
#EXT-X-CUE-IN
"""
    r = adbreaks.find(playlist)
    assert r["break_count"] == 2
    assert any("still open" in i for i in r["issues"])


@pytest.mark.parametrize(
    ("value", "expected"),
    [("30", 30.0), ("DURATION=15.5", 15.5), ("", None), ("2.0", 2.0)],
)
def test_cue_out_duration_forms(value, expected):
    assert adbreaks._cue_out_duration(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("12/30", (12.0, 30.0)), ("ElapsedTime=5.5,Duration=30", (5.5, 30.0))],
)
def test_cue_out_cont_forms(value, expected):
    assert adbreaks._cue_cont(value.upper() if "=" in value else value) == expected
