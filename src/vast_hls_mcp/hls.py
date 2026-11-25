"""HLS (RFC 8216) master and media playlist inspection."""

from __future__ import annotations

import math
import re
from typing import Any

_ATTR_RE = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')


class HlsError(ValueError):
    """The document is not an HLS playlist."""


def parse_attributes(value: str) -> dict[str, str]:
    """Parse an HLS attribute-list (handles quoted values containing commas)."""
    return {k: v.strip('"') for k, v in _ATTR_RE.findall(value)}


def lines_of(text: str) -> list[str]:
    lines = [ln.strip() for ln in text.lstrip("﻿").splitlines()]
    lines = [ln for ln in lines if ln]
    if not lines or lines[0] != "#EXTM3U":
        raise HlsError("not an HLS playlist: first line must be #EXTM3U")
    return lines


def _tag(line: str) -> tuple[str, str]:
    if ":" in line:
        name, value = line.split(":", 1)
        return name, value
    return line, ""


def is_master(lines: list[str]) -> bool:
    return any(ln.startswith("#EXT-X-STREAM-INF") for ln in lines)


def inspect_master(lines: list[str]) -> dict[str, Any]:
    variants: list[dict[str, Any]] = []
    renditions: list[dict[str, Any]] = []
    iframe_variants = 0
    warnings: list[str] = []
    pending: dict[str, str] | None = None
    for line in lines[1:]:
        name, value = _tag(line)
        if name == "#EXT-X-STREAM-INF":
            pending = parse_attributes(value)
        elif name == "#EXT-X-MEDIA":
            a = parse_attributes(value)
            renditions.append(
                {
                    k.lower().replace("-", "_"): v
                    for k, v in a.items()
                    if k in ("TYPE", "GROUP-ID", "NAME", "LANGUAGE", "DEFAULT", "AUTOSELECT", "URI")
                }
            )
        elif name == "#EXT-X-I-FRAME-STREAM-INF":
            iframe_variants += 1
        elif not line.startswith("#") and pending is not None:
            v: dict[str, Any] = {"uri": line}
            if "BANDWIDTH" in pending:
                v["bandwidth"] = int(pending["BANDWIDTH"])
            if "AVERAGE-BANDWIDTH" in pending:
                v["average_bandwidth"] = int(pending["AVERAGE-BANDWIDTH"])
            for key in ("RESOLUTION", "CODECS", "FRAME-RATE", "AUDIO", "SUBTITLES"):
                if key in pending:
                    v[key.lower().replace("-", "_")] = pending[key]
            variants.append(v)
            pending = None

    groups = {(r.get("type"), r.get("group_id")) for r in renditions}
    for i, v in enumerate(variants, start=1):
        if "bandwidth" not in v:
            warnings.append(f"variant {i} ({v['uri']}) has no BANDWIDTH (required)")
        if "codecs" not in v:
            warnings.append(f"variant {i} ({v['uri']}) has no CODECS (players may refuse it)")
        if "audio" in v and ("AUDIO", v["audio"]) not in groups:
            warnings.append(
                f"variant {i} references AUDIO group {v['audio']!r} that is not defined"
            )
        if "subtitles" in v and ("SUBTITLES", v["subtitles"]) not in groups:
            warnings.append(
                f"variant {i} references SUBTITLES group {v['subtitles']!r} that is not defined"
            )
    bandwidths = [v["bandwidth"] for v in variants if "bandwidth" in v]
    if len(variants) == 1:
        warnings.append("only one variant: no adaptive bitrate switching")
    if iframe_variants == 0 and variants:
        warnings.append("no I-frame playlists (#EXT-X-I-FRAME-STREAM-INF): no trick-play/scrubbing")
    return {
        "playlist_type": "master",
        "variant_count": len(variants),
        "bandwidth_range": [min(bandwidths), max(bandwidths)] if bandwidths else None,
        "variants": sorted(variants, key=lambda v: v.get("bandwidth", 0)),
        "renditions": renditions,
        "iframe_variants": iframe_variants,
        "warnings": warnings,
    }


def inspect_media(lines: list[str], max_segments: int = 20) -> dict[str, Any]:
    target: int | None = None
    media_sequence = 0
    playlist_kind = None
    endlist = False
    segments: list[dict[str, Any]] = []
    discontinuities = 0
    encryption: set[str] = set()
    has_pdt = False
    errors: list[str] = []
    warnings: list[str] = []
    pending_duration: float | None = None
    pending_discontinuity = False
    for line in lines[1:]:
        name, value = _tag(line)
        if name == "#EXT-X-TARGETDURATION":
            target = int(value)
        elif name == "#EXT-X-MEDIA-SEQUENCE":
            media_sequence = int(value)
        elif name == "#EXT-X-PLAYLIST-TYPE":
            playlist_kind = value
        elif name == "#EXT-X-ENDLIST":
            endlist = True
        elif name == "#EXT-X-DISCONTINUITY":
            discontinuities += 1
            pending_discontinuity = True
        elif name == "#EXT-X-KEY":
            encryption.add(parse_attributes(value).get("METHOD", "?"))
        elif name == "#EXT-X-PROGRAM-DATE-TIME":
            has_pdt = True
        elif name == "#EXTINF":
            try:
                pending_duration = float(value.split(",", 1)[0])
            except ValueError:
                errors.append(f"bad #EXTINF value {value!r}")
                pending_duration = 0.0
        elif not line.startswith("#"):
            if pending_duration is None:
                errors.append(f"segment {line!r} has no preceding #EXTINF")
                pending_duration = 0.0
            seg = {"uri": line, "duration": pending_duration}
            if pending_discontinuity:
                seg["discontinuity"] = True
            segments.append(seg)
            pending_duration = None
            pending_discontinuity = False

    if target is None:
        errors.append("missing #EXT-X-TARGETDURATION (required)")
    else:
        too_long = [s for s in segments if math.floor(s["duration"] + 0.5) > target]
        if too_long:
            warnings.append(
                f"{len(too_long)} segment(s) exceed EXT-X-TARGETDURATION {target}s "
                f"(longest {max(s['duration'] for s in too_long)}s); RFC 8216 violation"
            )
    total = round(sum(s["duration"] for s in segments), 3)
    is_live = not endlist and playlist_kind != "VOD"
    if playlist_kind == "VOD" and not endlist:
        warnings.append("PLAYLIST-TYPE is VOD but #EXT-X-ENDLIST is missing")
    if is_live and not has_pdt:
        warnings.append("live playlist without #EXT-X-PROGRAM-DATE-TIME (hard to sync ads/EPG)")
    if "NONE" in encryption:
        encryption.discard("NONE")
    return {
        "playlist_type": "media",
        "live": is_live,
        "playlist_kind": playlist_kind,
        "target_duration": target,
        "media_sequence": media_sequence,
        "segment_count": len(segments),
        "total_duration_seconds": total,
        "discontinuities": discontinuities,
        "encryption": sorted(encryption) or None,
        "program_date_time": has_pdt,
        "segments_preview": segments[:max_segments],
        "errors": errors,
        "warnings": warnings,
    }


def inspect(text: str) -> dict[str, Any]:
    lines = lines_of(text)
    result = inspect_master(lines) if is_master(lines) else inspect_media(lines)
    version = next((ln.split(":", 1)[1] for ln in lines if ln.startswith("#EXT-X-VERSION:")), None)
    result["hls_version"] = int(version) if version and version.isdigit() else None
    return result
