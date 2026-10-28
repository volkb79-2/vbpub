"""Offline interactive HTML renderer for the frozen usage-report-v1 contract."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


_URI = re.compile(r"https?://", re.IGNORECASE)


def _script_json(value: object) -> str:
    """Encode JSON for a raw-text script element without ending that element."""
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    encoded = (
        encoded.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    # The report's price provenance includes the public source URL. Retain its
    # exact decoded value in the v1 data while keeping URL syntax out of the
    # HTML source: the offline page never turns provenance into a link/request.
    return _URI.sub(lambda match: match.group(0)[:match.group(0).index(":")] + r"\u003a\u002f\u002f", encoded)


def render_html(report: dict[str, Any], *, weights_file: str = "usage-weights.toml") -> str:
    """Render one self-contained HTML document from usage-report-v1.

    The report object is embedded unchanged. ``weights_file`` is display-only
    renderer metadata; only its basename is included to avoid exposing paths.
    """
    if report.get("schema_version") != "usage-report-v1":
        raise ValueError("HTML renderer requires usage-report-v1")
    chart = report.get("extensions", {}).get("chart_data")
    if not isinstance(chart, dict) or chart.get("schema_version") != "usage-charts-v1":
        raise ValueError("HTML renderer requires the usage-charts-v1 extension")
    name = Path(weights_file).name
    if not name or name in {".", ".."}:
        raise ValueError("weights_file must have a filename")
    metadata = {"weights_file": name}
    return (
        _DOCUMENT.replace("<!--USAGE_REPORT_DATA-->", _script_json(report), 1)
        .replace("<!--USAGE_RENDERER_META-->", _script_json(metadata), 1)
    )


_DOCUMENT = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; base-uri 'none'; form-action 'none'; object-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'none'; font-src 'none'; connect-src 'none'; media-src 'none'">
<title>nyxloom usage report</title>
<style>
:root{color-scheme:light dark;
  --bg:#fff;
  --panel:#f4f6f9;
  --ink:#17212b;
  --muted:#566475;
  --line:#c6ced8;
  --accent:#155eef;
  --soft:#e5edff;
  --danger:#9b2c2c;
  --grid:#d7dee7;
  --tooltip:#101820;
  --tooltip-ink:#fff}

@media(prefers-color-scheme:dark){:root{--bg:#121820;
  --panel:#1b2530;
  --ink:#e8edf3;
  --muted:#a9b6c5;
  --line:#394756;
  --accent:#91b5ff;
  --soft:#263956;
  --danger:#ffaaa4;
  --grid:#344352;
  --tooltip:#f0f4f8;
  --tooltip-ink:#111820}
}

*{box-sizing:border-box}
body{margin:0;
  background:var(--bg);
  color:var(--ink);
  font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1680px;
  margin:auto;
  padding:1.1rem}
h1,h2,h3,p{margin-top:0}
h1{font-size:clamp(1.5rem,3vw,2.3rem);
  margin-bottom:.2rem}
h2{font-size:1.2rem;
  margin-bottom:.7rem}
h3{font-size:1rem;
  margin-bottom:.45rem}
.eyebrow,.muted,.small{color:var(--muted)}
.eyebrow{text-transform:uppercase;
  letter-spacing:.08em;
  font-size:.72rem;
  font-weight:700}
.header,.panel,.provenance,.controls,.summary{border:1px solid var(--line);
  border-radius:.65rem;
  background:var(--panel);
  padding:.8rem 1rem;
  margin-bottom:.8rem}
.header{display:flex;
  gap:1rem;
  align-items:flex-start;
  justify-content:space-between}
.header p{margin-bottom:.25rem}
.sensitive-note{max-width:31rem;
  color:var(--muted);
  font-size:.85rem}
.provenance{display:flex;
  flex-wrap:wrap;
  gap:.4rem 1.4rem}
.provenance strong{color:var(--ink)}
.controls{display:grid;
  grid-template-columns:repeat(4,minmax(10rem,1fr));
  gap:.7rem;
  align-items:end}
.control{display:flex;
  flex-direction:column;
  gap:.25rem}
.control label,.control legend{font-weight:650}
.control input,.control select,.controls button,.chart-actions button{font:inherit;
  color:var(--ink);
  background:var(--bg);
  border:1px solid var(--line);
  border-radius:.35rem;
  padding:.42rem .55rem;
  min-height:2.3rem}
.controls fieldset{border:0;
  margin:0;
  padding:0;
  min-width:0}
.mode-buttons,.chart-actions{display:flex;
  gap:.35rem;
  flex-wrap:wrap}
.mode-buttons button[aria-pressed="true"],button[aria-pressed="true"]{background:var(--soft);
  border-color:var(--accent);
  outline:1px solid var(--accent)}
button{cursor:pointer}
button:hover{border-color:var(--accent)}
:focus-visible{outline:3px solid var(--accent)!important;
  outline-offset:2px}
.wide{grid-column:1/-1}
.brush-grid{display:grid;
  grid-template-columns:1fr 1fr;
  gap:.8rem}
.brush-grid input{width:100%;
  padding:0}
.time-actions{display:flex;
  gap:.3rem;
  flex-wrap:wrap}
.summary{display:flex;
  flex-wrap:wrap;
  justify-content:space-between;
  gap:.8rem}
.summary p{margin:0}
.table-wrap{overflow:auto;
  max-height:70vh;
  border:1px solid var(--line);
  border-radius:.4rem}
table{border-collapse:collapse;
  width:100%;
  min-width:1250px;
  font-size:.78rem}
thead{position:sticky;
  top:0;
  z-index:2;
  background:var(--panel)}
th,td{padding:.42rem .48rem;
  border-bottom:1px solid var(--line);
  text-align:right;
  vertical-align:top;
  white-space:nowrap}
th:first-child,td:first-child{text-align:left;
  min-width:18rem}
th{font-weight:700}
th button{font:inherit;
  font-weight:700;
  color:var(--ink);
  background:transparent;
  border:0;
  padding:0;
  text-align:inherit}
td.name-cell{white-space:normal;
  min-width:18rem}
.node-controls{display:flex;
  align-items:center;
  gap:.35rem}
.tree-toggle,.node-select{font:inherit;
  color:var(--ink);
  background:transparent;
  border:0;
  padding:.1rem .2rem}
.tree-toggle{width:1.3rem;
  color:var(--accent)}
.node-select{text-align:left;
  overflow-wrap:anywhere}
.node-row.selected{background:var(--soft)}
.dimmed{opacity:.27}
.subtree-row{font-style:italic;
  color:var(--muted);
  background:color-mix(in srgb,var(--panel) 75%,transparent)}
.tree-hidden{display:none}
.sharebar{display:inline-flex;
  width:5.5rem;
  height:.5rem;
  background:var(--line);
  border-radius:1rem;
  overflow:hidden;
  vertical-align:middle;
  margin-right:.35rem}
.sharebar span{background:var(--accent);
  height:100%}
.share-label{font-variant-numeric:tabular-nums}
.charts{display:grid;
  grid-template-columns:1fr 1fr;
  gap:.8rem}
.chart-panel{min-width:0;
  overflow:hidden}
.chart-panel svg{display:block;
  width:100%;
  height:auto;
  min-height:190px;
  background:var(--bg);
  border:1px solid var(--line);
  border-radius:.35rem;
  touch-action:pan-y;
  user-select:none}
.chart-panel svg text{fill:var(--muted);
  font:11px system-ui,sans-serif}
.chart-panel svg .axis{stroke:var(--line);
  stroke-width:1}
.chart-panel svg .grid{stroke:var(--grid);
  stroke-width:1}
.chart-panel svg .series-line{fill:none;
  stroke-width:2}
.chart-panel svg [data-call-id],.chart-panel svg [data-event-id],.chart-panel svg [data-group-id],.chart-panel svg [data-node-ids]{cursor:pointer}
.chart-panel svg [data-call-id]:focus,.chart-panel svg [data-event-id]:focus,.chart-panel svg [data-group-id]:focus,.chart-panel svg [data-node-ids]:focus{stroke:var(--ink);
  stroke-width:3}
.chart-panel svg .brush{fill:var(--accent);
  fill-opacity:.16;
  stroke:var(--accent);
  stroke-width:1}
.legend{display:flex;
  flex-wrap:wrap;
  gap:.35rem .8rem;
  margin:.4rem 0;
  color:var(--muted);
  font-size:.8rem}
.legend-item{display:inline-flex;
  align-items:center;
  gap:.3rem}
.swatch{display:inline-block;
  width:.8rem;
  height:.8rem;
  border-radius:50%;
  border:1px solid var(--line)}
.legend-line{display:inline-block;
  width:1.5rem;
  border-top:2px solid var(--muted)}
.legend-note{color:var(--muted);
  font-size:.82rem}
.tooltip{position:fixed;
  z-index:10;
  max-width:min(28rem,calc(100vw - 1rem));
  padding:.55rem .7rem;
  background:var(--tooltip);
  color:var(--tooltip-ink);
  border-radius:.4rem;
  box-shadow:0 3px 18px #0005;
  white-space:pre-line;
  pointer-events:none;
  font-size:.82rem}
.tooltip[hidden]{display:none}
.footer{border-top:1px solid var(--line);
  padding-top:.8rem;
  color:var(--muted);
  font-size:.83rem}
.anonymize-label{display:inline-flex;
  gap:.4rem;
  align-items:center;
  white-space:nowrap;
  color:var(--muted)}
#usage-report-ready{position:absolute;
  left:-10000px}

@media(max-width:850px){main{padding:.65rem}
.header{display:block}
.sensitive-note{max-width:none}
.controls{grid-template-columns:repeat(2,minmax(0,1fr))}
.charts{grid-template-columns:1fr}
.table-wrap{max-height:none}
.chart-panel svg{min-height:160px}
.controls .wide{grid-column:1/-1}
}

@media(max-width:480px){.controls{grid-template-columns:1fr}
.brush-grid{grid-template-columns:1fr}
.provenance{display:grid;
  grid-template-columns:1fr 1fr}
.header{padding:.7rem}
}

@media print{@page{size:A4 landscape;
  margin:8mm}
:root{color-scheme:light;
  --bg:#fff;
  --panel:#fff;
  --ink:#111;
  --muted:#333;
  --line:#888;
  --grid:#aaa;
  --soft:#eef}
body{background:#fff!important;
  color:#111!important;
  font-size:8pt}
main{max-width:none;
  padding:0}
.header,.panel,.provenance,.controls,.summary{background:#fff!important;
  border-color:#888!important;
  break-inside:avoid}
.panel[aria-labelledby="tree-heading"]{break-inside:auto}
.controls,.anonymize-label,.time-actions,.mode-buttons,.chart-actions,.tree-toggle{display:none!important}
.provenance{font-size:7pt}
.table-wrap{overflow:visible;
  max-height:none;
  border:0}
table{width:100%;
  min-width:0;
  table-layout:auto;
  font-size:6pt}
table,tbody{break-inside:auto}
thead{position:static;
  display:table-header-group}
tr{break-inside:avoid;
  page-break-inside:avoid}
th,td{white-space:normal;
  padding:2px 3px;
  border-color:#aaa;
  overflow-wrap:anywhere}
th:first-child,td:first-child,td.name-cell{min-width:0}
.tree-hidden{display:table-row!important}
.charts{grid-template-columns:1fr 1fr}
.chart-panel{break-inside:avoid;
  page-break-inside:avoid}
.chart-panel svg{height:70mm;
  min-height:0;
  border-color:#888}
.footer{color:#333}
.tooltip{display:none!important}
.dimmed{opacity:1!important}
.node-row.selected{background:#eef!important}
*{-webkit-print-color-adjust:exact;
  print-color-adjust:exact}
}

</style>
</head>
<body>
<main>
<header class="header">
  <div><p class="eyebrow">Local transcript metadata</p><h1>nyxloom usage report</h1><p id="report-window" class="muted"></p></div>
  <div class="sensitive-note"><label class="anonymize-label"><input id="anonymize-labels" type="checkbox"> Anonymize labels for display only</label><p>Display anonymizing does not remove original labels from this file. Use <code>--no-labels</code> before writing when labels must be absent.</p></div>
</header>
<section class="provenance" aria-label="Report provenance">
  <span>Generated <strong id="generated-at"></strong></span>
  <span>Calls <strong id="report-call-count"></strong></span>
  <span>Transcript files scanned <strong id="scanned-files"></strong></span>
  <span>Weights <strong id="weights-file"></strong> · retrieved <strong id="weights-date"></strong></span>
  <span>Weights SHA-256 <strong id="weights-hash"></strong></span>
  <span id="coverage-status"></span>
</section>
<section class="controls" aria-label="Report filters">
  <div class="control"><label for="model-filter">Model</label><select id="model-filter"><option value="">All models</option></select></div>
  <div class="control"><label for="effort-filter">Effort</label><select id="effort-filter"><option value="">All effort levels</option></select></div>
  <div class="control"><label for="topic-filter">Topic contains</label><input id="topic-filter" type="search" autocomplete="off" placeholder="Filter labels"></div>
  <fieldset class="control"><legend>Cost measure</legend><div class="mode-buttons" role="group" aria-label="Cost measure"><button type="button" data-measure="raw" aria-pressed="true">Raw tokens</button><button type="button" data-measure="weighted" aria-pressed="false">Weighted</button><button type="button" data-measure="usd" aria-pressed="false">API USD</button></div></fieldset>
  <div class="control"><label for="window-start">Window start (ISO-8601)</label><input id="window-start" type="text" spellcheck="false"></div>
  <div class="control"><label for="window-end">Window end, exclusive (ISO-8601)</label><input id="window-end" type="text" spellcheck="false"></div>
  <div class="control wide"><span class="muted">Brush within the report window</span><div class="brush-grid"><label>Start <input id="start-brush" type="range" min="0" max="1000" value="0" aria-label="Brush start"></label><label>End <input id="end-brush" type="range" min="0" max="1000" value="1000" aria-label="Brush end"></label></div><div class="time-actions"><button type="button" id="zoom-in">Zoom in</button><button type="button" id="zoom-out">Zoom out</button><button type="button" id="pan-left">Pan left</button><button type="button" id="pan-right">Pan right</button><button type="button" id="reset-time">Reset time</button></div></div>
</section>
<p class="legend-note">Drag across a timeline to zoom. Shift-drag to pan; double-click to reset. Weighted units and API-equivalent USD are local estimates; neither is subscription usage or billed cost.</p>
<section class="summary" aria-live="polite"><p id="active-call-count"></p><p id="active-totals"></p></section>
<section class="panel" aria-labelledby="tree-heading">
  <h2 id="tree-heading">Sessions and nested subagents</h2>
  <p class="legend-note">Each node row shows its own calls. A following subtotal row includes its descendants. Observed local share is weighted activity within this report, not a percentage of an account limit.</p>
  <div class="table-wrap"><table id="usage-table"><thead><tr>
    <th scope="col"><button type="button" data-sort="label">Session / agent / topic</button></th>
    <th scope="col"><button type="button" data-sort="model">Model(s)</button></th><th scope="col"><button type="button" data-sort="effort">Effort</button></th><th scope="col"><button type="button" data-sort="start">Start–end UTC</button></th><th scope="col"><button type="button" data-sort="dur">Duration (s)</button></th><th scope="col"><button type="button" data-sort="calls">Calls</button></th><th scope="col"><button type="button" data-sort="peak">Peak context</button></th><th scope="col">In</th><th scope="col">Cache read</th><th scope="col"><button type="button" data-sort="out">Out</button></th><th scope="col">Cache write 5m / 1h / ?</th><th scope="col"><button type="button" data-sort="cost">Focused measure</button></th><th scope="col">Observed local share</th><th scope="col">Report share</th>
  </tr></thead><tbody id="usage-rows"></tbody></table></div>
</section>
<section aria-labelledby="charts-heading">
  <h2 id="charts-heading">Joined activity diagrams</h2>
  <div id="model-legend" class="legend" aria-label="Model color legend"></div><div id="effort-legend" class="legend" aria-label="Effort line legend"></div>
  <div class="charts">
    <section class="panel chart-panel"><h3>Swim lanes · context over time</h3><p class="legend-note">Each lane is a local agent stream; structural compactions and model/effort switches are marked.</p><svg id="swim-chart" viewBox="0 0 1000 330" role="img" aria-label="Context size by agent lane over time"></svg></section>
    <section class="panel chart-panel"><h3>Joined context over time</h3><p class="legend-note">Absolute context values share one vertical scale across selected sessions.</p><svg id="context-chart" viewBox="0 0 1000 330" role="img" aria-label="Joined context size over time"></svg></section>
    <section class="panel chart-panel"><h3>Self cost by time bucket</h3><p id="cost-chart-note" class="legend-note">Buckets partition deduplicated calls; tree subtotals are not stacked.</p><div id="group-legend" class="legend" aria-label="Cost group legend"></div><svg id="cost-chart" viewBox="0 0 1000 300" role="img" aria-label="Call self cost by time bucket"></svg></section>
    <section class="panel chart-panel"><h3 id="composition-heading">Cost composition</h3><p id="composition-note" class="legend-note">Token-class composition is a derived view; table arithmetic uses the exact call ledger.</p><div id="composition-legend" class="legend" aria-label="Composition legend"></div><svg id="composition-chart" viewBox="0 0 1000 300" role="img" aria-label="Cost composition by agent"></svg></section>
  </div>
</section>
<footer class="footer">
  <p id="footer-provenance"></p>
  <p id="lower-bound-note">This report describes observed local transcript activity. Other devices or shared account surfaces may contribute; local activity is a lower bound on the account's total activity. Estimated limit percentage remains unknown unless a complete, identifiable UI-anchor fit is available.</p>
  <p id="privacy-note">Message, prompt and tool content are excluded. Labeled HTML files are sensitive local artifacts; protect them accordingly.</p>
</footer>
<div id="usage-tooltip" class="tooltip" role="tooltip" hidden></div>
<span id="usage-report-ready" data-ready="0" aria-hidden="true"></span>
</main>
<script type="application/json" id="usage-report-data"><!--USAGE_REPORT_DATA--></script>
<script type="application/json" id="usage-renderer-meta"><!--USAGE_RENDERER_META--></script>
<script>
(() => {
"use strict";
const report = JSON.parse(document.getElementById("usage-report-data").textContent);
const rendererMeta = JSON.parse(document.getElementById("usage-renderer-meta").textContent);
const chart = report.extensions.chart_data;
const allCalls = report.calls;
const callLookup = new Map(chart.call_lookup.map(item => [item.call_id, item]));
const callsById = new Map(allCalls.map(item => [item.call_id, item]));
const nodes = [];
const nodeById = new Map();
function collectNodes(items, parentId) {
  for (const node of items || []) {
    const item = {node, parentId, children: node.children || []};
    nodes.push(item);
    nodeById.set(node.node_id, item);
    collectNodes(item.children, node.node_id);
  }
}
collectNodes(report.nodes, null);
const ownerByCall = new Map();
for (const call of allCalls) {
  const lookup = callLookup.get(call.call_id);
  if (lookup && nodeById.has(lookup.node_id)) ownerByCall.set(call.call_id, lookup.node_id);
}
const allNodeTopics = [...new Set([...nodes.map(item => item.node.topic), ...allCalls.map(call => call.topic)].filter(Boolean))].sort();
const topicIds = new Map(allNodeTopics.map((value, index) => [value, `topic-${String(index + 1).padStart(3, "0")}`]));
const initialSort=report.presentation?.sort||"cost";
const state = {measure:"raw", selected:new Set(), collapsed:new Set(), sort:initialSort,
  descending:!(["label","model","effort","start"].includes(initialSort)),
  fullStart:Date.parse(report.window.since), fullEnd:Date.parse(report.window.until),
  start:Date.parse(report.window.since), end:Date.parse(report.window.until), anonymize:false, drag:null};
const byId = id => document.getElementById(id);
const make = (tag, attrs={}, text=null) => {
  const element = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") element.className = value;
    else if (key === "dataset") for (const [name, data] of Object.entries(value)) element.dataset[name] = String(data);
    else if (key === "style") element.setAttribute("style", value);
    else if (key in element && key.startsWith("aria-") === false && key !== "role") element[key] = value;
    else element.setAttribute(key, String(value));
  }
  if (text !== null) element.textContent = String(text);
  return element;
};
const svgNS = ["http:", "", "www.w3.org", "2000", "svg"].join("/");
function svg(tag, attrs={}, text=null) {
  const element = document.createElementNS(svgNS, tag);
  for (const [key, value] of Object.entries(attrs)) element.setAttribute(key, String(value));
  if (text !== null) element.textContent = String(text);
  return element;
}
function fmt(value, digits=0) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "unknown";
  return new Intl.NumberFormat("en-US", {maximumFractionDigits:digits, minimumFractionDigits:digits}).format(Number(value));
}
function short(value) { return value ? String(value).replace("T", " ").replace(/Z$/, "") : "—"; }
function selectedCalls() {
  const start = state.start, end = state.end;
  const model = byId("model-filter").value;
  const effort = byId("effort-filter").value;
  const topic = byId("topic-filter").value.trim().toLocaleLowerCase();
  return allCalls.filter(call => {
    const ts = Date.parse(call.timestamp);
    if (!Number.isFinite(ts) || ts < start || ts >= end) return false;
    if (model && call.model !== model) return false;
    if (effort && (call.effort || "unknown") !== effort) return false;
    const node = nodeById.get(ownerByCall.get(call.call_id))?.node;
    const label = call.topic || node?.topic || "";
    if (topic && !String(label).toLocaleLowerCase().includes(topic)) return false;
    return true;
  });
}
function callMetric(call, metric=state.measure) {
  if (metric === "weighted") return Number.isFinite(call.weighted_units) ? call.weighted_units : null;
  if (metric === "usd") return Number.isFinite(call.api_equivalent_usd) ? call.api_equivalent_usd : null;
  return Number(call.raw_token_units || 0);
}
function aggregate(calls) {
  const tokens = {input:0, cache_read:0, cache_write_5m:0, cache_write_1h:0, cache_write_unclassified:0, output:0, thinking:0};
  const models = new Map(), efforts = new Map();
  let raw = 0, weighted = 0, usd = 0, hasWeighted = true, hasUsd = true, peak = 0, peakAt=null;
  const times=[];
  for (const call of calls) {
    raw += Number(call.raw_token_units || 0);
    if (Number.isFinite(call.weighted_units)) weighted += call.weighted_units; else hasWeighted=false;
    if (Number.isFinite(call.api_equivalent_usd)) usd += call.api_equivalent_usd; else hasUsd=false;
    models.set(call.model, (models.get(call.model)||0)+1);
    efforts.set(call.effort || "unknown", (efforts.get(call.effort || "unknown")||0)+1);
    for (const key of Object.keys(tokens)) tokens[key] += Number(call.tokens?.[key] || 0);
    const ts = Date.parse(call.timestamp); if (Number.isFinite(ts)) times.push(ts);
    if (Number(call.context_size||0) > peak) { peak=Number(call.context_size||0); peakAt=call.timestamp; }
  }
  times.sort((a,b)=>a-b);
  return {calls:calls.length,tokens,models,efforts,raw,weighted:hasWeighted?weighted:null,usd:hasUsd?usd:null,
    start:times.length?new Date(times[0]).toISOString():null,end:times.length?new Date(times[times.length-1]).toISOString():null,
    peak,peakAt,duration:times.length>1?Math.round((times[times.length-1]-times[0])/1000):0};
}
function nodeCalls(nodeId, selected, descend=true) {
  const allowed = new Set([nodeId]);
  if (descend) {
    const visit = id => { for (const child of nodeById.get(id)?.children || []) { allowed.add(child.node_id); visit(child.node_id); } };
    visit(nodeId);
  }
  return selected.filter(call => allowed.has(ownerByCall.get(call.call_id)));
}
function displayLabel(value, fallback="unlabeled") {
  if (!value) return fallback;
  return state.anonymize ? (topicIds.get(value) || value) : value;
}
function displayNodeLabel(nodeId,value) {
  const node=nodeById.get(nodeId)?.node;
  if(node?.topic)return displayLabel(node.topic);
  return value||nodeLabel(nodeById.get(nodeId));
}
function displayGroupLabel(group,groupBy) {
  if(group.kind==="other")return group.label||group.group_id;
  if(groupBy==="topic")return group.group_value==="unlabeled"?"unlabeled":displayLabel(group.group_value||group.label);
  if(groupBy==="agent"){
    const item=nodeById.get(group.group_value);
    if(item)return nodeLabel(item);
  }
  return group.label||group.group_id;
}
function nodeLabel(item) {
  const node=item.node;
  const topic=node.topic?` · ${displayLabel(node.topic)}`:"";
  if (node.kind === "main") return `session ${node.session_id}${topic}`;
  if (node.kind === "unattributed") return "(unattributed)";
  if (node.kind === "other") return `other (${node.omitted_descendants||0} agents)`;
  return node.agent_id ? `agent ${node.agent_id}${topic}` : `${node.kind}${topic}`;
}
function metricText(value, metric=state.measure) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "unknown";
  if (metric === "usd") return `$${fmt(value,8)}`;
  if (metric === "weighted") return `${fmt(value,2)} weighted`;
  return `${fmt(value)} tokens`;
}
function selectedMetric(calls) {
  const values=calls.map(call=>callMetric(call));
  if (values.some(value=>value===null)) return null;
  return values.reduce((sum,value)=>sum+value,0);
}
function appendCell(row, text, attrs={}) { row.appendChild(make("td",attrs,text)); }
function shareNode(value) {
  if(value===null||value===undefined||!Number.isFinite(Number(value)))return make("span",{class:"share-label"},"unknown");
  const wrap=make("span",{class:"sharebar","aria-hidden":"true"});
  const fill=make("span"); fill.style.width=`${Math.max(0,Math.min(100,value*100))}%`; wrap.appendChild(fill);
  const text=make("span",{class:"share-label"},`${fmt(value*100,1)}%`);
  const holder=make("span"); holder.append(wrap,text); return holder;
}
function sortValue(item, calls) {
  const own=nodeCalls(item.node.node_id,calls,false); const agg=aggregate(own); const metric=selectedMetric(own);
  const node=item.node;
  if(state.sort==="label") return nodeLabel(item).toLocaleLowerCase();
  if(state.sort==="model") return [...agg.models.keys()].join(",").toLocaleLowerCase();
  if(state.sort==="effort") return [...agg.efforts.keys()].join(",").toLocaleLowerCase();
  if(state.sort==="start") return agg.start || "";
  if(state.sort==="dur") return agg.duration;
  if(state.sort==="calls") return agg.calls;
  if(state.sort==="peak") return agg.peak;
  if(state.sort==="out") return agg.tokens.output;
  if(state.sort==="cost") return metric===null?-1:metric;
  return nodeLabel(item);
}
function decorateSelection(element, nodeId) {
  const has=state.selected.size>0;
  const selected=state.selected.has(nodeId);
  element.classList.toggle("selected",selected);
  element.classList.toggle("dimmed",has&&!selected);
}
function setSelection(nodeId, shift) {
  if (!shift) {
    if (state.selected.size===1 && state.selected.has(nodeId)) state.selected.clear();
    else { state.selected.clear(); state.selected.add(nodeId); }
  } else if (state.selected.has(nodeId)) state.selected.delete(nodeId);
  else state.selected.add(nodeId);
  render();
}
function setSelections(nodeIds, shift) {
  const ids=[...new Set(nodeIds.filter(id=>nodeById.has(id)))];if(!ids.length)return;
  if(!shift){if(ids.length===state.selected.size&&ids.every(id=>state.selected.has(id)))state.selected.clear();else state.selected=new Set(ids);}
  else for(const id of ids){if(state.selected.has(id))state.selected.delete(id);else state.selected.add(id);}
  render();
}
function nodeRow(item, selected, depth, hidden=false, compareItems) {
  const node=item.node; const own=nodeCalls(node.node_id,selected,false); const ownAgg=aggregate(own);
  const descendants=nodeCalls(node.node_id,selected,true); const subtree=aggregate(descendants);
  if (!descendants.length) return [];
  const row=make("tr",{class:`node-row usage-node-row${hidden?" tree-hidden":""}`,dataset:{nodeId:node.node_id,depth}});
  decorateSelection(row,node.node_id);
  const name=make("td",{class:"name-cell"});
  const controls=make("div",{class:"node-controls",style:`padding-left:${Math.min(depth,12)*1.1}rem`});
  const children=(item.children||[])
    .map(child=>nodeById.get(child.node_id))
    .filter(child=>child&&nodeCalls(child.node.node_id,selected,true).length>0)
    .sort(compareItems);
  if(children.length){
    const open=!state.collapsed.has(node.node_id);
    const toggle=make("button",{type:"button",class:"tree-toggle","aria-expanded":String(open),"aria-label":`${open?"Collapse":"Expand"} ${nodeLabel(item)}`},open?"▾":"▸");
    toggle.addEventListener("click",()=>{if(open)state.collapsed.add(node.node_id);else state.collapsed.delete(node.node_id);render();});
    controls.appendChild(toggle);
  } else controls.appendChild(make("span",{class:"tree-toggle","aria-hidden":"true"},"·"));
  const select=make("button",{type:"button",class:"node-select",dataset:{nodeSelect:node.node_id}},nodeLabel(item));
  select.addEventListener("click",event=>setSelection(node.node_id,event.shiftKey));
  controls.appendChild(select); name.appendChild(controls); row.appendChild(name);
  const modelText=agg=>[...agg.models.entries()].sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0])).map(([key,count])=>`${key} (${count})`).join(", ")||"—";
  const effortText=agg=>[...agg.efforts.entries()].sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0])).map(([key,count])=>`${key} (${count})`).join(", ")||"—";
  appendCell(row,modelText(ownAgg)); appendCell(row,effortText(ownAgg)); appendCell(row,ownAgg.start?`${short(ownAgg.start)} – ${short(ownAgg.end)}`:"—");
  appendCell(row,fmt(ownAgg.duration)); appendCell(row,fmt(ownAgg.calls)); appendCell(row,fmt(ownAgg.peak));
  appendCell(row,fmt(ownAgg.tokens.input)); appendCell(row,fmt(ownAgg.tokens.cache_read)); appendCell(row,fmt(ownAgg.tokens.output));
  appendCell(row,`${fmt(ownAgg.tokens.cache_write_5m)} / ${fmt(ownAgg.tokens.cache_write_1h)} / ${fmt(ownAgg.tokens.cache_write_unclassified)}`);
  appendCell(row,metricText(selectedMetric(own)));
  const totalWeighted=aggregate(selected).weighted;
  appendCell(row,ownAgg.weighted!==null&&totalWeighted?`${fmt(ownAgg.weighted/totalWeighted*100,1)}%`:"unknown");
  const reportTotal=selectedMetric(selected); const ownValue=selectedMetric(own);
  const fraction=reportTotal!==null&&reportTotal>0&&ownValue!==null?ownValue/reportTotal:null;
  const share=make("td"); share.appendChild(shareNode(fraction)); row.appendChild(share);
  const result=[row];
  if(children.length){
    const totalRow=make("tr",{class:`subtree-row${hidden?" tree-hidden":""}`,dataset:{nodeId:node.node_id,depth}}); decorateSelection(totalRow,node.node_id);
    const label=make("td",{class:"name-cell"}); label.appendChild(make("span",{style:`padding-left:${(Math.min(depth,12)+1.55)*1.1}rem`},"subtree subtotal")); totalRow.appendChild(label);
    appendCell(totalRow,modelText(subtree)); appendCell(totalRow,effortText(subtree)); appendCell(totalRow,subtree.start?`${short(subtree.start)} – ${short(subtree.end)}`:"—"); appendCell(totalRow,fmt(subtree.duration)); appendCell(totalRow,fmt(subtree.calls)); appendCell(totalRow,fmt(subtree.peak));
    appendCell(totalRow,fmt(subtree.tokens.input)); appendCell(totalRow,fmt(subtree.tokens.cache_read)); appendCell(totalRow,fmt(subtree.tokens.output));
    appendCell(totalRow,`${fmt(subtree.tokens.cache_write_5m)} / ${fmt(subtree.tokens.cache_write_1h)} / ${fmt(subtree.tokens.cache_write_unclassified)}`);
    appendCell(totalRow,metricText(selectedMetric(descendants)));
    appendCell(totalRow,subtree.weighted!==null&&totalWeighted?`${fmt(subtree.weighted/totalWeighted*100,1)}%`:"unknown");
    const treeValue=selectedMetric(descendants); const treeShare=make("td"); treeShare.appendChild(shareNode(reportTotal!==null&&reportTotal>0&&treeValue!==null?treeValue/reportTotal:null)); totalRow.appendChild(treeShare); result.push(totalRow);
  }
  const hideChildren=hidden||state.collapsed.has(node.node_id);
  for(const child of children) result.push(...nodeRow(child,selected,depth+1,hideChildren,compareItems));
  return result;
}
function renderTable(selected) {
  const tbody=byId("usage-rows"); tbody.replaceChildren();
  const cmp=(a,b)=>{const av=sortValue(a,selected),bv=sortValue(b,selected);let order=typeof av==="number"&&typeof bv==="number"?av-bv:String(av).localeCompare(String(bv));if(state.descending)order=-order;return order||a.node.node_id.localeCompare(b.node.node_id);};
  const roots=(report.nodes||[]).map(node=>nodeById.get(node.node_id)).filter(Boolean).sort(cmp);
  for(const root of roots) for(const row of nodeRow(root,selected,0,false,cmp)) tbody.appendChild(row);
  const total=aggregate(selected); const weight=total.weighted===null?"unknown":fmt(total.weighted,2);const usd=total.usd===null?"unknown":`$${fmt(total.usd,8)}`;
  byId("active-call-count").textContent=`Showing ${fmt(selected.length)} of ${fmt(allCalls.length)} calls`;
  byId("active-totals").textContent=`Raw ${fmt(total.raw)} · weighted ${weight} · API-equivalent ${usd}`;
}
function colorForModel(model) {
  const hue=chart.color_keys.model_hues[model]; return `hsl(${Number.isFinite(hue)?hue:210} 65% 48%)`;
}
function dashForEffort(effort) { return chart.color_keys.effort_styles[effort||"unknown"]?.dash || ""; }
function setAxis(svgRoot, height, yMax, yLabel, digits=0) {
  const top=26,bottom=height-34,left=65,right=982;
  for(let i=0;i<=4;i++){
    const y=top+(bottom-top)*i/4; const value=yMax*(1-i/4);
    svgRoot.appendChild(svg("line",{x1:left,y1:y,x2:right,y2:y,class:"grid"}));
    svgRoot.appendChild(svg("text",{x:left-8,y:y+4,"text-anchor":"end"},fmt(value,digits)));
  }
  svgRoot.appendChild(svg("line",{x1:left,y1:top,x2:left,y2:bottom,class:"axis"}));
  svgRoot.appendChild(svg("line",{x1:left,y1:bottom,x2:right,y2:bottom,class:"axis"}));
  svgRoot.appendChild(svg("text",{x:13,y:16},yLabel));
  const span=Math.max(1,state.end-state.start);
  for(let i=0;i<=4;i++){
    const x=left+(right-left)*i/4; const t=state.start+span*i/4;
    svgRoot.appendChild(svg("line",{x1:x,y1:top,x2:x,y2:bottom,class:"grid"}));
    svgRoot.appendChild(svg("text",{x,y:height-12,"text-anchor":i===0?"start":i===4?"end":"middle"},new Date(t).toISOString().slice(11,16)+"Z"));
  }
  return {left,right,top,bottom};
}
function xAt(time, axis) { return axis.left+(axis.right-axis.left)*(Number(time)-state.start)/Math.max(1,state.end-state.start); }
function yAt(value, axis, max) { return axis.bottom-(axis.bottom-axis.top)*(Number(value)/Math.max(1,max)); }
function matchingCall(call, selectedSet) { return selectedSet.has(call.call_id); }
function showTooltip(text,event) {
  const tip=byId("usage-tooltip"); tip.textContent=text; tip.hidden=false;
  if(event&&Number.isFinite(event.clientX)){tip.style.left=`${Math.min(event.clientX+14,window.innerWidth-tip.offsetWidth-8)}px`;tip.style.top=`${Math.min(event.clientY+14,window.innerHeight-tip.offsetHeight-8)}px`;}
  else {tip.style.left="12px";tip.style.top="12px";}
}
function hideTooltip(){byId("usage-tooltip").hidden=true;}
function callTooltip(callId,event) {
  const call=callsById.get(callId), lookup=callLookup.get(callId); if(!call||!lookup)return;
  const node=nodeById.get(lookup.node_id)?.node; const topic=call.topic||node?.topic||"(none)";
  const tokens=call.tokens||{};
  showTooltip([
    `Session: ${call.session_id}`,`Agent: ${call.agent_id||"controller"}`,`Topic: ${displayLabel(topic,"(none)")}`,
    `Model: ${call.model}`,`Effort: ${call.effort||"unknown"}`,`Time: ${call.timestamp}`,
    `Tokens: in ${fmt(tokens.input)} · cache read ${fmt(tokens.cache_read)} · write 5m ${fmt(tokens.cache_write_5m)} · write 1h ${fmt(tokens.cache_write_1h)} · out ${fmt(tokens.output)}`,
    `Context ${fmt(call.context_size)} · raw ${fmt(call.raw_token_units)} · weighted ${metricText(call.weighted_units,"weighted")} · API ${metricText(call.api_equivalent_usd,"usd")}`
  ].join("\n"),event);
}
function callSetTooltip(calls, heading, timeLabel) {
  const unique=(values,limit=6,topics=false)=>{
    const labels=[...new Set(values.filter(Boolean).map(String))];
    const shown=labels.slice(0,limit).map(value=>topics?displayLabel(value):value);
    return `${shown.join(", ")||"(none)"}${labels.length>limit?`, +${labels.length-limit} more`:""}`;
  };
  const values={input:0,cache_read:0,cache_write_5m:0,cache_write_1h:0,cache_write_unclassified:0,output:0};
  for(const call of calls)for(const key of Object.keys(values))values[key]+=Number(call.tokens?.[key]||0);
  const topics=calls.map(call=>call.topic||nodeById.get(ownerByCall.get(call.call_id))?.node.topic);
  return [
    heading,
    `Session: ${unique(calls.map(call=>call.session_id))}`,
    `Agent: ${unique(calls.map(call=>call.agent_id||"controller"))}`,
    `Topic: ${unique(topics,6,true)}`,
    `Model: ${unique(calls.map(call=>call.model))}`,
    `Effort: ${unique(calls.map(call=>call.effort||"unknown"))}`,
    `Time: ${timeLabel||unique(calls.map(call=>call.timestamp))}`,
    `Tokens: in ${fmt(values.input)} · cache read ${fmt(values.cache_read)} · write 5m ${fmt(values.cache_write_5m)} · write 1h ${fmt(values.cache_write_1h)} · write ? ${fmt(values.cache_write_unclassified)} · out ${fmt(values.output)}`,
    `Calls: ${fmt(calls.length)}`
  ].join("\n");
}
function bindKeyboardSelection(element,nodeId) {
  const nodeIds=Array.isArray(nodeId)?nodeId:[nodeId].filter(Boolean);
  element.setAttribute("tabindex","0");element.setAttribute("role","button");element.setAttribute("aria-describedby","usage-tooltip");
  element.addEventListener("keydown",event=>{
    if(event.key!=="Enter"&&event.key!==" ")return;
    event.preventDefault();event.stopPropagation();setSelections(nodeIds,event.shiftKey);
  });
}
function bindAggregateMark(element,calls,tooltipText,label) {
  const nodeIds=[...new Set(calls.map(call=>ownerByCall.get(call.call_id)).filter(Boolean))];
  bindKeyboardSelection(element,nodeIds);
  element.setAttribute("aria-label",label||tooltipText.split("\n").slice(0,6).join(". "));
  element.addEventListener("pointerenter",event=>showTooltip(tooltipText,event));
  element.addEventListener("pointermove",event=>showTooltip(tooltipText,event));
  element.addEventListener("pointerleave",hideTooltip);element.addEventListener("focus",()=>showTooltip(tooltipText));element.addEventListener("blur",hideTooltip);
  element.addEventListener("click",event=>{event.stopPropagation();setSelections(nodeIds,event.shiftKey);});
}
function eventTooltip(item,event) {
  const node=nodeById.get(item.node_id)?.node; showTooltip([
    `Event: ${item.kind}`,`Session: ${item.session_id||node?.session_id||"unknown"}`,
    `Agent: ${item.agent_id||node?.agent_id||"controller"}`,`Topic: ${displayLabel(node?.topic,"(none)")}`,
    `Time: ${item.timestamp}`,item.pre_tokens!==undefined?`Context ${fmt(item.pre_tokens)} → ${fmt(item.post_tokens)}`:"",
    item.previous_value!==undefined?`Change: ${item.previous_value||"unknown"} → ${item.current_value||"unknown"}`:""
  ].filter(Boolean).join("\n"),event);
}
function bindPoint(element,callId,nodeId) {
  element.dataset.callId=callId; element.dataset.nodeId=nodeId;bindKeyboardSelection(element,nodeId);
  const call=callsById.get(callId); const lookup=callLookup.get(callId);
  const node=nodeById.get(nodeId)?.node; const label=`${lookup?.session_id||call?.session_id||"session"}, ${call?.agent_id||"controller"}, ${call?.model||"model"}, ${call?.effort||"unknown"}, ${call?.timestamp||"time"}`;
  element.setAttribute("aria-label",label);
  element.addEventListener("pointerenter",event=>callTooltip(callId,event)); element.addEventListener("pointermove",event=>callTooltip(callId,event));
  element.addEventListener("pointerleave",hideTooltip); element.addEventListener("focus",()=>callTooltip(callId)); element.addEventListener("blur",hideTooltip);
  element.addEventListener("click",event=>{event.stopPropagation();setSelection(nodeId,event.shiftKey);});
  element.appendChild(svg("title",{},label));
}
function bindEvent(element,item,nodeId) {
  element.dataset.eventId=item.event_id||item.chart_id||"event"; element.dataset.nodeId=nodeId; element.dataset.kind=item.kind;
  bindKeyboardSelection(element,nodeId);element.setAttribute("aria-label",`${item.kind} at ${item.timestamp}`);
  element.addEventListener("pointerenter",event=>eventTooltip(item,event)); element.addEventListener("pointermove",event=>eventTooltip(item,event));
  element.addEventListener("pointerleave",hideTooltip); element.addEventListener("focus",()=>eventTooltip(item)); element.addEventListener("blur",hideTooltip);
  element.addEventListener("click",event=>{event.stopPropagation();setSelection(nodeId,event.shiftKey);}); element.appendChild(svg("title",{},`${item.kind} at ${item.timestamp}`));
}
function addLineSegment(group,a,b,axis,max) {
  const color=colorForModel(b.model); const dash=dashForEffort(b.effort);
  group.appendChild(svg("line",{x1:xAt(a.timestamp,axis),y1:yAt(a.context_size,axis,max),x2:xAt(b.timestamp,axis),y2:yAt(b.context_size,axis,max),class:"series-line",stroke:color,"stroke-dasharray":dash}));
}
function addMarker(group,item,x,y,nodeId) {
  const kind=item.kind||"event"; let element;
  if(kind==="compaction") element=svg("path",{d:`M ${x} ${y-6} L ${x+6} ${y} L ${x} ${y+6} L ${x-6} ${y} Z`,fill:"#d97706",stroke:"#5a3a00"});
  else if(kind==="model_switch") element=svg("rect",{x:x-5,y:y-5,width:10,height:10,fill:"#7c3aed",stroke:"#fff"});
  else if(kind==="effort_switch") element=svg("path",{d:`M ${x} ${y-6} L ${x+6} ${y+5} L ${x-6} ${y+5} Z`,fill:"#087e8b",stroke:"#fff"});
  else if(kind==="unexplained_drop") element=svg("path",{d:`M ${x-5} ${y-5} L ${x+5} ${y+5} M ${x+5} ${y-5} L ${x-5} ${y+5}`,stroke:"#b42318","stroke-width":2});
  else element=svg("circle",{cx:x,cy:y,r:5,fill:"#b42318"});
  bindEvent(element,item,nodeId); group.appendChild(element);
}
function renderContext(selected) {
  const root=byId("context-chart");root.replaceChildren();const sessions=chart.datasets.context_over_time.sessions||[];
  const allPoints=sessions.flatMap(session=>(session.series||[]).flatMap(series=>series.points||[]));
  const selectedSet=new Set(selected.map(call=>call.call_id));
  const visible=allPoints.filter(point=>selectedSet.has(point.call_id));const max=Math.max(1,...visible.map(point=>Number(point.context_size||0)));
  const axis=setAxis(root,330,max,"context tokens");
  let count=0;
  for(const session of sessions) for(const series of session.series||[]){
    const nodeId=series.node_id; if(!nodeById.has(nodeId))continue; const group=svg("g",{"data-node-id":nodeId});
    const points=(series.points||[]).filter(point=>selectedSet.has(point.call_id)&&Date.parse(point.timestamp)>=state.start&&Date.parse(point.timestamp)<state.end);
    if(!points.length)continue; count+=points.length;
    const name=displayNodeLabel(nodeId,series.label);
    group.appendChild(svg("text",{x:axis.left+4,y:axis.top+12+(count%8)*13},name));
    for(let i=1;i<points.length;i++)addLineSegment(group,points[i-1],points[i],axis,max);
    for(const point of points){const circle=svg("circle",{cx:xAt(point.timestamp,axis),cy:yAt(point.context_size,axis,max),r:4,fill:colorForModel(point.model),stroke:"var(--ink)","stroke-dasharray":dashForEffort(point.effort)});bindPoint(circle,point.call_id,nodeId);group.appendChild(circle);}
    for(const event of series.events||[]){if(Date.parse(event.timestamp)<state.start||Date.parse(event.timestamp)>=state.end)continue;const y=yAt(event.pre_tokens??series.peak_context,axis,max);addMarker(group,event,xAt(event.timestamp,axis),y,nodeId);}
    root.appendChild(group);
  }
  if(!count)root.appendChild(svg("text",{x:500,y:165,"text-anchor":"middle"},"No context points match these filters."));
}
function renderSwim(selected) {
  const root=byId("swim-chart");root.replaceChildren();const lanes=chart.datasets.swim_lanes.lanes||[];const selectedIds=new Set(selected.map(call=>call.call_id));
  const visible=lanes.filter(lane=>(lane.points||[]).some(point=>selectedIds.has(point.call_id)&&Date.parse(point.timestamp)>=state.start&&Date.parse(point.timestamp)<state.end));
  const axis=setAxis(root,330,visible.length,"agent lane");const laneH=Math.max(22,(axis.bottom-axis.top)/Math.max(1,visible.length));let index=0;
  for(const lane of visible){const nodeId=lane.node_id;if(!nodeById.has(nodeId))continue;const group=svg("g",{"data-node-id":nodeId});const y0=axis.top+index*laneH;const laneMax=Math.max(1,Number(lane.peak_context||0));
    group.appendChild(svg("text",{x:axis.left+3,y:y0+11},displayNodeLabel(nodeId,lane.label)));
    const points=(lane.points||[]).filter(point=>selectedIds.has(point.call_id)&&Date.parse(point.timestamp)>=state.start&&Date.parse(point.timestamp)<state.end);
    const localAxis={...axis,top:y0+13,bottom:y0+laneH-2};for(let i=1;i<points.length;i++)addLineSegment(group,points[i-1],points[i],localAxis,laneMax);
    for(const point of points){const circle=svg("circle",{cx:xAt(point.timestamp,axis),cy:yAt(point.context_size,localAxis,laneMax),r:3.5,fill:colorForModel(point.model),stroke:"var(--ink)","stroke-dasharray":dashForEffort(point.effort)});bindPoint(circle,point.call_id,nodeId);group.appendChild(circle);}
    for(const event of lane.events||[]){if(Date.parse(event.timestamp)<state.start||Date.parse(event.timestamp)>=state.end)continue;addMarker(group,event,xAt(event.timestamp,axis),y0+laneH/2,nodeId);}
    root.appendChild(group);index++;
  }
  if(!visible.length)root.appendChild(svg("text",{x:500,y:165,"text-anchor":"middle"},"No agent lanes match these filters."));
}
function groupColor(groupId){let hash=2166136261;for(const char of String(groupId)){hash^=char.charCodeAt(0);hash=Math.imul(hash,16777619);}return `hsl(${(hash>>>0)%360} 58% 48%)`;}
function renderCost(selected) {
  const root=byId("cost-chart");root.replaceChildren();const dataset=chart.datasets.weighted_cost_buckets;const groups=dataset.groups||[];
  const buckets=dataset.buckets||[];const bucketStarts=buckets.map(bucket=>Date.parse(bucket.start));const bucketEnds=buckets.map(bucket=>Date.parse(bucket.end));
  const bucketIndex=timestamp=>{let low=0,high=bucketStarts.length;while(low<high){const mid=(low+high)>>1;if(bucketStarts[mid]<=timestamp)low=mid+1;else high=mid;}const index=low-1;return index>=0&&timestamp<bucketEnds[index]?index:-1;};
  const callGroup=new Map();for(const group of groups)for(const bucket of group.buckets||[])for(const id of bucket.call_ids||[])callGroup.set(id,group.group_id);
  const values=new Map(groups.map(group=>[group.group_id,Array(buckets.length).fill(0)]));const members=new Map(groups.map(group=>[group.group_id,new Set()]));const callsByGroupBucket=new Map(groups.map(group=>[group.group_id,Array.from({length:buckets.length},()=>[])]));let unpriced=0;
  for(const call of selected){const groupId=callGroup.get(call.call_id);if(!groupId)continue;const index=bucketIndex(Date.parse(call.timestamp));if(index<0)continue;const amount=callMetric(call);members.get(groupId).add(ownerByCall.get(call.call_id));callsByGroupBucket.get(groupId)[index].push(call);if(amount===null){unpriced++;continue;}values.get(groupId)[index]+=amount;}
  const active=groups.filter(group=>(values.get(group.group_id)||[]).some(value=>value>0));const max=Math.max(1,...buckets.map((_,i)=>active.reduce((sum,group)=>sum+values.get(group.group_id)[i],0)));
  const axis=setAxis(root,300,max,state.measure==="usd"?"API USD":state.measure==="weighted"?"weighted units":"raw tokens",state.measure==="usd"?8:state.measure==="weighted"?2:0);const totalHeight=axis.bottom-axis.top;
  for(let i=0;i<buckets.length;i++){
    const clippedStart=Math.max(bucketStarts[i],state.start),clippedEnd=Math.min(bucketEnds[i],state.end);if(clippedStart>=clippedEnd)continue;
    const x=axis.left+(clippedStart-state.start)/Math.max(1,state.end-state.start)*(axis.right-axis.left);const width=(clippedEnd-clippedStart)/Math.max(1,state.end-state.start)*(axis.right-axis.left);
    let running=0;
    for(const group of active){
      const value=values.get(group.group_id)[i];if(!value)continue;
      const height=value/max*totalHeight;
      const rect=svg("rect",{x:x+0.5,y:axis.bottom-running-height,width:Math.max(1,width-1),height,fill:groupColor(group.group_id),"data-group-id":group.group_id,"data-node-ids":[...members.get(group.group_id)].join(" ")});
      const label=displayGroupLabel(group,dataset.group_by);
      const pointCalls=callsByGroupBucket.get(group.group_id)[i];
      const timeLabel=`${buckets[i].start} – ${buckets[i].end}`;
      const tooltip=callSetTooltip(pointCalls,`Self measure: ${metricText(value)}`,timeLabel);
      bindAggregateMark(rect,pointCalls,tooltip,`${label}. ${metricText(value)}. ${timeLabel}`);
      rect.appendChild(svg("title",{},`${label}: ${metricText(value)}`));
      root.appendChild(rect);running+=height;
    }
  }
  byId("cost-chart-note").textContent=`Groups partition deduplicated calls by ${dataset.group_by}; ${unpriced&&state.measure==="usd"?`${fmt(unpriced)} unpriced calls are excluded from USD bars. `:""}Tree subtotals are not stacked.`;
  renderGroupLegend(active,dataset.group_by);
}
function renderGroupLegend(groups,groupBy){const legend=byId("group-legend");if(!legend)return;legend.replaceChildren();for(const group of groups){const item=make("span",{class:"legend-item"});const swatch=make("span",{class:"swatch"});swatch.style.background=groupColor(group.group_id);item.append(swatch,make("span",{},displayGroupLabel(group,groupBy)));legend.appendChild(item);}}
function renderComposition(selected) {
  const root=byId("composition-chart");root.replaceChildren();const dataset=chart.datasets.cost_composition;const rows=dataset.rows||[];const selectedIds=new Set(selected.map(call=>call.call_id));
  const tokenFields=["input","cache_read","cache_write_5m","cache_write_1h","cache_write_unclassified","output"];
  const tokenLabels=["Input","Cache read","Write 5m","Write 1h","Write ?","Output"];
  const rowCalls=rows.map(row=>(row.call_ids||[]).map(id=>callsById.get(id)).filter(call=>call&&selectedIds.has(call.call_id)));
  const showUsd=state.measure==="usd";const values=[];let max=0,unpriced=0;
  for(let i=0;i<rows.length;i++){
    const calls=rowCalls[i];if(showUsd){let sum=0,known=0;for(const call of calls){if(Number.isFinite(call.api_equivalent_usd)){sum+=call.api_equivalent_usd;known++;}else unpriced++;}values.push([known?sum:0]);max=Math.max(max,known?sum:0);}
    else {const rowValues=tokenFields.map(field=>calls.reduce((sum,call)=>sum+Number(call.tokens?.[field]||0),0));if(state.measure==="weighted"){const weights=rows[i].components||{};for(let j=0;j<tokenFields.length;j++)rowValues[j]*=Number(weights[tokenFields[j]]?.weight_per_token||0);}values.push(rowValues);max=Math.max(max,rowValues.reduce((a,b)=>a+b,0));}
  }
  const axis=setValueAxis(root,300,max,showUsd?"API USD":state.measure==="weighted"?"weighted units":"tokens",showUsd?8:state.measure==="weighted"?2:0);const colors=["#3366cc","#dc3912","#ff9900","#109618","#990099","#0099c6"];
  const barH=Math.min(24,Math.max(12,(axis.bottom-axis.top)/Math.max(1,rows.length)-5));
  for(let i=0;i<rows.length;i++){const row=rows[i],calls=rowCalls[i];if(!calls.length)continue;const y=axis.top+i*((axis.bottom-axis.top)/Math.max(1,rows.length));root.appendChild(svg("text",{x:axis.left-6,y:y+barH,"text-anchor":"end"},displayNodeLabel(row.node_id,row.label||row.kind||"other")));
    let offset=0;
    for(let j=0;j<values[i].length;j++){
      const value=values[i][j];if(!value)continue;
      const width=value/max*(axis.right-axis.left);
      const rect=svg("rect",{x:axis.left+offset,y,width,height:barH,fill:showUsd?groupColor(row.chart_id):colors[j],"data-node-ids":calls.map(call=>ownerByCall.get(call.call_id)).filter(Boolean).join(" ")});
      const category=showUsd?"API-equivalent USD":tokenLabels[j];
      const valueText=showUsd?`$${fmt(value,8)}`:fmt(value,state.measure==="weighted"?2:0);
      const timeLabel=`${new Date(state.start).toISOString()} – ${new Date(state.end).toISOString()}`;
      bindAggregateMark(rect,calls,callSetTooltip(calls,`${category}: ${valueText}`,timeLabel),`${category}. ${valueText}. ${timeLabel}`);
      rect.appendChild(svg("title",{},`${category}: ${fmt(value,showUsd?8:state.measure==="weighted"?2:0)}`));
      root.appendChild(rect);offset+=width;
    }
  }
  byId("composition-heading").textContent=showUsd?"API-equivalent USD by agent":"Cost composition";
  byId("composition-note").textContent=showUsd?`${unpriced?`${fmt(unpriced)} unpriced calls are excluded. `:""}USD is list-price arithmetic, not billed cost.`:`${state.measure==="weighted"?"Experimental weighted token classes":"Raw token classes"}; exact filtered calls are used.`;
  const legend=byId("composition-legend");if(legend){legend.replaceChildren();const labels=showUsd?["API USD"]:tokenLabels;labels.forEach((label,index)=>{const item=make("span",{class:"legend-item"});const swatch=make("span",{class:"swatch"});swatch.style.background=showUsd?"#3366cc":colors[index];item.append(swatch,make("span",{},label));legend.appendChild(item);});}
}
function renderLegends(){
  const models=byId("model-legend");models.replaceChildren();for(const [model] of Object.entries(chart.color_keys.model_hues)){const item=make("span",{class:"legend-item"});const swatch=make("span",{class:"swatch"});swatch.style.background=colorForModel(model);item.append(swatch,make("span",{},model));models.appendChild(item);}
  const efforts=byId("effort-legend");efforts.replaceChildren();for(const [effort,style] of Object.entries(chart.color_keys.effort_styles)){const item=make("span",{class:"legend-item"});const line=make("span",{class:"legend-line"});line.style.borderTopStyle=style.dash?"dashed":"solid";item.append(line,make("span",{},effort));efforts.appendChild(item);}
}
function setValueAxis(root,height,max,label,digits=0){
  const top=26,bottom=height-34,left=190,right=982;
  root.appendChild(svg("text",{x:13,y:16},label));root.appendChild(svg("line",{x1:left,y1:top,x2:left,y2:bottom,class:"axis"}));root.appendChild(svg("line",{x1:left,y1:bottom,x2:right,y2:bottom,class:"axis"}));
  for(let i=0;i<=4;i++){const x=left+(right-left)*i/4;root.appendChild(svg("line",{x1:x,y1:top,x2:x,y2:bottom,class:"grid"}));root.appendChild(svg("text",{x,y:height-12,"text-anchor":i===0?"start":i===4?"end":"middle"},fmt(max*i/4,digits)));}
  return {left,right,top,bottom};
}
function applySelectionToCharts(){for(const element of document.querySelectorAll("[data-node-id], [data-node-ids]")){const ids=(element.dataset.nodeIds||element.dataset.nodeId||"").split(/\s+/).filter(Boolean);const active=state.selected.size===0||ids.some(id=>state.selected.has(id));element.classList.toggle("dimmed",!active);}}
function render() {
  const selected=selectedCalls();renderTable(selected);renderSwim(selected);renderContext(selected);renderCost(selected);renderComposition(selected);applySelectionToCharts();
  byId("window-start").value=new Date(state.start).toISOString();byId("window-end").value=new Date(state.end).toISOString();
  const span=Math.max(1,state.fullEnd-state.fullStart);byId("start-brush").value=String(Math.round((state.start-state.fullStart)/span*1000));byId("end-brush").value=String(Math.round((state.end-state.fullStart)/span*1000));
  byId("report-window").textContent=`[${report.window.since}, ${report.window.until}) UTC · ${report.filters.no_labels?"labels stripped from this output":"labels may contain sensitive sidecar descriptions"}`;
}
function setRange(start,end){const min=state.fullStart,max=state.fullEnd;state.start=Math.max(min,Math.min(Number(start),max-1));state.end=Math.max(state.start+1,Math.min(Number(end),max));render();}
function setRangeFromBrush(){const span=state.fullEnd-state.fullStart;let start=state.fullStart+span*(Number(byId("start-brush").value)/1000);let end=state.fullStart+span*(Number(byId("end-brush").value)/1000);if(start>=end){if(document.activeElement===byId("start-brush"))start=Math.max(state.fullStart,end-span/1000);else end=Math.min(state.fullEnd,start+span/1000);}setRange(start,end);}
function configureFilters(){
  const models=[...new Set(allCalls.map(call=>call.model))].sort();for(const value of models)byId("model-filter").appendChild(make("option",{value},value));
  const efforts=[...new Set(allCalls.map(call=>call.effort||"unknown"))].sort();for(const value of efforts)byId("effort-filter").appendChild(make("option",{value},value));
  byId("model-filter").addEventListener("change",render);byId("effort-filter").addEventListener("change",render);byId("topic-filter").addEventListener("input",render);
  for(const button of document.querySelectorAll("[data-measure]"))button.addEventListener("click",()=>{state.measure=button.dataset.measure;for(const other of document.querySelectorAll("[data-measure]"))other.setAttribute("aria-pressed",String(other===button));render();});
  for(const button of document.querySelectorAll("th button[data-sort]"))button.addEventListener("click",()=>{const key=button.dataset.sort;if(state.sort===key)state.descending=!state.descending;else{state.sort=key;state.descending=key!=="label"&&key!=="model"&&key!=="effort"&&key!=="start";}render();});
  byId("window-start").addEventListener("change",()=>{const parsed=Date.parse(byId("window-start").value);if(Number.isFinite(parsed))setRange(parsed,state.end);else render();});
  byId("window-end").addEventListener("change",()=>{const parsed=Date.parse(byId("window-end").value);if(Number.isFinite(parsed))setRange(state.start,parsed);else render();});
  byId("start-brush").addEventListener("input",setRangeFromBrush);byId("end-brush").addEventListener("input",setRangeFromBrush);
  byId("anonymize-labels").addEventListener("change",event=>{state.anonymize=event.target.checked;render();});
  const zoom=factor=>{const center=(state.start+state.end)/2,half=(state.end-state.start)*factor/2;setRange(center-half,center+half);};
  byId("zoom-in").addEventListener("click",()=>zoom(.5));byId("zoom-out").addEventListener("click",()=>zoom(2));
  byId("pan-left").addEventListener("click",()=>{const delta=(state.end-state.start)*.25;setRange(state.start-delta,state.end-delta);});
  byId("pan-right").addEventListener("click",()=>{const delta=(state.end-state.start)*.25;setRange(state.start+delta,state.end+delta);});
  byId("reset-time").addEventListener("click",()=>setRange(state.fullStart,state.fullEnd));
  const checkbox=byId("anonymize-labels");if(report.filters.no_labels){checkbox.disabled=true;checkbox.setAttribute("aria-label","Labels were removed before this report was serialized");}
  for(const root of [byId("context-chart"),byId("swim-chart"),byId("cost-chart")]){
    root.addEventListener("dblclick",()=>setRange(state.fullStart,state.fullEnd));
    root.addEventListener("pointerdown",event=>{if(event.target.closest("[data-call-id],[data-event-id],button"))return;state.drag={x:event.clientX,shift:event.shiftKey,start:state.start,end:state.end,rect:root.getBoundingClientRect()};root.setPointerCapture?.(event.pointerId);});
    root.addEventListener("pointerup",event=>{const drag=state.drag;if(!drag)return;state.drag=null;const width=Math.max(1,drag.rect.width);const dx=event.clientX-drag.x;const fraction=Math.abs(dx)/width;if(fraction<.015)return;const delta=(drag.end-drag.start)*dx/width;if(drag.shift)setRange(drag.start-delta,drag.end-delta);else{const x1=Math.min(drag.x,event.clientX),x2=Math.max(drag.x,event.clientX);const left=drag.rect.left+drag.rect.width*.065,right=drag.rect.left+drag.rect.width*.917;const f1=Math.max(0,Math.min(1,(x1-left)/(right-left))),f2=Math.max(0,Math.min(1,(x2-left)/(right-left)));setRange(drag.start+(drag.end-drag.start)*f1,drag.start+(drag.end-drag.start)*f2);}});
    root.addEventListener("pointercancel",()=>{state.drag=null;});
  }
}
function populateProvenance(){
  const price=report.extensions.prices||{};const anchors=report.extensions.limit_anchors||{};const coverage=report.coverage||{};
  byId("generated-at").textContent=report.generated_at||"unknown";byId("report-call-count").textContent=fmt(report.totals?.calls||allCalls.length);byId("scanned-files").textContent=fmt(coverage.scanned_files||0);
  byId("weights-file").textContent=rendererMeta.weights_file||"unknown";byId("weights-date").textContent=price.retrieved_on||"unknown";byId("weights-hash").textContent=price.weights_sha256||"unknown";
  const warningCount=Object.values(coverage.warnings||{}).reduce((sum,value)=>sum+(Number(value)||0),0);
  byId("coverage-status").textContent=coverage.partial?`Coverage partial · ${fmt(warningCount)} diagnostics`:`Coverage complete within ${fmt(coverage.scanned_files||0)} scanned files`;
  byId("footer-provenance").textContent=`Generated ${report.generated_at||"unknown"}; window [${report.window.since}, ${report.window.until}) UTC; ${fmt(report.totals?.calls||allCalls.length)} calls across ${fmt(coverage.scanned_files||0)} transcript files; weights ${rendererMeta.weights_file||"unknown"} retrieved ${price.retrieved_on||"unknown"}.`;
  byId("lower-bound-note").textContent=`Observed local share is activity in this report only. Other devices and shared account surfaces may contribute; local activity is a lower bound on total account activity. Estimated limit percentage: ${anchors.estimated_limit_pct??"unknown"}.`;
  byId("privacy-note").textContent=report.filters?.no_labels?"Labels were removed before serialization. Message, prompt, tool content and filesystem paths are excluded.":"Message, prompt, tool content and filesystem paths are excluded. Labeled HTML files may contain sensitive sidecar descriptions; keep them local and protect the file.";
}
function start(){
  if(report.schema_version!=="usage-report-v1"||chart.schema_version!=="usage-charts-v1")throw new Error("Unsupported usage report contract");
  if(!Number.isFinite(state.fullStart)||!Number.isFinite(state.fullEnd)||state.fullStart>=state.fullEnd)throw new Error("Invalid report window");
  populateProvenance();renderLegends();configureFilters();render();byId("usage-report-ready").dataset.ready="1";
}
start();
})();
</script>
</body>
</html>
'''


__all__ = ["render_html"]
