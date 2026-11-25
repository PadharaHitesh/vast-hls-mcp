"""VAST 2.0 – 4.3 parsing, validation and wrapper unwrapping."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from typing import Any

from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException

from .fetch import FetchError, FetchResult, fetch_text

KNOWN_VERSIONS = {"2.0", "3.0", "4.0", "4.1", "4.2", "4.3"}
DURATION_RE = re.compile(r"^\d{2}:[0-5]\d:[0-5]\d(\.\d{1,3})?$")
MAX_WRAPPER_DEPTH = 5


class VastError(ValueError):
    """The document is not parseable VAST XML."""


def _strip_ns(root: ET.Element) -> ET.Element:
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def parse_xml(text: str) -> ET.Element:
    text = text.strip().lstrip("﻿")
    if not text:
        raise VastError("empty document")
    try:
        root = SafeET.fromstring(text)
    except DefusedXmlException as exc:
        raise VastError(f"unsafe XML rejected ({type(exc).__name__})") from exc
    except ET.ParseError as exc:
        raise VastError(f"XML parse error: {exc}") from exc
    return _strip_ns(root)


def _text(el: ET.Element | None) -> str:
    return (el.text or "").strip() if el is not None else ""


def duration_to_seconds(value: str) -> float | None:
    if not DURATION_RE.match(value):
        return None
    h, m, s = value.split(":")
    return round(int(h) * 3600 + int(m) * 60 + float(s), 3)


def _major(version: str) -> int:
    try:
        return int(float(version))
    except ValueError:
        return 0


def _parse_linear(linear: ET.Element, where: str, version: str, issues: dict) -> dict[str, Any]:
    out: dict[str, Any] = {"type": "linear"}
    duration = _text(linear.find("Duration"))
    out["duration"] = duration
    if not duration:
        issues["errors"].append(f"{where}: Linear creative has no <Duration>")
    else:
        secs = duration_to_seconds(duration)
        if secs is None:
            issues["errors"].append(
                f"{where}: Duration {duration!r} is not HH:MM:SS or HH:MM:SS.mmm"
            )
        out["duration_seconds"] = secs
    skip = linear.get("skipoffset")
    if skip:
        out["skipoffset"] = skip

    media_files = []
    for mf in linear.findall("MediaFiles/MediaFile"):
        url = _text(mf)
        entry = {
            "url": url,
            "delivery": mf.get("delivery"),
            "type": mf.get("type"),
            "width": mf.get("width"),
            "height": mf.get("height"),
            "bitrate": mf.get("bitrate"),
            "codec": mf.get("codec"),
        }
        media_files.append({k: v for k, v in entry.items() if v})
        missing = [a for a in ("delivery", "type", "width", "height") if not mf.get(a)]
        if missing:
            issues["warnings"].append(
                f"{where}: MediaFile missing required attribute(s) {', '.join(missing)}"
            )
        if url.lower().startswith("http://"):
            issues["warnings"].append(
                f"{where}: MediaFile uses http:// (blocked by HTTPS players and most CTV apps)"
            )
        if not url:
            issues["errors"].append(f"{where}: MediaFile has no URL")
    out["media_files"] = media_files

    interactive = linear.findall("MediaFiles/InteractiveCreativeFile")
    if interactive:
        out["interactive_creative_files"] = len(interactive)
    if not media_files:
        issues["errors"].append(f"{where}: Linear creative has no <MediaFile>")
    else:
        types = {m.get("type", "") for m in media_files}
        if not types & {"video/mp4", "application/x-mpegURL", "application/vnd.apple.mpegurl"}:
            issues["warnings"].append(
                f"{where}: no MP4 or HLS MediaFile; many CTV/OTT players will not play it"
            )
    if _major(version) >= 4 and not linear.findall("MediaFiles/Mezzanine"):
        issues["info"].append(f"{where}: no <Mezzanine> file (recommended for SSAI in VAST 4)")

    out["tracking_events"] = sorted(
        {t.get("event", "") for t in linear.findall("TrackingEvents/Tracking") if t.get("event")}
    )
    out["click_through"] = _text(linear.find("VideoClicks/ClickThrough")) or None
    return out


def _parse_creatives(ad_el: ET.Element, where: str, version: str, issues: dict) -> list[dict]:
    creatives = []
    for i, creative in enumerate(ad_el.findall("Creatives/Creative"), start=1):
        cwhere = f"{where} creative {i}"
        entry: dict[str, Any] = {"id": creative.get("id"), "ad_id": creative.get("adId")}
        uaid = creative.find("UniversalAdId")
        if uaid is not None:
            entry["universal_ad_id"] = {
                "registry": uaid.get("idRegistry"),
                "value": _text(uaid) or uaid.get("idValue"),
            }
        elif _major(version) >= 4 and ad_el.tag == "InLine":
            issues["warnings"].append(f"{cwhere}: VAST 4 requires <UniversalAdId>")
        linear = creative.find("Linear")
        if linear is not None:
            if ad_el.tag == "InLine":
                entry.update(_parse_linear(linear, cwhere, version, issues))
            else:
                entry["type"] = "linear"
                entry["tracking_events"] = sorted(
                    {t.get("event", "") for t in linear.findall("TrackingEvents/Tracking")} - {""}
                )
        elif creative.find("NonLinearAds") is not None:
            entry["type"] = "nonlinear"
        elif creative.find("CompanionAds") is not None:
            entry["type"] = "companion"
        else:
            entry["type"] = "unknown"
        creatives.append({k: v for k, v in entry.items() if v is not None})
    return creatives


def _parse_ad(ad: ET.Element, index: int, version: str, issues: dict) -> dict[str, Any]:
    where = f"Ad {index}" + (f" (id={ad.get('id')})" if ad.get("id") else "")
    inline = ad.find("InLine")
    wrapper = ad.find("Wrapper")
    if (inline is None) == (wrapper is None):
        issues["errors"].append(f"{where}: must contain exactly one of <InLine> or <Wrapper>")
        return {"index": index, "id": ad.get("id"), "kind": "invalid"}
    body = inline if inline is not None else wrapper
    kind = "inline" if inline is not None else "wrapper"
    out: dict[str, Any] = {
        "index": index,
        "id": ad.get("id"),
        "sequence": ad.get("sequence"),
        "kind": kind,
        "ad_system": _text(body.find("AdSystem")) or None,
        "impressions": [_text(i) for i in body.findall("Impression") if _text(i)],
        "error_urls": [_text(e) for e in body.findall("Error") if _text(e)],
    }
    if not out["ad_system"]:
        issues["errors"].append(f"{where}: missing <AdSystem>")
    if not out["impressions"]:
        issues["errors"].append(f"{where}: no <Impression> URL (impressions cannot be counted)")
    if kind == "inline":
        out["ad_title"] = _text(body.find("AdTitle")) or None
        if not out["ad_title"]:
            issues["errors"].append(f"{where}: missing <AdTitle>")
        if _major(version) >= 4 and version != "4.0" and body.find("AdServingId") is None:
            issues["warnings"].append(f"{where}: VAST {version} requires <AdServingId>")
        if body.find("Creatives/Creative") is None:
            issues["errors"].append(f"{where}: InLine ad has no <Creative>")
    else:
        out["vast_ad_tag_uri"] = _text(body.find("VASTAdTagURI")) or None
        if not out["vast_ad_tag_uri"]:
            issues["errors"].append(f"{where}: Wrapper has no <VASTAdTagURI>")
        for attr in ("followAdditionalWrappers", "allowMultipleAds", "fallbackOnNoAd"):
            if wrapper.get(attr) is not None:
                out[attr] = wrapper.get(attr)
    out["creatives"] = _parse_creatives(body, where, version, issues)
    return {k: v for k, v in out.items() if v not in (None, [])}


def analyze(text: str) -> dict[str, Any]:
    """Parse and validate one VAST document (no network)."""
    root = parse_xml(text)
    issues: dict[str, list[str]] = {"errors": [], "warnings": [], "info": []}
    if root.tag != "VAST":
        raise VastError(f"root element is <{root.tag}>, expected <VAST>")
    version = root.get("version", "")
    if not version:
        issues["errors"].append("<VAST> has no version attribute")
    elif version not in KNOWN_VERSIONS:
        issues["warnings"].append(f"unknown VAST version {version!r}")

    ads = [_parse_ad(ad, i, version, issues) for i, ad in enumerate(root.findall("Ad"), start=1)]
    root_errors = [_text(e) for e in root.findall("Error") if _text(e)]
    if not ads:
        issues["info"].append("no <Ad> elements: this is a no-fill response")
        if not root_errors:
            issues["warnings"].append("no-fill response has no root <Error> URL to report it")
    if len(ads) > 1 and any(a.get("sequence") for a in ads):
        issues["info"].append(f"ad pod with {len(ads)} ads")

    status = "invalid" if issues["errors"] else ("no_fill" if not ads else "valid")
    return {
        "status": status,
        "version": version or None,
        "ad_count": len(ads),
        "ads": ads,
        "root_error_urls": root_errors,
        **issues,
    }


Fetcher = Callable[[str], FetchResult]


def unwrap(url: str, fetcher: Fetcher = fetch_text, max_depth: int = MAX_WRAPPER_DEPTH) -> dict:
    """Follow a VAST wrapper chain from `url` down to the InLine ad."""
    hops: list[dict[str, Any]] = []
    seen: set[str] = set()
    current = url.strip()
    outcome = "error"
    reason = ""
    total_impressions = 0
    for depth in range(max_depth + 1):
        if current in seen:
            outcome, reason = "loop", f"wrapper chain loops back to {current}"
            break
        seen.add(current)
        hop: dict[str, Any] = {"depth": depth, "url": current}
        hops.append(hop)
        try:
            res = fetcher(current)
        except FetchError as exc:
            outcome, reason = "fetch_error", str(exc)
            hop["error"] = str(exc)
            break
        hop["status_code"] = res.status_code
        hop["elapsed_ms"] = res.elapsed_ms
        if res.status_code >= 400:
            outcome, reason = "http_error", f"HTTP {res.status_code}"
            break
        try:
            doc = analyze(res.text)
        except VastError as exc:
            outcome, reason = "parse_error", str(exc)
            hop["error"] = str(exc)
            break
        hop["version"] = doc["version"]
        hop["errors"] = doc["errors"]
        hop["warnings"] = doc["warnings"]
        if not doc["ads"]:
            outcome, reason = "no_fill", "a hop returned an empty VAST (no ad)"
            break
        ad = doc["ads"][0]
        hop["ad_system"] = ad.get("ad_system")
        hop["kind"] = ad.get("kind")
        hop["impressions"] = len(ad.get("impressions", []))
        total_impressions += hop["impressions"]
        if ad.get("kind") == "inline":
            outcome = "inline"
            hop["ad_title"] = ad.get("ad_title")
            hop["creatives"] = ad.get("creatives", [])
            break
        nxt = ad.get("vast_ad_tag_uri")
        if not nxt:
            outcome, reason = "error", "wrapper without VASTAdTagURI"
            break
        current = nxt
    else:
        outcome, reason = "too_deep", f"more than {max_depth} wrappers (players usually give up)"

    return {
        "outcome": outcome,
        "reason": reason or None,
        "depth": len(hops) - 1,
        "total_impression_urls": total_impressions,
        "total_elapsed_ms": sum(h.get("elapsed_ms", 0) for h in hops),
        "hops": hops,
    }
