// SPDX-License-Identifier: MIT
"use strict";

const CSP =
  "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:;";

function previewHtml() {
  const T = String.raw;
  return T`<!DOCTYPE html><html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="${CSP}">
<style>
body{font-family:var(--vscode-font-family);font-size:12px;padding:8px;color:var(--vscode-foreground);box-sizing:border-box}
.toolbar{display:flex;gap:4px;flex-wrap:wrap;margin-bottom:8px}
button{background:var(--vscode-button-secondaryBackground);color:var(--vscode-button-secondaryForeground);border:1px solid var(--vscode-button-border);padding:3px 10px;cursor:pointer;font-size:12px}
button.active{background:var(--vscode-button-background);color:var(--vscode-button-foreground)}
.status{opacity:.7;margin:4px 0 8px;font-size:11px}
#content{position:relative}
canvas{width:100%;height:auto;image-rendering:pixelated;background:#000;cursor:crosshair}
#mapView{display:block;margin:0 auto;cursor:grab;touch-action:none;background:#0b1220}
#mapView:active{cursor:grabbing}
#tip{position:absolute;pointer-events:none;background:var(--vscode-editor-background);border:1px solid var(--vscode-panel-border);padding:2px 6px;font-size:11px;display:none;z-index:5;white-space:nowrap}
svg{width:100%;height:auto;background:var(--vscode-editor-background)}
.node{cursor:pointer}
.node rect{fill:var(--vscode-editorWidget-background);stroke:var(--vscode-focusBorder);stroke-width:1}
.node text{fill:var(--vscode-foreground);font-size:10px;text-anchor:middle;pointer-events:none}
.node:hover rect{stroke:var(--vscode-button-background);stroke-width:2}
.edge{stroke:var(--vscode-focusBorder);stroke-width:1;fill:none}
.edge.mutex{stroke:var(--vscode-errorForeground);stroke-dasharray:4 3}
#list{max-height:420px;overflow:auto;border:1px solid var(--vscode-panel-border)}
.item{padding:4px 6px;cursor:pointer;border-bottom:1px solid var(--vscode-panel-border)}
.item:hover{background:var(--vscode-list-hoverBackground)}
.item b{font-size:11px}
.item .sub{opacity:.7;font-size:10px;margin-left:6px}
#search{width:100%;margin-bottom:6px;box-sizing:border-box}
#info{border-top:1px solid var(--vscode-panel-border);margin-top:8px;padding-top:6px;max-height:240px;overflow:auto;display:none}
#info pre{background:var(--vscode-textBlockQuote-background);padding:6px;white-space:pre-wrap;font-size:11px}
h4{margin:2px 0;font-size:11px;text-transform:uppercase;opacity:.7}
</style></head><body>
<div class="toolbar">
  <button data-kind="map">Map</button>
  <button data-kind="focus_tree">Focus Tree</button>
  <button data-kind="events">Events</button>
  <button data-kind="decisions">Decisions</button>
  <select id="modeSel" style="display:none">
    <option value="province">Province</option>
    <option value="state">State</option>
    <option value="country">Country</option>
    <option value="strategic_region">Strategic Region</option>
    <option value="supply_area">Supply Area</option>
  </select>
  <button id="pickBtn" style="margin-left:auto">Choose file...</button>
</div>
<div class="status" id="status">Loading...</div>
<div id="content"></div>
<div id="tip"></div>
<div id="info"></div>
<script>
const vscode = acquireVsCodeApi();
let currentKind = null;
let payload = null;
let decoded = null;
let selectedStates = new Set();
let mapMode = "province";
let mapCanvas = null;
let mapBase = null;
let view = { scale: 1, ox: 0, oy: 0 };
let dragMoveHandler = null;
let dragUpHandler = null;
let resizeHandler = null;

const $ = (id) => document.getElementById(id);

function esc(s) {
  return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function setStatus(t) { $("status").textContent = t; }

function sourceLabel(p) {
  return p && p.source_root === "workspace"
    ? "Showing YOUR MOD (workspace)"
    : "Showing VANILLA GAME FILES (your workspace has none of these)";
}

function b64ToU16(b64) {
  const bin = atob(b64);
  const out = new Uint16Array(bin.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = bin.charCodeAt(i * 2) | (bin.charCodeAt(i * 2 + 1) << 8);
  return out;
}
function b64ToU8(b64) {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < out.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

let lastInspect = null;

function showInfo(title, lines, raw) {
  const el = $("info");
  el.style.display = "block";
  el.innerHTML = "<h4>" + esc(title) + "</h4>" +
    lines.map((l) => "<div>" + esc(l) + "</div>").join("") +
    (raw ? "<pre>" + esc(raw.slice(0, 4000)) + "</pre>" : "") +
    (lastInspect ? '<div style="margin-top:6px"><button id="askBtn">Ask agent to explain this</button></div>' : "");
  const btn = $("askBtn");
  if (btn) {
    btn.addEventListener("click", () => {
      vscode.postMessage({ type: "ask", kind: lastInspect.kind, id: lastInspect.id });
    });
  }
}

function renderMap(p) {
  const c = document.createElement("canvas");
  c.width = p.width; c.height = p.height;
  c.id = "mapView";
  $("content").innerHTML = "";
  $("content").appendChild(c);
  const controls = document.createElement("div");
  controls.id = "mapControls";
  controls.style.cssText = "margin-top:6px;display:flex;gap:4px;flex-wrap:wrap;align-items:center";
  controls.innerHTML =
    '<button id="zoomInBtn" title="Zoom in">+</button>' +
    '<button id="zoomOutBtn" title="Zoom out">-</button>' +
    '<button id="zoomResetBtn" title="Reset zoom">100%</button>' +
    '<button id="clearSelBtn" style="display:none">Clear selection</button>' +
    '<button id="createCountryBtn" style="display:none;background:var(--vscode-button-background);color:var(--vscode-button-foreground)">Create country from selected states</button>' +
    '<button id="transferBtn" style="display:none">Transfer to existing country…</button>' +
    '<span id="zoomHint" style="opacity:.6;font-size:10px;margin-left:6px">scroll to zoom, drag to pan</span>';
  $("content").appendChild(controls);
  $("tip").style.display = "none";
  mapMode = p.mode || "province";
  const modeSel = $("modeSel");
  modeSel.style.display = "inline-block";
  modeSel.value = mapMode;
  if (mapMode !== "state") selectedStates.clear();
  decoded = {
    ids: b64ToU16(p.ids), owners: b64ToU8(p.owners), overlay: null,
    tagColors: {}, political: false, highlight: !!p.highlight_mask,
    highlightMask: p.highlight_mask ? b64ToU8(p.highlight_mask) : null,
    modeIds: p.mode_ids ? b64ToU16(p.mode_ids) : b64ToU16(p.ids),
    modeMeta: p.mode_meta || [],
    metaById: {}, selectionMask: null,
  };
  (p.mode_meta || []).forEach((m, i) => {
    if (p.mode === "country" || p.mode === "state") decoded.metaById[i + 1] = m;
    else decoded.metaById[m.id] = m;
  });
  p.owner_tags.forEach((tag, i) => {
    let h = 0;
    for (let j = 0; j < tag.length; j++) h = (h * 31 + tag.charCodeAt(j)) & 0xffff;
    decoded.tagColors[i + 1] = hslToRgb((h % 360) / 360, 0.62, 0.8);
  });

  mapBase = document.createElement("canvas");
  mapBase.width = p.width; mapBase.height = p.height;
  mapCanvas = document.createElement("canvas");
  mapCanvas.width = p.width; mapCanvas.height = p.height;
  view = { scale: 1, ox: 0, oy: 0 };

  const img = new Image();
  img.onload = () => {
    const mctx = mapBase.getContext("2d");
    mctx.clearRect(0, 0, p.width, p.height);
    mctx.drawImage(img, 0, 0);
    if (mapMode === "country") {
      recolorPixels(mapBase, decoded.owners, (o) => decoded.tagColors[o] || null);
    } else if (mapMode !== "province") {
      recolorPixels(mapBase, decoded.modeIds, (v) => (v ? idColor(v) : null));
    }
    if (decoded.highlight && decoded.highlightMask) {
      recolorPixels(mapBase, decoded.highlightMask, (v) => (v ? [255, 235, 60] : null));
    }
    rebake();
    sizeCanvas();
    drawVisible();
  };
  img.src = p.image;
  setStatus("World map — " + (p.mode_label || "Province") +
    (p.workspace_overlay ? " (includes your workspace states)" : "") +
    (mapMode === "state" ? " — click states to select, then create a country" : ""));

  wireMapEvents(c, p);
}

function rebuildSelectionMask() {
  if (!decoded) return;
  decoded.selectionMask = new Uint8Array(decoded.modeIds.length);
  if (!selectedStates.size) return;
  for (let i = 0; i < decoded.modeIds.length; i++) {
    if (selectedStates.has(decoded.modeIds[i])) decoded.selectionMask[i] = 1;
  }
}

function updateSelectionStatus() {
  if (mapMode !== "state") return;
  setStatus("State selection — " + selectedStates.size + " state(s) selected" +
    (selectedStates.size ? " — create a country from them below" : " — click states on the map"));
  const btn = $("createCountryBtn");
  if (btn) {
    btn.style.display = selectedStates.size ? "inline-block" : "none";
    btn.textContent = "Create country from selected states (" + selectedStates.size + ")";
  }
  const clr = $("clearSelBtn");
  if (clr) clr.style.display = selectedStates.size ? "inline-block" : "none";
  const tr = $("transferBtn");
  if (tr) tr.style.display = selectedStates.size ? "inline-block" : "none";
}

function clearSelection() {
  selectedStates.clear();
  rebuildSelectionMask();
  if (mapCanvas) rebake();
  updateSelectionStatus();
}

function idColor(id) {
  const hue = ((id * 137.508) % 360) / 360;
  return hslToRgb(hue, 0.55, 0.62);
}

function recolorPixels(target, values, colorFor) {
  const ctx = target.getContext("2d");
  const imgData = ctx.getImageData(0, 0, target.width, target.height);
  const px = imgData.data;
  for (let i = 0; i < values.length; i++) {
    if (!values[i]) continue; // 0 = water/unowned/not-selected: never recolor
    const col = colorFor(values[i]);
    if (!col) continue;
    px[i * 4] = col[0]; px[i * 4 + 1] = col[1]; px[i * 4 + 2] = col[2];
  }
  ctx.putImageData(imgData, 0, 0);
}

function rebake() {
  if (!mapBase || !mapCanvas) return;
  const ctx = mapCanvas.getContext("2d");
  ctx.clearRect(0, 0, mapCanvas.width, mapCanvas.height);
  ctx.drawImage(mapBase, 0, 0);
  if (decoded && decoded.selectionMask) {
    recolorPixels(mapCanvas, decoded.selectionMask, (v) => (v ? [255, 225, 90] : null));
  }
  if (mapCanvas) drawVisible();
}

function canvasPoint(e) {
  const r = $("mapView").getBoundingClientRect();
  return {
    x: (e.clientX - r.left) * ($("mapView").width / r.width),
    y: (e.clientY - r.top) * ($("mapView").height / r.height),
  };
}

function imagePoint(e) {
  const cp = canvasPoint(e);
  return {
    x: (cp.x - view.ox) / view.scale,
    y: (cp.y - view.oy) / view.scale,
  };
}

function clampView() {
  const c = $("mapView");
  if (!c || !mapCanvas) return;
  const vw = mapCanvas.width * view.scale;
  const vh = mapCanvas.height * view.scale;
  if (vw <= c.width) view.ox = (c.width - vw) / 2;
  else view.ox = Math.min(0, Math.max(view.ox, c.width - vw));
  if (vh <= c.height) view.oy = (c.height - vh) / 2;
  else view.oy = Math.min(0, Math.max(view.oy, c.height - vh));
}

function drawVisible() {
  const c = $("mapView");
  if (!c || !mapCanvas) return;
  const ctx = c.getContext("2d");
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, c.width, c.height);
  ctx.imageSmoothingEnabled = false;
  ctx.setTransform(view.scale, 0, 0, view.scale, view.ox, view.oy);
  ctx.drawImage(mapCanvas, 0, 0);
  ctx.setTransform(1, 0, 0, 1, 0, 0);
}

function sizeCanvas() {
  const c = $("mapView");
  if (!c) return;
  const availH = Math.max(260, window.innerHeight - 190);
  const availW = Math.max(320, window.innerWidth - 48);
  const ar = c.width / c.height;
  let w = availW, h = w / ar;
  if (h > availH) { h = availH; w = h * ar; }
  c.style.width = Math.round(w) + "px";
  c.style.height = Math.round(h) + "px";
}

function wireMapEvents(c, p) {
  let dragging = null;
  let suppressClick = false;

  c.addEventListener("mousemove", (e) => {
    if (dragging) {
      const cp = canvasPoint(e);
      view.ox = dragging.ox + (cp.x - dragging.sx);
      view.oy = dragging.oy + (cp.y - dragging.sy);
      clampView();
      drawVisible();
      dragging.moved += Math.abs(cp.x - dragging.sx) + Math.abs(cp.y - dragging.sy);
      return;
    }
    const ip = imagePoint(e);
    const x = Math.floor(ip.x), y = Math.floor(ip.y);
    if (x < 0 || y < 0 || x >= c.width || y >= c.height) {
      $("tip").style.display = "none";
      return;
    }
    const pid = decoded.ids[y * c.width + x];
    if (!pid) { $("tip").style.display = "none"; return; }
    const mid = decoded.modeIds[y * c.width + x];
    const meta = decoded.metaById[mid];
    const tag = decoded.owners[y * c.width + x] > 0
      ? (p.owner_tags[decoded.owners[y * c.width + x] - 1] || "") : "";
    const r = c.getBoundingClientRect();
    const t = $("tip");
    t.style.display = "block";
    t.style.left = (e.clientX - r.left + 14) + "px";
    t.style.top = (e.clientY - r.top + 14) + "px";
    if (mapMode === "state" && meta) {
      t.textContent = "State " + meta.id + " - " + meta.name + (meta.owner ? " - " + meta.owner : "") +
        (selectedStates.has(mid) ? " [SELECTED]" : "");
    } else if (mapMode === "strategic_region" && meta) {
      t.textContent = "Strategic Region " + meta.id + " - " + meta.name;
    } else if (mapMode === "supply_area" && meta) {
      t.textContent = "Supply Area " + meta.id + " - " + meta.name;
    } else if (mapMode === "country" && meta) {
      t.textContent = meta.name || meta.id;
    } else {
      t.textContent = "Province " + pid + (tag ? " - " + tag : "");
    }
  });

  c.addEventListener("mouseleave", () => { $("tip").style.display = "none"; });

  c.addEventListener("mousedown", (e) => {
    const cp = canvasPoint(e);
    dragging = { sx: cp.x, sy: cp.y, ox: view.ox, oy: view.oy, moved: 0 };
    suppressClick = false;
  });
  if (dragMoveHandler) window.removeEventListener("mousemove", dragMoveHandler);
  if (dragUpHandler) window.removeEventListener("mouseup", dragUpHandler);
  dragMoveHandler = (e) => {
    if (!dragging) return;
    const cp = canvasPoint(e);
    view.ox = dragging.ox + (cp.x - dragging.sx);
    view.oy = dragging.oy + (cp.y - dragging.sy);
    clampView();
    drawVisible();
    dragging.moved += Math.abs(cp.x - dragging.sx) + Math.abs(cp.y - dragging.sy);
  };
  dragUpHandler = () => {
    if (dragging) {
      suppressClick = dragging.moved > 6;
      dragging = null;
    }
  };
  window.addEventListener("mousemove", dragMoveHandler);
  window.addEventListener("mouseup", dragUpHandler);

  c.addEventListener("wheel", (e) => {
    e.preventDefault();
    const ip = imagePoint(e);
    const cp = canvasPoint(e);
    const factor = e.deltaY < 0 ? 1.25 : 0.8;
    view.scale = Math.min(12, Math.max(1, view.scale * factor));
    view.ox = cp.x - ip.x * view.scale;
    view.oy = cp.y - ip.y * view.scale;
    clampView();
    drawVisible();
  }, { passive: false });

  c.addEventListener("click", (e) => {
    if (suppressClick) { suppressClick = false; return; }
    const ip = imagePoint(e);
    const x = Math.floor(ip.x), y = Math.floor(ip.y);
    if (x < 0 || y < 0 || x >= c.width || y >= c.height) return;
    const pid = decoded.ids[y * c.width + x];
    if (!pid) return;
    if (mapMode === "state") {
      const sid = decoded.modeIds[y * c.width + x];
      const meta = decoded.metaById[sid];
      if (!meta) return;
      if (selectedStates.has(sid)) selectedStates.delete(sid);
      else selectedStates.add(sid);
      rebuildSelectionMask();
      rebake();
      updateSelectionStatus();
    } else if (mapMode === "strategic_region") {
      const rid = decoded.modeIds[y * c.width + x];
      if (rid) inspect("strategic_region", String(rid));
    } else if (mapMode === "supply_area") {
      const aid = decoded.modeIds[y * c.width + x];
      if (aid) inspect("supply_area", String(aid));
    } else if (mapMode === "country") {
      const o = decoded.owners[y * c.width + x];
      const tag = o > 0 ? (p.owner_tags[o - 1] || "") : "";
      if (tag) inspect("identifier", tag);
    } else {
      inspect("province", String(pid));
    }
  });

  $("zoomInBtn").addEventListener("click", () => {
    view.scale = Math.min(12, view.scale * 1.4);
    clampView();
    drawVisible();
  });
  $("zoomOutBtn").addEventListener("click", () => {
    view.scale = Math.max(1, view.scale / 1.4);
    clampView();
    drawVisible();
  });
  $("zoomResetBtn").addEventListener("click", () => {
    view = { scale: 1, ox: 0, oy: 0 };
    clampView();
    drawVisible();
  });
  $("clearSelBtn").addEventListener("click", clearSelection);

  if (resizeHandler) window.removeEventListener("resize", resizeHandler);
  resizeHandler = sizeCanvas;
  window.addEventListener("resize", resizeHandler);
}

function hslToRgb(h, s, l) {
  const a = s * Math.min(l, 1 - l);
  const f = (n) => {
    const k = (n + h * 12) % 12;
    return l - a * Math.max(-1, Math.min(k - 3, Math.min(9 - k, 1)));
  };
  return [Math.round(f(0) * 255), Math.round(f(8) * 255), Math.round(f(4) * 255)];
}

function renderFocusTree(p, treeIndex) {
  const tree = (p.trees || [])[treeIndex || 0];
  if (!tree) {
    $("content").innerHTML =
      "<p>No focus blocks found in " + esc(p.file || "") + ".</p>" +
      "<p style=\"opacity:.7\">This file may not be a focus tree, or its focuses " +
      "may be commented out. Use <b>Choose file...</b> to pick another file.</p>";
    setStatus("Focus tree — 0 focuses in " + esc(p.file || "?"));
    return;
  }
  decoded = null;
  const header = (p.trees || []).length > 1
    ? '<div style="margin-bottom:6px"><label>Tree: </label><select id="treeSel">' +
      p.trees.map((t, i) => '<option value="' + i + '"' + (i === (treeIndex || 0) ? " selected" : "") + ">" +
        esc(t.id) + " (" + t.country_tags.join(", ") + ")</option>").join("") +
      "</select></div>"
    : "";
  $("content").innerHTML = header;
  const nodes = (tree.nodes || []).filter((n) => n.x != null && n.y != null);
  const byId = {};
  tree.nodes.forEach((n) => { byId[n.id] = n; });
  const xs = nodes.map((n) => n.x), ys = nodes.map((n) => n.y);
  const minX = Math.min.apply(null, xs), maxX = Math.max.apply(null, xs);
  const minY = Math.min.apply(null, ys), maxY = Math.max.apply(null, ys);
  const NW = 900, NH = 560;
  const sx = (maxX - minX) > 0 ? (NW - 120) / (maxX - minX) : 40;
  const sy = (maxY - minY) > 0 ? (NH - 80) / (maxY - minY) : 40;
  const scale = Math.min(sx, sy);
  const ox = (NW - (maxX - minX) * scale) / 2 - minX * scale + 40;
  const oy = (NH - (maxY - minY) * scale) / 2 - minY * scale + 30;
  const px = (n) => n.x * scale + ox;
  const py = (n) => n.y * scale + oy;
  const W = 130, H = 34;
  const edges = p.edges || [];
  let svg = '<svg viewBox="0 0 ' + NW + " " + NH + '">';
  svg += '<defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="var(--vscode-focusBorder)"/></marker></defs>';
  edges.forEach((e) => {
    const a = byId[e.from], b = byId[e.to];
    if (!a || !b || a.x == null || b.x == null) return;
    const cls = e.type === "mutually_exclusive" ? "edge mutex" : "edge";
    svg += '<line class="' + cls + '" x1="' + px(a) + '" y1="' + (py(a) + H) + '" x2="' + px(b) + '" y2="' + py(b) + '" marker-end="url(#arr)"/>';
  });
  nodes.forEach((n) => {
    const title = (n.title || n.id || "").slice(0, 24);
    svg += '<g class="node" data-id="' + esc(n.id) + '" data-kind="focus">';
    svg += '<rect x="' + (px(n) - W / 2) + '" y="' + (py(n) - H / 2) + '" width="' + W + '" height="' + H + '" rx="4"/>';
    svg += '<text x="' + px(n) + '" y="' + (py(n) + 4) + '">' + esc(title) + "</text>";
    svg += "</g>";
  });
  svg += "</svg>";
  $("content").insertAdjacentHTML("beforeend", svg);
  const sel = $("treeSel");
  if (sel) sel.addEventListener("change", () => renderFocusTree(p, parseInt(sel.value, 10)));
  setStatus(sourceLabel(p) + " — focus tree: " + tree.id + " (" + tree.country_tags.join(", ") + ") - " +
    tree.focus_count + " focuses, " + edges.length + " links");
  wireNodeClicks();
  wireNodeTooltips(byId);
}

function wireNodeClicks() {
  document.querySelectorAll(".node").forEach((el) => {
    el.addEventListener("click", () => inspect(el.dataset.kind, el.dataset.id));
  });
}

function wireNodeTooltips(byId) {
  document.querySelectorAll(".node").forEach((el) => {
    el.addEventListener("mousemove", (e) => {
      const n = byId[el.dataset.id];
      if (!n) return;
      const t = $("tip");
      t.style.display = "block";
      t.style.left = (e.offsetX + 14) + "px";
      t.style.top = (e.offsetY + 14) + "px";
      t.textContent = (n.title || n.id) + (n.desc ? " - " + n.desc.slice(0, 120) : "");
    });
    el.addEventListener("mouseleave", () => { $("tip").style.display = "none"; });
  });
}

function renderEvents(p) {
  decoded = null;
  const events = p.events || [];
  const byId = {};
  events.forEach((e) => { byId[e.id] = e; });
  if (events.length > 0 && events.length <= 120) {
    const layer = {};
    const depthOf = (id, seen) => {
      if (layer[id] != null) return layer[id];
      if (seen.has(id)) return 0;
      seen.add(id);
      const e = byId[id];
      let d = 0;
      (e.refs || []).forEach((r) => { if (byId[r]) d = Math.max(d, depthOf(r, seen) + 1); });
      layer[id] = d;
      return d;
    };
    events.forEach((e) => depthOf(e.id, new Set()));
    const cols = {};
    events.forEach((e) => { const d = layer[e.id] || 0; (cols[d] = cols[d] || []).push(e.id); });
    const CW = 250, CH = 40;
    const W = Math.max(1, Object.keys(cols).length) * CW + 20;
    let rows = 1;
    Object.keys(cols).forEach((k) => { rows = Math.max(rows, cols[k].length); });
    const H = rows * CH + 60;
    let svg = '<svg viewBox="0 0 ' + W + " " + H + '">';
    (p.edges || []).forEach((e) => {
      const a = byId[e.from], b = byId[e.to];
      if (!a || !b) return;
      const ax = (layer[a.id] || 0) * CW + 220, ay = (cols[layer[a.id] || 0].indexOf(a.id) + 1) * CH;
      const bx = (layer[b.id] || 0) * CW + 20, by2 = (cols[layer[b.id] || 0].indexOf(b.id) + 1) * CH;
      svg += '<line class="edge" x1="' + ax + '" y1="' + ay + '" x2="' + bx + '" y2="' + by2 + '"/>';
    });
    Object.keys(cols).forEach((d) => {
      cols[d].forEach((id, i) => {
        const e = byId[id];
        const x = d * CW + 20, y = (i + 1) * CH;
        svg += '<g class="node" data-id="' + esc(id) + '" data-kind="event">';
        svg += '<rect x="' + x + '" y="' + (y - 17) + '" width="200" height="34" rx="4"/>';
        svg += '<text x="' + (x + 100) + '" y="' + (y + 3) + '">' + esc((e.title || e.id).slice(0, 26)) + "</text>";
        svg += "</g>";
      });
    });
    svg += "</svg>";
    $("content").innerHTML = svg;
    setStatus(sourceLabel(p) + " — events: " + events.length + " in graph (" + p.files.length + " file(s))");
    wireNodeClicks();
  } else {
    $("content").innerHTML =
      '<input id="search" placeholder="Search ' + events.length + ' events...">' +
      '<div id="list">' + events.map((e) =>
        '<div class="item" data-id="' + esc(e.id) + '" data-kind="event">' +
        "<b>" + esc(e.id) + "</b><span class=\"sub\">" + (e.type || "") +
        (e.title ? " - " + esc(e.title.slice(0, 60)) : "") + "</span>" +
        (e.option_count ? '<span class="sub">' + e.option_count + " options</span>" : "") +
        "</div>").join("") + "</div>";
    setStatus(sourceLabel(p) + " — events: " + events.length + " (list view; up to 120 render as a graph)");
    $("search").addEventListener("input", () => {
      const q = $("search").value.toLowerCase();
      document.querySelectorAll("#list .item").forEach((el) => {
        el.style.display = (el.dataset.id.toLowerCase().includes(q) ||
          (el.textContent || "").toLowerCase().includes(q)) ? "" : "none";
      });
    });
    document.querySelectorAll("#list .item").forEach((el) => {
      el.addEventListener("click", () => inspect(el.dataset.kind, el.dataset.id));
    });
  }
}

function renderDecisions(p) {
  decoded = null;
  const decs = p.decisions || [];
  $("content").innerHTML =
    '<input id="search" placeholder="Search ' + decs.length + ' decisions...">' +
    '<div id="list">' + decs.map((d) =>
      '<div class="item" data-id="' + esc(d.id) + '" data-kind="decision">' +
      "<b>" + esc(d.id) + "</b>" +
      (d.category ? '<span class="sub">' + esc(d.category) + "</span>" : "") +
      (d.title ? '<span class="sub">' + esc(d.title.slice(0, 70)) + "</span>" : "") +
      (d.cost ? '<span class="sub">cost ' + esc(d.cost) + "</span>" : "") +
      "</div>").join("") + "</div>";
  setStatus(sourceLabel(p) + " — decisions: " + decs.length + " in " + esc(p.file || "?"));
  $("search").addEventListener("input", () => {
    const q = $("search").value.toLowerCase();
    document.querySelectorAll("#list .item").forEach((el) => {
      el.style.display = (el.dataset.id.toLowerCase().includes(q) ||
        (el.textContent || "").toLowerCase().includes(q)) ? "" : "none";
    });
  });
  document.querySelectorAll("#list .item").forEach((el) => {
    el.addEventListener("click", () => inspect(el.dataset.kind, el.dataset.id));
  });
}

function render() {
  if (!payload) return;
  if (payload.error) { $("content").innerHTML = "<p>" + esc(payload.error) + "</p>"; return; }
  $("modeSel").style.display = payload.kind === "map" ? "inline-block" : "none";
  if (payload.kind === "map") renderMap(payload);
  else if (payload.kind === "focus_tree") renderFocusTree(payload);
  else if (payload.kind === "events") renderEvents(payload);
  else if (payload.kind === "decisions") renderDecisions(payload);
  else $("content").innerHTML = "<p>Unknown preview kind: " + esc(payload.kind) + "</p>";
}

function inspect(kind, id) {
  lastInspect = { kind, id };
  showInfo("Inspecting " + id + "...", [], "");
  vscode.postMessage({ type: "inspect", kind, id });
}

document.querySelectorAll(".toolbar button[data-kind]").forEach((btn) => {
  btn.addEventListener("click", () => {
    if (btn.dataset.kind === currentKind) return;
    vscode.postMessage({ type: "switch", kind: btn.dataset.kind });
  });
});
$("pickBtn").addEventListener("click", () => vscode.postMessage({ type: "pick" }));
$("modeSel").addEventListener("change", () => {
  vscode.postMessage({ type: "map_mode", mode: $("modeSel").value });
});

document.addEventListener("click", (e) => {
  if (e.target && e.target.id === "createCountryBtn") {
    const states = Array.from(selectedStates)
      .sort((a, b) => a - b)
      .map((v) => (decoded && decoded.metaById[v] ? decoded.metaById[v].id : v))
      .filter((v) => v != null);
    if (!states.length) return;
    vscode.postMessage({ type: "create_from_states", states });
  } else if (e.target && e.target.id === "transferBtn") {
    const states = Array.from(selectedStates)
      .sort((a, b) => a - b)
      .map((v) => (decoded && decoded.metaById[v] ? decoded.metaById[v].id : v))
      .filter((v) => v != null);
    if (!states.length) return;
    vscode.postMessage({ type: "transfer_from_states", states });
  }
});

window.addEventListener("message", (e) => {
  const m = e.data;
  if (m.type === "preview") {
    payload = m.payload;
    currentKind = m.kind;
    document.querySelectorAll(".toolbar button").forEach((b) => {
      b.classList.toggle("active", b.dataset.kind === m.kind);
    });
    render();
  } else if (m.type === "inspect-result") {
    const r = m.result || {};
    const d = r.data || {};
    const lines = [];
    if (r.message) lines.push(r.message);
    if (d.id) lines.push("ID: " + d.id);
    if (d.name) lines.push("Name: " + d.name);
    if (d.province_count != null) lines.push("Provinces: " + d.province_count);
    if (d.state_count != null) lines.push("States: " + d.state_count);
    if (r.localisation && r.localisation.title) lines.push("Title: " + r.localisation.title);
    if (r.localisation && r.localisation.desc) lines.push("Description: " + r.localisation.desc);
    if (d.owner) lines.push("Owner: " + d.owner + (d.state ? " (state " + d.state + ")" : ""));
    if (d.type) lines.push("Type: " + d.type);
    if (d.terrain) lines.push("Terrain: " + d.terrain + (d.coastal ? ", coastal" : ""));
    if (d.option_count != null) lines.push("Options: " + d.option_count);
    if (d.refs && d.refs.length) lines.push("Fires: " + d.refs.join(", "));
    if (d.file) lines.push("File: " + d.file);
    showInfo("Inspect: " + (d.id || ""), lines, d.text || "");
    if (d.file) vscode.postMessage({ type: "open", file: d.file });
  }
});

vscode.postMessage({ type: "ready" });
</script></body></html>`;
}

module.exports = { previewHtml };
