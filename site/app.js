// In-browser demo: loads the real vast_hls_mcp Python package into Pyodide and renders results.
// Every value is inserted with textContent (never innerHTML), so pasted input cannot inject markup.
"use strict";

const SPLICE_INSERT =
  "/DAvAAAAAAAA///wFAVIAACPf+/+c2nALv4AUsz1AAAAAAAKAAhDVUVJAAABNWLbowo=";
const TIME_SIGNAL =
  "/DA0AAAAAAAA///wBQb+cr0AUAAeAhxDVUVJSAAAjn/PAAGlmbAICAAAAAAsoKGKNAIAmsnRfg==";

const TOOLS = [
  {
    id: "decode_scte35",
    label: "Decode SCTE-35",
    input: "SCTE-35 cue (base64 or hex)",
    desc: "Decode a SCTE-35 splice_info_section: splice_insert or time_signal, splice time, break duration, segmentation descriptors (type, UPID, duration) and the CRC-32 check.",
    examples: [
      { label: "splice_insert (ad start, 60s)", text: SPLICE_INSERT },
      { label: "time_signal + placement opportunity", text: TIME_SIGNAL },
      { label: "hex input", text: "0xFC302F000000000000FFFFF014054800008F7FEFFE7369C02EFE0052CCF500000000000A0008435545490000013562DBA30A" },
      { label: "corrupted cue", text: SPLICE_INSERT.slice(0, 56) + "AAAAAA" + SPLICE_INSERT.slice(62) },
    ],
  },
  {
    id: "find_ad_breaks",
    label: "Find ad breaks",
    input: "HLS media playlist (.m3u8 text)",
    desc: "Find every ad break in an HLS media playlist (EXT-X-CUE-OUT/CUE-IN, EXT-X-DATERANGE SCTE35-OUT/IN, EXT-OATCLS-SCTE35), compare planned vs actual duration and flag what breaks ad insertion.",
    examples: [
      { label: "VOD with problems", file: "cue_out_vod.m3u8" },
      { label: "live, joined mid-break", file: "live_mid_break.m3u8" },
      { label: "DATERANGE at live edge", file: "daterange_live.m3u8" },
    ],
  },
  {
    id: "inspect_hls",
    label: "Inspect HLS",
    input: "HLS master or media playlist (.m3u8 text)",
    desc: "Inspect a master playlist (bitrate ladder, codecs, audio/subtitle groups) or a media playlist (live vs VOD, segments, target duration, discontinuities) with RFC 8216 warnings.",
    examples: [
      { label: "master playlist", file: "master.m3u8" },
      { label: "live media playlist", file: "live_mid_break.m3u8" },
    ],
  },
  {
    id: "validate_vast",
    label: "Validate VAST",
    input: "VAST XML (2.0 – 4.3)",
    desc: "Validate a VAST ad response: ads, creatives, media files and tracking, with the errors and warnings that make CTV/OTT players reject an ad or lose impressions.",
    examples: [
      { label: "valid VAST 4.2", file: "vast4_inline.xml" },
      { label: "broken ad", file: "vast_broken.xml" },
      { label: "wrapper", file: "vast_wrapper.xml" },
      { label: "no-fill", file: "vast_nofill.xml" },
    ],
  },
];

const $ = (id) => document.getElementById(id);
const state = { tool: TOOLS[0], demo: null, drafts: {} };

// ---------- tiny DOM helper ----------
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style") el.setAttribute("style", v);
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === undefined || c === null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

const fmt = (v) => (v === undefined || v === null ? "—" : typeof v === "boolean" ? (v ? "yes" : "no") : v);
const secs = (v) => (v === undefined || v === null ? "—" : `${Number(v).toFixed(3).replace(/\.?0+$/, "")} s`);

function kvTable(rows) {
  return h("div", { class: "table-wrap" },
    h("table", { class: "kv" },
      h("tbody", {}, rows.filter(([, v]) => v !== undefined).map(([k, v]) => h("tr", {}, h("th", { scope: "row" }, k), h("td", {}, fmt(v)))))));
}

function table(headers, rows, numCols = []) {
  return h("div", { class: "table-wrap" },
    h("table", {},
      h("thead", {}, h("tr", {}, headers.map((x) => h("th", { scope: "col" }, x)))),
      h("tbody", {}, rows.map((r) => h("tr", {}, r.map((c, i) => h("td", { class: numCols.includes(i) ? "num" : null }, fmt(c))))))));
}

function findings(groups) {
  const items = [];
  for (const [kind, list] of groups) for (const msg of list || []) items.push(h("li", { class: kind }, h("span", { class: "tag" }, kind), msg));
  return items.length ? h("ul", { class: "findings" }, items) : null;
}

function stats(pairs) {
  return h("dl", { class: "stats" }, pairs.map(([k, v]) => h("div", { class: "stat" }, h("dt", {}, k), h("dd", {}, fmt(v)))));
}

// ---------- per-tool renderers: return [pillClass, statusText, body nodes] ----------
const RENDER = {
  decode_scte35(o) {
    const cmd = o.splice_command || {};
    const bd = cmd.break_duration || {};
    const st = cmd.splice_time || (o.splice_command_name === "time_signal" ? cmd : {});
    const body = [
      h("p", {}, h("strong", {}, o.summary)),
      findings([["warning", o.warnings]]),
      kvTable([
        ["Command", `${o.splice_command_name} (0x${(o.splice_command_type ?? 0).toString(16).padStart(2, "0")})`],
        ["Event id", cmd.splice_event_id],
        ["Out of network (ad start)", cmd.out_of_network_indicator],
        ["Splice time (PTS)", st.pts_time !== undefined ? `${st.pts_time} (${secs(st.pts_seconds)})` : undefined],
        ["Break duration", bd.duration !== undefined ? `${secs(bd.duration_seconds)}, auto-return ${fmt(bd.auto_return)}` : undefined],
        ["PTS adjustment", o.pts_adjustment],
        ["CRC-32", o.crc_32 ? `${o.crc_32} (${o.crc_valid ? "valid" : "MISMATCH"})` : undefined],
      ]),
    ];
    const descs = o.descriptors || [];
    if (descs.length) {
      body.push(h("h3", {}, `Descriptors (${descs.length})`));
      body.push(table(["Descriptor", "Type", "Duration", "UPID", "Event id"], descs.map((d) => [
        d.name,
        d.segmentation_type_name ? `${d.segmentation_type_name} (0x${d.segmentation_type_id.toString(16)})` : d.provider_avail_id !== undefined ? `provider avail ${d.provider_avail_id}` : "—",
        d.segmentation_duration_seconds !== undefined ? secs(d.segmentation_duration_seconds) : "—",
        d.segmentation_upid ? `${d.segmentation_upid_type_name}: ${d.segmentation_upid}` : "—",
        d.segmentation_event_id,
      ])));
    }
    const bad = o.crc_valid === false;
    return [bad ? "err" : o.warnings?.length ? "warn" : "ok", bad ? "Corrupted cue" : "Decoded", body];
  },

  find_ad_breaks(o) {
    const total = Math.max(o.playlist_seconds || 0, 0.001);
    const breaks = o.breaks || [];
    const badIdx = new Set();
    for (const msg of o.issues || []) { const m = /break (\d+)/.exec(msg); if (m) badIdx.add(Number(m[1])); }
    const body = [
      stats([["Ad breaks", o.break_count], ["Ad time", secs(o.total_ad_seconds)], ["Playlist time", secs(o.playlist_seconds)], ["Segments", o.segment_count]]),
      findings([["error", o.issues]]),
    ];
    if (breaks.length) {
      body.push(h("div", { class: "timeline", role: "img", "aria-label": `Timeline with ${breaks.length} ad breaks` },
        h("div", { class: "track" }, breaks.map((b) => {
          const start = Math.max(b.start_seconds, 0);
          const width = Math.max(b.actual_duration_seconds - (start - b.start_seconds), 0);
          return h("div", { class: `ad${badIdx.has(b.index) ? " bad" : ""}`, title: `Break ${b.index}: ${secs(b.actual_duration_seconds)}`, style: `left:${(start / total) * 100}%;width:${Math.min((width / total) * 100, 100)}%` });
        })),
        h("div", { class: "legend" },
          h("span", { style: "--swatch: var(--content)" }, "content"),
          h("span", { style: "--swatch: var(--ad)" }, "ad break"),
          h("span", { style: "--swatch: var(--err)" }, "break with a problem"))));
      body.push(table(["#", "Marker", "Starts at", "Planned", "Actual", "Closed", "SCTE-35 cue"], breaks.map((b) => [
        b.index, b.style + (b.joined_mid_break ? " (joined mid-break)" : ""), secs(b.start_seconds), secs(b.planned_duration_seconds), secs(b.actual_duration_seconds), b.closed,
        b.scte35 ? (b.scte35.summary || b.scte35.decode_error) : "—",
      ]), [1, 2, 3, 4]));
    }
    return [o.healthy ? "ok" : "err", o.healthy ? "All breaks healthy" : `${o.issues.length} issue${o.issues.length === 1 ? "" : "s"} found`, body];
  },

  inspect_hls(o) {
    const issues = [["error", o.errors], ["warning", o.warnings]];
    let body;
    if (o.playlist_type === "master") {
      body = [
        stats([["Type", "master"], ["Variants", o.variant_count], ["Renditions", o.renditions.length], ["I-frame playlists", o.iframe_variants]]),
        findings(issues),
        h("h3", {}, "Bitrate ladder"),
        table(["Bandwidth", "Resolution", "Codecs", "Audio", "URI"], o.variants.map((v) => [v.bandwidth ? `${(v.bandwidth / 1e6).toFixed(2)} Mbps` : "—", v.resolution, v.codecs, v.audio, v.uri]), [0]),
      ];
    } else {
      body = [
        stats([["Type", o.live ? "live" : "VOD"], ["Segments", o.segment_count], ["Duration", secs(o.total_duration_seconds)], ["Target duration", secs(o.target_duration)]]),
        findings(issues),
        kvTable([["HLS version", o.hls_version], ["Media sequence", o.media_sequence], ["Discontinuities", o.discontinuities], ["Encryption", (o.encryption || []).join(", ") || "none"], ["Program date-time", o.program_date_time]]),
      ];
    }
    const errs = (o.errors || []).length, warns = (o.warnings || []).length;
    return [errs ? "err" : warns ? "warn" : "ok", errs ? "Invalid playlist" : warns ? `${warns} warning${warns === 1 ? "" : "s"}` : "Looks good", body];
  },

  validate_vast(o) {
    const body = [
      stats([["Status", o.status.replace("_", "-")], ["VAST version", o.version], ["Ads", o.ad_count]]),
      findings([["error", o.errors], ["warning", o.warnings], ["info", o.info]]),
    ];
    for (const ad of o.ads || []) {
      body.push(h("h3", {}, `Ad ${ad.index}${ad.id ? ` (${ad.id})` : ""}: ${ad.kind}`));
      body.push(kvTable([["Ad system", ad.ad_system], ["Title", ad.ad_title], ["Impression URLs", (ad.impressions || []).length], ["Wrapper → next tag", ad.vast_ad_tag_uri]]));
      const creatives = ad.creatives || [];
      if (creatives.length) {
        body.push(table(["Creative", "Type", "Duration", "Media files", "Tracking events"], creatives.map((c, i) => [
          c.id || i + 1, c.type, c.duration ? `${c.duration}${c.duration_seconds != null ? "" : " (invalid)"}` : "—",
          (c.media_files || []).map((m) => `${m.type || "?"}${m.width ? ` ${m.width}×${m.height}` : ""}`).join(", ") || "—",
          (c.tracking_events || []).join(", ") || "—",
        ])));
      }
    }
    const pill = o.status === "invalid" ? "err" : o.warnings.length || o.status === "no_fill" ? "warn" : "ok";
    const text = { valid: "Valid", invalid: "Invalid", no_fill: "No-fill response" }[o.status] || o.status;
    return [pill, text, body];
  },
};

// ---------- UI ----------
function selectTool(tool) {
  state.drafts[state.tool.id] = $("input").value;
  state.tool = tool;
  for (const btn of $("tabs").children) btn.setAttribute("aria-selected", String(btn.dataset.tool === tool.id));
  $("tool-desc").textContent = tool.desc;
  $("input-label").textContent = tool.input;
  $("examples").replaceChildren(...tool.examples.map((ex) => h("button", { class: "chip", type: "button", onclick: () => loadExample(ex) }, ex.label)));
  $("input").value = state.drafts[tool.id] ?? "";
  $("results").hidden = true;
  if (location.hash.slice(1) !== tool.id) history.replaceState(null, "", `#${tool.id}`);
  if (!$("input").value) loadExample(tool.examples[0], Boolean(state.demo));
}

async function loadExample(ex, autorun = true) {
  try {
    $("input").value = ex.text ?? (await (await fetch(`examples/${ex.file}`)).text());
    if (autorun && state.demo) run();
  } catch (e) {
    $("input").value = "";
  }
}

function run() {
  if (!state.demo) return;
  const t0 = performance.now();
  const out = JSON.parse(state.demo.run(state.tool.id, $("input").value));
  const ms = Math.round(performance.now() - t0);
  const results = $("results");
  let pill, text, body;
  if (!out.ok) {
    [pill, text, body] = ["err", "Could not analyse input", [h("ul", { class: "findings" }, h("li", { class: "error" }, out.error))]];
  } else {
    [pill, text, body] = RENDER[state.tool.id](out);
  }
  results.replaceChildren(
    h("div", { class: "status" }, h("span", { class: `pill ${pill}` }, pill === "ok" ? "OK" : pill === "warn" ? "Check" : "Problem"), h("span", { class: "status-text" }, text), h("span", { class: "timing" }, `${ms < 1 ? "<1" : ms} ms in your browser`)),
    ...body.filter(Boolean),
    h("details", { class: "raw" }, h("summary", {}, "Raw JSON (what an AI assistant receives)"), h("pre", {}, JSON.stringify(out, null, 2))),
  );
  results.hidden = false;
}

async function boot() {
  $("tabs").replaceChildren(...TOOLS.map((tool) => h("button", { class: "tab", role: "tab", type: "button", "data-tool": tool.id, onclick: () => selectTool(tool) }, tool.label)));
  $("tabs").addEventListener("keydown", (e) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    const i = TOOLS.indexOf(state.tool) + (e.key === "ArrowRight" ? 1 : -1);
    const next = TOOLS[(i + TOOLS.length) % TOOLS.length];
    selectTool(next);
    $("tabs").querySelector(`[data-tool="${next.id}"]`).focus();
  });
  selectTool(TOOLS.find((t) => t.id === location.hash.slice(1)) || TOOLS[0]);
  $("run").addEventListener("click", run);
  $("input").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); run(); } });

  try {
    const pyodide = await loadPyodide();
    await pyodide.loadPackage("micropip");
    await pyodide.runPythonAsync("import micropip\nawait micropip.install('defusedxml')");
    const files = await (await fetch("py/manifest.json")).json();
    pyodide.FS.mkdirTree("/home/pyodide/vast_hls_mcp");
    await Promise.all(files.map(async (f) => pyodide.FS.writeFile(`/home/pyodide/vast_hls_mcp/${f}`, await (await fetch(`py/vast_hls_mcp/${f}`)).text())));
    state.demo = pyodide.pyimport("vast_hls_mcp.demo");
    $("run").disabled = false;
    $("run").textContent = "Run";
    $("loader").textContent = "Python ready.";
    setTimeout(() => ($("loader").textContent = ""), 2500);
    run();
  } catch (err) {
    $("loader").textContent = `Could not start Python in this browser: ${err.message}`;
    $("loader").classList.add("error");
    $("run").textContent = "Unavailable";
  }
}

boot();
