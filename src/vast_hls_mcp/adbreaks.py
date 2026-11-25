"""Find SCTE-35 ad breaks in an HLS media playlist.

Understands the three marker styles used by SSAI vendors and packagers:

* ``#EXT-X-CUE-OUT`` / ``#EXT-X-CUE-OUT-CONT`` / ``#EXT-X-CUE-IN`` (Adobe/Elemental style)
* ``#EXT-X-DATERANGE`` with ``SCTE35-OUT`` / ``SCTE35-IN`` (RFC 8216 style)
* ``#EXT-OATCLS-SCTE35`` / ``#EXT-X-SCTE35`` carrying the raw base64 cue

For each break it reports where it starts on the playlist timeline, the planned
duration (from the tag or the decoded cue) and the actual duration covered by
segments, then flags the problems that break ad insertion.
"""

from __future__ import annotations

import re
from typing import Any

from . import scte35
from .hls import lines_of, parse_attributes

DURATION_TOLERANCE = 0.5  # seconds


def _number(value: str | None) -> float | None:
    if value is None:
        return None
    m = re.search(r"[-+]?\d+(\.\d+)?", value)
    return float(m.group()) if m else None


def _cue_out_duration(value: str) -> float | None:
    if not value:
        return None
    attrs = parse_attributes(value)
    if "DURATION" in attrs:
        return _number(attrs["DURATION"])
    return _number(value)


def _cue_cont(value: str) -> tuple[float | None, float | None]:
    """Return (elapsed, duration) from CUE-OUT-CONT in either '5/30' or attr form."""
    if "/" in value and "=" not in value:
        a, b = value.split("/", 1)
        return _number(a), _number(b)
    attrs = parse_attributes(value)
    return _number(attrs.get("ELAPSEDTIME")), _number(attrs.get("DURATION"))


def _decode(payload: str) -> dict[str, Any]:
    try:
        info = scte35.decode(payload)
    except scte35.Scte35Error as exc:
        return {"decode_error": str(exc)}
    return {
        "summary": info["summary"],
        "is_ad_start": scte35.is_ad_start(info),
        "duration_seconds": scte35.cue_duration_seconds(info),
        "crc_valid": info.get("crc_valid"),
    }


def find(text: str) -> dict[str, Any]:
    lines = lines_of(text)
    t = 0.0
    breaks: list[dict[str, Any]] = []
    issues: list[str] = []
    current: dict[str, Any] | None = None
    pending_cue: dict[str, Any] | None = None
    dateranges: dict[str, dict[str, Any]] = {}
    pending_duration: float | None = None
    segments_total = 0

    def open_break(style: str, planned: float | None, start: float, **extra) -> dict[str, Any]:
        brk: dict[str, Any] = {
            "index": len(breaks) + 1,
            "style": style,
            "start_seconds": round(start, 3),
            "planned_duration_seconds": planned,
            "actual_duration_seconds": 0.0,
            "segments": 0,
            "closed": False,
            **extra,
        }
        breaks.append(brk)
        return brk

    for line in lines[1:]:
        name, _, value = line.partition(":")
        if name in ("#EXT-OATCLS-SCTE35", "#EXT-X-SCTE35"):
            payload = (
                parse_attributes(value).get("CUE", value) if name == "#EXT-X-SCTE35" else value
            )
            pending_cue = _decode(payload)
        elif name == "#EXT-X-CUE-OUT":
            if current is not None:
                issues.append(
                    f"CUE-OUT at {t:.3f}s while break {current['index']} is still open "
                    "(missing CUE-IN)"
                )
                current["closed"] = False
            current = open_break("cue-out", _cue_out_duration(value), t)
            if pending_cue:
                current["scte35"] = pending_cue
                if current["planned_duration_seconds"] is None:
                    current["planned_duration_seconds"] = pending_cue.get("duration_seconds")
                pending_cue = None
        elif name == "#EXT-X-CUE-OUT-CONT":
            elapsed, duration = _cue_cont(value)
            if current is None:
                start = t - (elapsed or 0.0)
                current = open_break(
                    "cue-out",
                    duration,
                    start,
                    joined_mid_break=True,
                )
                current["actual_duration_seconds"] = round(elapsed or 0.0, 3)
            elif elapsed is not None and abs(elapsed - current["actual_duration_seconds"]) > 1.0:
                issues.append(
                    f"break {current['index']}: CUE-OUT-CONT ElapsedTime {elapsed}s but "
                    f"segments so far cover {current['actual_duration_seconds']}s"
                )
        elif name == "#EXT-X-CUE-IN":
            if current is None:
                issues.append(f"CUE-IN at {t:.3f}s without a matching CUE-OUT")
            else:
                current["closed"] = True
                current["end_seconds"] = round(t, 3)
                current = None
        elif name == "#EXT-X-DATERANGE":
            a = parse_attributes(value)
            dr_id = a.get("ID", f"daterange-{len(dateranges) + 1}")
            if "SCTE35-OUT" in a:
                brk = open_break(
                    "daterange",
                    _number(a.get("PLANNED-DURATION")) or _number(a.get("DURATION")),
                    t,
                    id=dr_id,
                    start_date=a.get("START-DATE"),
                    scte35=_decode(a["SCTE35-OUT"]),
                )
                if brk["planned_duration_seconds"] is None:
                    brk["planned_duration_seconds"] = brk["scte35"].get("duration_seconds")
                dateranges[dr_id] = brk
            elif "SCTE35-IN" in a or ("DURATION" in a and dr_id in dateranges):
                brk = dateranges.get(dr_id)
                if brk is None:
                    issues.append(f"DATERANGE {dr_id!r} SCTE35-IN without a matching SCTE35-OUT")
                else:
                    brk["closed"] = True
                    brk["end_seconds"] = round(t, 3)
                    if "DURATION" in a:
                        brk["signalled_duration_seconds"] = _number(a["DURATION"])
        elif name == "#EXTINF":
            pending_duration = _number(value.split(",", 1)[0]) or 0.0
        elif not line.startswith("#"):
            d = pending_duration or 0.0
            segments_total += 1
            for brk in breaks:
                in_daterange = brk["style"] == "daterange" and not brk["closed"]
                if brk is current or in_daterange:
                    brk["actual_duration_seconds"] = round(brk["actual_duration_seconds"] + d, 3)
                    brk["segments"] += 1
            t += d
            pending_duration = None

    for brk in breaks:
        planned = brk["planned_duration_seconds"]
        actual = brk["actual_duration_seconds"]
        if not brk["closed"]:
            issues.append(
                f"break {brk['index']} at {brk['start_seconds']}s never closes "
                "(no CUE-IN / SCTE35-IN; fine for a live edge, a bug for VOD)"
            )
        elif planned is not None and abs(planned - actual) > DURATION_TOLERANCE:
            issues.append(
                f"break {brk['index']}: planned {planned}s but segments cover {actual}s "
                f"({actual - planned:+.3f}s); viewers see filler/slate or a truncated ad"
            )
        cue = brk.get("scte35") or {}
        if cue.get("decode_error"):
            issues.append(
                f"break {brk['index']}: SCTE-35 payload did not decode: {cue['decode_error']}"
            )
        elif cue.get("crc_valid") is False:
            issues.append(f"break {brk['index']}: SCTE-35 CRC mismatch (corrupted cue)")
        cue_dur = cue.get("duration_seconds")
        if (
            cue_dur is not None
            and planned is not None
            and abs(cue_dur - planned) > DURATION_TOLERANCE
        ):
            issues.append(
                f"break {brk['index']}: tag says {planned}s but the SCTE-35 cue says {cue_dur}s"
            )

    return {
        "break_count": len(breaks),
        "total_ad_seconds": round(sum(b["actual_duration_seconds"] for b in breaks), 3),
        "playlist_seconds": round(t, 3),
        "segment_count": segments_total,
        "breaks": breaks,
        "issues": issues,
        "healthy": not issues,
    }
