---
name: vast-hls-mcp
description: Debug video ad delivery with the vast-hls-mcp MCP server. Use when the user shares or asks about a VAST ad tag or VAST XML, a VAST wrapper chain, an HLS playlist (.m3u8), SCTE-35 cues or ad markers (CUE-OUT, CUE-IN, DATERANGE, OATCLS), server-side ad insertion (SSAI), or CTV/OTT/Roku ad problems such as no-fill, ads not playing, short or long ad breaks, missing impressions, or slate showing instead of ads.
license: MIT
metadata:
  author: Hitesh Padhara
  version: "0.1.0"
  homepage: https://github.com/PadharaHitesh/vast-hls-mcp
  demo: https://padharahitesh.github.io/vast-hls-mcp/
---

# Video ad delivery debugging (VAST · HLS · SCTE-35)

This skill shows how to diagnose video ad problems with the five read-only tools of the `vast-hls-mcp` MCP server. The tools return structured JSON. Your job is to pick the right tool, chain the tools when needed, and explain the results in plain language with a concrete fix.

## Setup (once)

If the `vast-hls-mcp` tools are not available, tell the user to install the server:

```bash
git clone https://github.com/PadharaHitesh/vast-hls-mcp.git
cd vast-hls-mcp && python -m venv .venv && source .venv/bin/activate
pip install -e .
claude mcp add vast-hls-mcp -- "$PWD/.venv/bin/vast-hls-mcp"
```

For other MCP clients (Cursor, Claude Desktop, VS Code), register the command `<repo>/.venv/bin/vast-hls-mcp` as a stdio server. If the user can't install anything, point them to the browser demo: https://padharahitesh.github.io/vast-hls-mcp/

## Tools

| Tool | Argument | Use it for |
|---|---|---|
| `validate_vast` | `source`: ad tag URL or VAST XML | Checking one VAST response: is it valid, a wrapper, or no-fill? |
| `unwrap_vast` | `url`: ad tag URL (`max_depth`, default 5) | Following wrapper → wrapper → inline, and finding the hop that breaks |
| `inspect_hls` | `source`: .m3u8 URL or text | Master playlists (bitrate ladder, codecs) or media playlists (live/VOD, segments) |
| `find_ad_breaks` | `source`: **media** .m3u8 URL or text | Ad breaks, planned vs actual duration, unmatched markers |
| `decode_scte35` | `payload`: base64 (`/DA…`) or hex (`0xFC…`) | Explaining what a single cue means |

Every tool accepts either a URL or the pasted document. Each result has `"ok": true` or `"ok": false` with an `error` field. Read the `errors`, `warnings`, `info` and `issues` arrays before you summarize anything.

## Choosing the tool

- **The user pasted text starting with `/DA` or `0xFC`, or asks what a cue means** → `decode_scte35`.
- **The user pasted text or a URL containing `<VAST`, or an ad tag URL** → `validate_vast`.
  - If an ad's `kind` is `wrapper`, or the user says "no fill" or "ad didn't play" → also call `unwrap_vast` on the tag URL.
- **The user pasted a `.m3u8` file, or a playlist URL:**
  - If it contains `#EXT-X-STREAM-INF`, it's a master playlist → call `inspect_hls`, choose a variant (usually the highest bandwidth, or the one the user names), then call `find_ad_breaks` on that variant's URL.
  - Otherwise it's a media playlist → call `find_ad_breaks`. Also call `inspect_hls` if the user asks about playback, segments or live/VOD.
- **A general SSAI complaint ("slate instead of ads", "break too short")** → ask for the media playlist URL or text, then call `find_ad_breaks`.
  - If a break has an `scte35` field, you don't need `decode_scte35` separately; its summary is already included.

Relative variant URIs in a master playlist must be resolved against the master's URL before you pass them on.

## Interpreting results

### VAST (`validate_vast`, `unwrap_vast`)

| Finding | What it means | Typical fix |
|---|---|---|
| `status: no_fill` / `outcome: no_fill` | The demand returned an empty VAST | Check targeting, floors and geo; the ad server's Error URL should log it |
| `no <Impression>` | Impressions will not be counted, so revenue is lost | The ad server must include an Impression URL |
| `Duration … is not HH:MM:SS` | Many players reject the creative | Use `HH:MM:SS` or `HH:MM:SS.mmm` |
| `MediaFile uses http://` | HTTPS apps and most CTV devices block it | Serve media over https |
| `no MP4 or HLS MediaFile` | CTV/OTT players can't play it (for example WebM only, or VPAID only) | Provide an MP4 or HLS rendition |
| `requires <UniversalAdId>` / `<AdServingId>` | Not compliant with VAST 4 | Add the IDs; some SSAI vendors require them |
| `outcome: loop` | A wrapper chain loops back on itself | Fix the trafficking at the hop that repeats |
| `outcome: too_deep` | More than 5 wrappers; players time out | Reduce the number of resellers in the chain |
| A hop with high `elapsed_ms` | Slow demand partner, which causes timeouts | Name the `ad_system` responsible |

### HLS and ad breaks (`inspect_hls`, `find_ad_breaks`)

| Finding | What it means | Typical fix |
|---|---|---|
| `planned Xs but segments cover Ys` | The stitched ads don't fill the break: the viewer sees slate, or the ad is cut off | Fix the ad decisioning duration, or pad the break |
| `CUE-IN … without a matching CUE-OUT` | Stray marker, often from a packager restart or a manifest-manipulation bug | Check the packager or SSAI logs at that time |
| `never closes` | No CUE-IN or SCTE35-IN | Normal at the live edge (`live: true`); a bug in VOD |
| `CUE-OUT … still open` | Two OUTs without an IN | The upstream encoder sent duplicate signals |
| `tag says Xs but the SCTE-35 cue says Ys` | The manifest tag and the cue disagree | Fix whichever source is wrong, usually the packager |
| `CRC mismatch` / `did not decode` | The cue is corrupted or truncated | Inspect the source feed or encoder |
| `segment(s) exceed EXT-X-TARGETDURATION` | Violates RFC 8216; can stall playback | Re-segment, or raise the target duration |
| Variant `has no CODECS` / undefined `AUDIO group` | Some players refuse the variant | Fix the master playlist |

`joined_mid_break: true` means a live playlist started inside a break. Its negative `start_seconds` is the estimated real start, not an error.

## How to answer

1. Start with the verdict in one line (for example "2 problems found" or "this tag is valid").
2. List each problem with its location (break number and time, ad number, or wrapper hop), what it means for the viewer or for revenue, and the fix.
3. Quote real values from the results, such as durations, event IDs and ad systems. Never invent numbers.
4. Keep the raw JSON out of the answer unless the user asks for it.

## Limitations

- **SCTE-35 in HLS only.** DASH (MPD `EventStream`/`emsg`) isn't supported yet.
- **Manifests only, no media checks.** It doesn't verify actual segment media durations against `#EXTINF`; that needs ffprobe.
- **Public addresses only.** URL fetching refuses private and loopback addresses. For local test servers, the user can set `VAST_HLS_MCP_ALLOW_PRIVATE=1`.
- **Responses are capped** at 2 MB with a 10-second timeout.
