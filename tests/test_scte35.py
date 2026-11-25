import base64

import pytest

from vast_hls_mcp import scte35

from .conftest import SPLICE_INSERT_OUT, TIME_SIGNAL_PPO


def test_time_signal_with_placement_opportunity_descriptor():
    info = scte35.decode(TIME_SIGNAL_PPO)
    assert info["splice_command_name"] == "time_signal"
    assert info["splice_command"]["pts_time"] == 1924989008
    assert info["splice_time_seconds"] == pytest.approx(21388.766756)
    seg = info["descriptors"][0]
    assert seg["identifier"] == "CUEI"
    assert seg["segmentation_event_id"] == 0x4800008E
    assert seg["segmentation_type_id"] == 0x34
    assert seg["segmentation_type_name"] == "Provider Placement Opportunity Start"
    assert seg["segmentation_duration_seconds"] == 307.0
    assert seg["segmentation_upid_type_name"] == "TI"
    assert seg["segment_num"] == 2
    assert info["crc_valid"] is True
    assert info["warnings"] == []


def test_splice_insert_out_with_break_duration_and_avail_descriptor():
    info = scte35.decode(SPLICE_INSERT_OUT)
    cmd = info["splice_command"]
    assert info["splice_command_name"] == "splice_insert"
    assert cmd["splice_event_id"] == 0x4800008F
    assert cmd["out_of_network_indicator"] is True
    assert cmd["break_duration"]["auto_return"] is True
    assert cmd["break_duration"]["duration_seconds"] == pytest.approx(60.293567)
    assert info["descriptors"][0]["name"] == "avail_descriptor"
    assert info["descriptors"][0]["provider_avail_id"] == 309
    assert info["crc_valid"] is True
    assert "OUT (ad start)" in info["summary"]


def test_hex_and_base64_inputs_decode_identically():
    raw = base64.b64decode(SPLICE_INSERT_OUT)
    from_hex = scte35.decode("0x" + raw.hex().upper())
    from_bare_hex = scte35.decode(raw.hex())
    from_bytes = scte35.decode(raw)
    from_b64 = scte35.decode(SPLICE_INSERT_OUT)
    assert from_hex == from_b64 == from_bare_hex == from_bytes


def test_helpers_classify_cues():
    out = scte35.decode(SPLICE_INSERT_OUT)
    ppo = scte35.decode(TIME_SIGNAL_PPO)
    assert scte35.is_ad_start(out) is True
    assert scte35.is_ad_start(ppo) is True
    assert scte35.cue_duration_seconds(out) == pytest.approx(60.293567)
    assert scte35.cue_duration_seconds(ppo) == 307.0


def test_corrupted_cue_reports_crc_mismatch():
    raw = bytearray(base64.b64decode(SPLICE_INSERT_OUT))
    raw[-5] ^= 0xFF  # flip a byte inside the descriptor loop
    info = scte35.decode(bytes(raw))
    assert info["crc_valid"] is False
    assert any("CRC mismatch" in w for w in info["warnings"])


def test_crc32_mpeg2_known_vector():
    # CRC-32/MPEG-2 check value for "123456789".
    assert scte35.crc32_mpeg2(b"123456789") == 0x0376E6E7


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("", "empty"),
        ("not base64 !!!", "neither valid base64 nor hex"),
        (base64.b64encode(b"\x47\x00\x11").decode(), "not SCTE-35"),
        (SPLICE_INSERT_OUT[:20], "truncated"),
    ],
)
def test_invalid_payloads_raise(payload, message):
    with pytest.raises(scte35.Scte35Error, match=message):
        scte35.decode(payload)


def test_splice_null_decodes():
    # table_id, flags+section_length(17), protocol, pts_adjustment, cw_index,
    # tier(0xFFF)+command_length(0), command_type(splice_null), descriptor_loop_length(0)
    section = bytes.fromhex("FC301100000000000000FFF000000000")
    crc = scte35.crc32_mpeg2(section).to_bytes(4, "big")
    info = scte35.decode(section + crc)
    assert info["splice_command_name"] == "splice_null"
    assert info["crc_valid"] is True
    assert info["warnings"] == []
    assert scte35.is_ad_start(info) is None
