"""SCTE-35 splice_info_section decoder.

Implements the parts of ANSI/SCTE 35 that show up in real ad-insertion
workflows: splice_null, splice_insert, time_signal, bandwidth_reservation and
private_command, plus avail_descriptor and segmentation_descriptor.

No third-party dependencies: a small bit reader walks the section exactly as
the spec's syntax tables describe it.
"""

from __future__ import annotations

import base64
import binascii
import re
from typing import Any

PTS_HZ = 90_000

COMMAND_TYPES = {
    0x00: "splice_null",
    0x04: "splice_schedule",
    0x05: "splice_insert",
    0x06: "time_signal",
    0x07: "bandwidth_reservation",
    0xFF: "private_command",
}

DESCRIPTOR_TAGS = {
    0x00: "avail_descriptor",
    0x01: "dtmf_descriptor",
    0x02: "segmentation_descriptor",
    0x03: "time_descriptor",
    0x04: "audio_descriptor",
}

# SCTE 35 Table 23 (segmentation_type_id). Subset covering ad and program signalling.
SEGMENTATION_TYPES = {
    0x00: "Not Indicated",
    0x01: "Content Identification",
    0x10: "Program Start",
    0x11: "Program End",
    0x12: "Program Early Termination",
    0x13: "Program Breakaway",
    0x14: "Program Resumption",
    0x15: "Program Runover Planned",
    0x16: "Program Runover Unplanned",
    0x17: "Program Overlap Start",
    0x18: "Program Blackout Override",
    0x19: "Program Join",
    0x20: "Chapter Start",
    0x21: "Chapter End",
    0x22: "Break Start",
    0x23: "Break End",
    0x24: "Opening Credit Start",
    0x25: "Opening Credit End",
    0x26: "Closing Credit Start",
    0x27: "Closing Credit End",
    0x30: "Provider Advertisement Start",
    0x31: "Provider Advertisement End",
    0x32: "Distributor Advertisement Start",
    0x33: "Distributor Advertisement End",
    0x34: "Provider Placement Opportunity Start",
    0x35: "Provider Placement Opportunity End",
    0x36: "Distributor Placement Opportunity Start",
    0x37: "Distributor Placement Opportunity End",
    0x38: "Provider Overlay Placement Opportunity Start",
    0x39: "Provider Overlay Placement Opportunity End",
    0x3A: "Distributor Overlay Placement Opportunity Start",
    0x3B: "Distributor Overlay Placement Opportunity End",
    0x3C: "Provider Promo Start",
    0x3D: "Provider Promo End",
    0x3E: "Distributor Promo Start",
    0x3F: "Distributor Promo End",
    0x40: "Unscheduled Event Start",
    0x41: "Unscheduled Event End",
    0x42: "Alternate Content Opportunity Start",
    0x43: "Alternate Content Opportunity End",
    0x44: "Provider Ad Block Start",
    0x45: "Provider Ad Block End",
    0x46: "Distributor Ad Block Start",
    0x47: "Distributor Ad Block End",
    0x50: "Network Start",
    0x51: "Network End",
}

# Segmentation types that carry sub_segment_num / sub_segments_expected.
_SUB_SEGMENT_TYPES = {0x34, 0x36, 0x38, 0x3A, 0x44, 0x46}

# Types that open an ad/placement window (used by callers to classify cues).
AD_START_TYPES = {0x22, 0x30, 0x32, 0x34, 0x36, 0x38, 0x3A, 0x44, 0x46}
AD_END_TYPES = {0x23, 0x31, 0x33, 0x35, 0x37, 0x39, 0x3B, 0x45, 0x47}

UPID_TYPES = {
    0x00: "Not Used",
    0x01: "User Defined (deprecated)",
    0x02: "ISCI (deprecated)",
    0x03: "Ad-ID",
    0x04: "UMID",
    0x05: "ISAN (deprecated)",
    0x06: "ISAN",
    0x07: "TID",
    0x08: "TI",
    0x09: "ADI",
    0x0A: "EIDR",
    0x0B: "ATSC Content Identifier",
    0x0C: "MPU",
    0x0D: "MID",
    0x0E: "ADS Information",
    0x0F: "URI",
    0x10: "UUID",
    0x11: "SCR",
}

_TEXT_UPIDS = {0x02, 0x03, 0x09, 0x0E, 0x0F}


class Scte35Error(ValueError):
    """Raised when a payload is not a decodable SCTE-35 splice_info_section."""


class _BitReader:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0  # bit position

    @property
    def bits_left(self) -> int:
        return len(self._data) * 8 - self._pos

    @property
    def byte_pos(self) -> int:
        return self._pos // 8

    def read(self, n: int) -> int:
        if n > self.bits_left:
            raise Scte35Error(
                f"payload truncated: needed {n} bits at byte {self.byte_pos}, "
                f"only {self.bits_left} left"
            )
        value = 0
        for _ in range(n):
            byte = self._data[self._pos // 8]
            bit = (byte >> (7 - self._pos % 8)) & 1
            value = (value << 1) | bit
            self._pos += 1
        return value

    def flag(self) -> bool:
        return bool(self.read(1))

    def skip(self, n: int) -> None:
        self.read(n)

    def read_bytes(self, n: int) -> bytes:
        if self._pos % 8:
            raise Scte35Error("unaligned byte read")
        if n * 8 > self.bits_left:
            raise Scte35Error(f"payload truncated: needed {n} bytes at byte {self.byte_pos}")
        start = self._pos // 8
        self._pos += n * 8
        return self._data[start : start + n]


def crc32_mpeg2(data: bytes) -> int:
    """CRC-32/MPEG-2 (poly 0x04C11DB7, init 0xFFFFFFFF, no reflection, no xor-out)."""
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= byte << 24
        for _ in range(8):
            crc = ((crc << 1) ^ 0x04C11DB7) if crc & 0x80000000 else (crc << 1)
            crc &= 0xFFFFFFFF
    return crc


def to_bytes(payload: str | bytes) -> bytes:
    """Accept base64, hex ("0xFC30..." or "FC30...") or raw bytes."""
    if isinstance(payload, bytes):
        return payload
    text = payload.strip().strip('"')
    if not text:
        raise Scte35Error("empty SCTE-35 payload")
    hex_candidate = text[2:] if text.lower().startswith("0x") else text
    if re.fullmatch(r"[0-9a-fA-F]+", hex_candidate) and len(hex_candidate) % 2 == 0:
        # Pure hex that also starts with the SCTE-35 table_id (0xFC) is unambiguous.
        if text.lower().startswith("0x") or hex_candidate[:2].lower() == "fc":
            return bytes.fromhex(hex_candidate)
    try:
        return base64.b64decode(text + "=" * (-len(text) % 4), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise Scte35Error("payload is neither valid base64 nor hex") from exc


def _seconds(ticks: int) -> float:
    return round(ticks / PTS_HZ, 6)


def _splice_time(r: _BitReader) -> dict[str, Any]:
    if r.flag():  # time_specified_flag
        r.skip(6)
        pts = r.read(33)
        return {"time_specified": True, "pts_time": pts, "pts_seconds": _seconds(pts)}
    r.skip(7)
    return {"time_specified": False}


def _break_duration(r: _BitReader) -> dict[str, Any]:
    auto_return = r.flag()
    r.skip(6)
    duration = r.read(33)
    return {
        "auto_return": auto_return,
        "duration": duration,
        "duration_seconds": _seconds(duration),
    }


def _splice_insert(r: _BitReader) -> dict[str, Any]:
    cmd: dict[str, Any] = {"splice_event_id": r.read(32)}
    cmd["splice_event_cancel_indicator"] = r.flag()
    r.skip(7)
    if cmd["splice_event_cancel_indicator"]:
        return cmd
    cmd["out_of_network_indicator"] = r.flag()
    cmd["program_splice_flag"] = r.flag()
    cmd["duration_flag"] = r.flag()
    cmd["splice_immediate_flag"] = r.flag()
    r.skip(4)  # event_id_compliance_flag + reserved
    if cmd["program_splice_flag"] and not cmd["splice_immediate_flag"]:
        cmd["splice_time"] = _splice_time(r)
    if not cmd["program_splice_flag"]:
        components = []
        for _ in range(r.read(8)):
            comp: dict[str, Any] = {"component_tag": r.read(8)}
            if not cmd["splice_immediate_flag"]:
                comp["splice_time"] = _splice_time(r)
            components.append(comp)
        cmd["components"] = components
    if cmd["duration_flag"]:
        cmd["break_duration"] = _break_duration(r)
    cmd["unique_program_id"] = r.read(16)
    cmd["avail_num"] = r.read(8)
    cmd["avails_expected"] = r.read(8)
    return cmd


def _decode_upid(upid_type: int, raw: bytes) -> str:
    if upid_type in _TEXT_UPIDS:
        try:
            return raw.decode("ascii")
        except UnicodeDecodeError:
            pass
    return "0x" + raw.hex()


def _segmentation_descriptor(r: _BitReader, end_byte: int) -> dict[str, Any]:
    d: dict[str, Any] = {"segmentation_event_id": r.read(32)}
    d["segmentation_event_cancel_indicator"] = r.flag()
    r.skip(7)
    if d["segmentation_event_cancel_indicator"]:
        return d
    d["program_segmentation_flag"] = r.flag()
    d["segmentation_duration_flag"] = r.flag()
    d["delivery_not_restricted_flag"] = r.flag()
    if not d["delivery_not_restricted_flag"]:
        d["web_delivery_allowed_flag"] = r.flag()
        d["no_regional_blackout_flag"] = r.flag()
        d["archive_allowed_flag"] = r.flag()
        d["device_restrictions"] = r.read(2)
    else:
        r.skip(5)
    if not d["program_segmentation_flag"]:
        comps = []
        for _ in range(r.read(8)):
            tag = r.read(8)
            r.skip(7)
            comps.append({"component_tag": tag, "pts_offset": r.read(33)})
        d["components"] = comps
    if d["segmentation_duration_flag"]:
        dur = r.read(40)
        d["segmentation_duration"] = dur
        d["segmentation_duration_seconds"] = _seconds(dur)
    upid_type = r.read(8)
    upid_len = r.read(8)
    upid_raw = r.read_bytes(upid_len)
    d["segmentation_upid_type"] = upid_type
    d["segmentation_upid_type_name"] = UPID_TYPES.get(upid_type, "Reserved")
    d["segmentation_upid"] = _decode_upid(upid_type, upid_raw) if upid_len else ""
    type_id = r.read(8)
    d["segmentation_type_id"] = type_id
    d["segmentation_type_name"] = SEGMENTATION_TYPES.get(type_id, "Reserved")
    d["segment_num"] = r.read(8)
    d["segments_expected"] = r.read(8)
    if type_id in _SUB_SEGMENT_TYPES and end_byte - r.byte_pos >= 2:
        d["sub_segment_num"] = r.read(8)
        d["sub_segments_expected"] = r.read(8)
    return d


def _descriptors(r: _BitReader, loop_length: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    loop_end = r.byte_pos + loop_length
    while r.byte_pos < loop_end:
        tag = r.read(8)
        length = r.read(8)
        end = r.byte_pos + length
        entry: dict[str, Any] = {
            "tag": tag,
            "name": DESCRIPTOR_TAGS.get(tag, "unknown_descriptor"),
            "length": length,
        }
        identifier = r.read_bytes(4)
        entry["identifier"] = identifier.decode("ascii", errors="replace")
        if tag == 0x00:
            entry["provider_avail_id"] = r.read(32)
        elif tag == 0x02:
            entry.update(_segmentation_descriptor(r, end))
        # Skip anything left (unknown descriptors, private bytes, stuffing).
        remaining = end - r.byte_pos
        if remaining < 0:
            raise Scte35Error(f"descriptor 0x{tag:02x} overran its declared length")
        if remaining:
            raw = r.read_bytes(remaining)
            if tag not in (0x00, 0x02):
                entry["private_bytes"] = "0x" + raw.hex()
        out.append(entry)
    return out


def decode(payload: str | bytes) -> dict[str, Any]:
    """Decode a SCTE-35 splice_info_section from base64, hex or bytes."""
    data = to_bytes(payload)
    r = _BitReader(data)
    table_id = r.read(8)
    if table_id != 0xFC:
        raise Scte35Error(f"table_id is 0x{table_id:02x}, expected 0xfc (not SCTE-35)")
    info: dict[str, Any] = {"table_id": "0xfc"}
    info["section_syntax_indicator"] = r.flag()
    info["private_indicator"] = r.flag()
    info["sap_type"] = r.read(2)
    section_length = r.read(12)
    info["section_length"] = section_length
    info["protocol_version"] = r.read(8)
    info["encrypted_packet"] = r.flag()
    info["encryption_algorithm"] = r.read(6)
    pts_adjustment = r.read(33)
    info["pts_adjustment"] = pts_adjustment
    info["cw_index"] = r.read(8)
    info["tier"] = r.read(12)
    command_length = r.read(12)
    info["splice_command_length"] = command_length
    command_type = r.read(8)
    info["splice_command_type"] = command_type
    info["splice_command_name"] = COMMAND_TYPES.get(command_type, "reserved")

    warnings: list[str] = []
    if 3 + section_length != len(data):
        warnings.append(
            f"section_length says {3 + section_length} bytes but payload has {len(data)}"
        )
    if info["encrypted_packet"]:
        info["warnings"] = warnings + ["encrypted packet: command and descriptors not decoded"]
        return info

    command_start = r.byte_pos
    command: dict[str, Any] = {}
    if command_type == 0x05:
        command = _splice_insert(r)
    elif command_type == 0x06:
        command = _splice_time(r)
    elif command_type == 0xFF:
        command = {"identifier": r.read(32)}
    elif command_type in (0x00, 0x07):
        command = {}
    else:
        warnings.append(f"{info['splice_command_name']} (0x{command_type:02x}) body not decoded")
        if command_length != 0xFFF:
            r.read_bytes(command_length)
    # Respect the declared command length (skip any unparsed command bytes).
    if command_length != 0xFFF:
        consumed = r.byte_pos - command_start
        if consumed < command_length:
            r.read_bytes(command_length - consumed)
        elif consumed > command_length and command_type in (0x05, 0x06):
            warnings.append(
                f"splice_command_length is {command_length} but command used {consumed} bytes"
            )
    info["splice_command"] = command

    descriptor_loop_length = r.read(16)
    info["descriptor_loop_length"] = descriptor_loop_length
    info["descriptors"] = _descriptors(r, descriptor_loop_length)

    # Skip alignment stuffing, then CRC_32 is the final 4 bytes of the section.
    section_end = min(3 + section_length, len(data))
    if section_end >= 4:
        crc_stored = int.from_bytes(data[section_end - 4 : section_end], "big")
        crc_calc = crc32_mpeg2(data[: section_end - 4])
        info["crc_32"] = f"0x{crc_stored:08x}"
        info["crc_valid"] = crc_stored == crc_calc
        if not info["crc_valid"]:
            warnings.append(f"CRC mismatch: stored 0x{crc_stored:08x}, computed 0x{crc_calc:08x}")

    # Convenience: effective splice time in seconds (pts_time + pts_adjustment, 33-bit wrap).
    splice_time = command.get("splice_time") if command_type == 0x05 else command
    if splice_time and splice_time.get("time_specified"):
        adjusted = (splice_time["pts_time"] + pts_adjustment) % (1 << 33)
        info["splice_time_seconds"] = _seconds(adjusted)

    info["summary"] = summarize(info)
    info["warnings"] = warnings
    return info


def summarize(info: dict[str, Any]) -> str:
    """One-line human summary of a decoded cue, for the LLM and for humans."""
    name = info.get("splice_command_name", "?")
    cmd = info.get("splice_command", {}) or {}
    parts = [name]
    if name == "splice_insert":
        if cmd.get("splice_event_cancel_indicator"):
            return f"splice_insert CANCEL for event {cmd.get('splice_event_id')}"
        parts.append("OUT (ad start)" if cmd.get("out_of_network_indicator") else "IN (return)")
        if "break_duration" in cmd:
            parts.append(f"duration {cmd['break_duration']['duration_seconds']}s")
        if cmd.get("splice_immediate_flag"):
            parts.append("immediate")
    for d in info.get("descriptors", []):
        if d.get("name") == "segmentation_descriptor" and "segmentation_type_name" in d:
            seg = d["segmentation_type_name"]
            if "segmentation_duration_seconds" in d:
                seg += f" ({d['segmentation_duration_seconds']}s)"
            parts.append(seg)
    if "splice_time_seconds" in info:
        parts.append(f"at PTS {info['splice_time_seconds']}s")
    return ", ".join(parts)


def is_ad_start(info: dict[str, Any]) -> bool | None:
    """True for an out/ad-start cue, False for an in/ad-end cue, None if neither."""
    cmd = info.get("splice_command") or {}
    if info.get("splice_command_name") == "splice_insert" and not cmd.get(
        "splice_event_cancel_indicator"
    ):
        return bool(cmd.get("out_of_network_indicator"))
    for d in info.get("descriptors", []):
        type_id = d.get("segmentation_type_id")
        if type_id in AD_START_TYPES:
            return True
        if type_id in AD_END_TYPES:
            return False
    return None


def cue_duration_seconds(info: dict[str, Any]) -> float | None:
    """Break duration signalled by the cue, from break_duration or the segmentation descriptor."""
    cmd = info.get("splice_command") or {}
    if "break_duration" in cmd:
        return cmd["break_duration"]["duration_seconds"]
    for d in info.get("descriptors", []):
        if "segmentation_duration_seconds" in d:
            return d["segmentation_duration_seconds"]
    return None
