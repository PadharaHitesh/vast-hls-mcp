import pytest

from vast_hls_mcp import hls


def test_attribute_list_handles_quoted_commas():
    attrs = hls.parse_attributes('BANDWIDTH=800000,CODECS="avc1.4d401e,mp4a.40.2",NAME="A, B"')
    assert attrs == {"BANDWIDTH": "800000", "CODECS": "avc1.4d401e,mp4a.40.2", "NAME": "A, B"}


def test_master_playlist(fixture_text):
    r = hls.inspect(fixture_text("master.m3u8"))
    assert r["playlist_type"] == "master"
    assert r["hls_version"] == 6
    assert r["variant_count"] == 3
    assert r["bandwidth_range"] == [800000, 5000000]
    assert [v["bandwidth"] for v in r["variants"]] == [800000, 2500000, 5000000]
    assert r["renditions"][0]["group_id"] == "aud"
    warnings = " | ".join(r["warnings"])
    assert "720p.m3u8) has no CODECS" in warnings
    assert "AUDIO group 'sound' that is not defined" in warnings
    assert "no I-frame playlists" in warnings


def test_vod_media_playlist(fixture_text):
    r = hls.inspect(fixture_text("cue_out_vod.m3u8"))
    assert r["playlist_type"] == "media"
    assert r["live"] is False
    assert r["playlist_kind"] == "VOD"
    assert r["target_duration"] == 10
    assert r["segment_count"] == 13
    assert r["total_duration_seconds"] == pytest.approx(130.294)
    assert r["discontinuities"] == 4
    assert r["errors"] == [] and r["warnings"] == []


def test_live_playlist_warnings(fixture_text):
    r = hls.inspect(fixture_text("live_mid_break.m3u8"))
    assert r["live"] is True
    assert r["media_sequence"] == 500
    warnings = " | ".join(r["warnings"])
    assert "exceed EXT-X-TARGETDURATION 6s" in warnings
    assert "without #EXT-X-PROGRAM-DATE-TIME" in warnings


def test_missing_target_duration_is_an_error():
    r = hls.inspect("#EXTM3U\n#EXTINF:4.0,\na.ts\n#EXT-X-ENDLIST\n")
    assert "missing #EXT-X-TARGETDURATION (required)" in r["errors"]


def test_not_a_playlist():
    with pytest.raises(hls.HlsError):
        hls.inspect("<html></html>")
