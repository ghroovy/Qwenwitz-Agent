// SPDX-License-Identifier: MIT
"use strict";

const CSP = "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline';";

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function diffHtml(diffs) {
  const files = Object.keys(diffs || {});
  if (!files.length) {
    return `<p>No pending diffs.</p>`;
  }
  return files.map((file) => `
    <div class="file">
      <div class="file-head">
        <b>${esc(file)}</b>
        <button data-act="approve" data-file="${esc(file)}">Accept</button>
        <button data-act="reject" data-file="${esc(file)}">Reject</button>
      </div>
      <pre>${esc(diffs[file])}</pre>
    </div>`).join("");
}

function sidePanelHtml() {
  return `<!DOCTYPE html><html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="${CSP}">
<style>
body{font-family:var(--vscode-font-family);font-size:12px;padding:8px;color:var(--vscode-foreground)}
h3{margin:12px 0 4px;text-transform:uppercase;font-size:10px;opacity:.7}
ul{margin:2px 0 8px;padding-left:16px}
button{margin:2px;background:var(--vscode-button-background);color:var(--vscode-button-foreground);border:none;padding:3px 8px;cursor:pointer}
pre{background:var(--vscode-textBlockQuote-background);padding:6px;overflow:auto;max-height:220px}
.ok{color:var(--vscode-testing-iconPassed)}
.bad{color:var(--vscode-testing-iconFailed)}
.file-head{display:flex;align-items:center;gap:6px}
.file-link{cursor:pointer;padding:2px 4px;border-radius:3px}
.file-link:hover{background:var(--vscode-list-hoverBackground)}
</style></head><body>
<button data-act="refresh">Refresh</button> <button data-act="validate">Validate</button>
<div id="root"><p>Loading...</p></div>
<script>
const vscode = acquireVsCodeApi();
function render(state){
  const s = state || {};
  const taskRows = (s.task_status || []).map(t =>
    '<li class="' + (t.status === "completed" ? "ok" : t.status === "failed" ? "bad" : "") + '">' + t.id + ': ' + t.status + '</li>').join("");
  const pending = Object.keys(s.pending_diffs || {});
  const diffButtons = pending.length ? pending.map(f =>
    '<li>' + esc(f) + ' <button data-act="approve" data-file="' + esc(f) + '">Accept</button>' +
    '<button data-act="reject" data-file="' + esc(f) + '">Reject</button>' +
    '<button data-act="undo" data-file="' + esc(f) + '">Undo</button></li>').join("") : '<li>none</li>';
  const approved = (s.approved_files || []).map(f =>
    '<li class="file-link" data-file="' + esc(f.file) + '" title="' + esc(f.label || "") + '">' + esc(f.file) + '</li>').join("");
  const verified = Object.keys(s.verified_identifiers || {}).slice(0, 50);
  document.getElementById("root").innerHTML =
    '<h3>Project</h3><div>' + (s.project_status || "idle") + '</div>' +
    '<h3>Tasks</h3><ul>' + taskRows + '</ul>' +
    '<h3>Pending diffs</h3><ul>' + diffButtons + '</ul>' +
    '<h3>Approved changes</h3><ul>' + (approved || '<li>none</li>') + '</ul>' +
    '<h3>Verified identifiers</h3><ul>' + verified.map(v => '<li>' + esc(v) + '</li>').join("") + '</ul>' +
    '<h3>Modified files</h3><ul>' + (s.files_modified || []).map(f => '<li>' + esc(f) + '</li>').join("") + '</ul>' +
    '<h3>Notes</h3><ul>' + (s.notes || []).slice(-20).map(n => '<li>' + esc(n) + '</li>').join("") + '</ul>';
}
window.addEventListener("message", e => { if (e.data.type === "state") render(e.data.state); });
document.addEventListener("click", e => {
  const el = e.target.closest("button[data-act]");
  if (el) return vscode.postMessage({ type: el.dataset.act, file: el.dataset.file });
  const link = e.target.closest(".file-link");
  if (link) vscode.postMessage({ type: "open_file", file: link.dataset.file });
});
vscode.postMessage({ type: "refresh" });
</script></body></html>`;
}

function chatHtml() {
  return `<!DOCTYPE html><html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="${CSP}">
<style>
body{font-family:var(--vscode-font-family);font-size:12px;padding:8px;display:flex;flex-direction:column;height:100vh;box-sizing:border-box}
#log{flex:1;overflow:auto}
.msg{margin:4px 0;padding:6px;border-radius:4px;background:var(--vscode-textBlockQuote-background);white-space:pre-wrap}
.phase{color:var(--vscode-charts-blue);font-size:11px}
.ok{color:var(--vscode-testing-iconPassed)}
.bad{color:var(--vscode-testing-iconFailed)}
#row{display:flex;gap:4px;margin-top:6px}
#input{flex:1}
button{background:var(--vscode-button-background);color:var(--vscode-button-foreground);border:none;padding:4px 10px;cursor:pointer}
.file-chip{display:inline-block;margin:2px 4px 2px 0;padding:2px 8px;border:1px solid var(--vscode-panel-border);border-radius:10px;cursor:pointer;font-size:11px;background:var(--vscode-textBlockQuote-background)}
.file-chip:hover{border-color:var(--vscode-focusBorder)}
.files-label{font-size:10px;text-transform:uppercase;opacity:.7;margin-bottom:2px}
.files-row{margin-top:6px}
</style></head><body>
<div id="log"></div>
<div id="actions" hidden><button data-act="approve">Accept All</button><button data-act="reject">Reject All</button><button data-act="review">Review Diffs</button></div>
<div id="row"><input id="input" placeholder="Add a communist path. / Repair my mod. / Explain this error.">
<button data-act="send">Send</button></div>
<script>
const vscode = acquireVsCodeApi();
const log = document.getElementById("log");
function esc(s){return String(s==null?"":s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")}
function add(cls, text){ const d = document.createElement("div"); d.className = "msg " + cls; d.textContent = text; log.appendChild(d); log.scrollTop = log.scrollHeight; }
function addResult(cls, text, files){
  const d = document.createElement("div");
  d.className = "msg " + cls;
  const t = document.createElement("div");
  t.textContent = text;
  d.appendChild(t);
  if (files && files.length) {
    const row = document.createElement("div");
    row.className = "files-row";
    const label = document.createElement("div");
    label.className = "files-label";
    label.textContent = "Changed files (click to open):";
    row.appendChild(label);
    files.forEach(f => {
      const chip = document.createElement("span");
      chip.className = "file-chip";
      chip.textContent = f;
      chip.dataset.file = f;
      row.appendChild(chip);
    });
    d.appendChild(row);
  }
  log.appendChild(d);
  log.scrollTop = log.scrollHeight;
}
window.addEventListener("message", e => {
  const m = e.data;
  if (m.type === "progress") add("phase", "..." + m.text);
  if (m.type === "result") {
    addResult(m.ok ? "ok" : "bad", m.text, m.pending);
    document.getElementById("actions").hidden = !(m.pending && m.pending.length);
  }
  if (m.type === "diff") { add("ok", m.diff || "(no diff)"); }
});
document.addEventListener("click", e => {
  const chip = e.target.closest(".file-chip");
  if (chip) return vscode.postMessage({ type: "open_file", file: chip.dataset.file });
  const el = e.target.closest("button[data-act]");
  if (!el) return;
  if (el.dataset.act === "send") {
    const text = document.getElementById("input").value.trim();
    if (text) { add("", "> " + text); vscode.postMessage({ type: "chat", text }); document.getElementById("input").value = ""; }
  } else vscode.postMessage({ type: el.dataset.act });
});
document.getElementById("input").addEventListener("keydown", e => {
  if (e.key === "Enter") document.querySelector('button[data-act="send"]').click();
});
</script></body></html>`;
}

function diffViewerHtml() {
  return `<!DOCTYPE html><html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="${CSP}">
<style>
body{font-family:var(--vscode-font-family);font-size:12px;padding:8px}
pre{background:var(--vscode-textBlockQuote-background);padding:6px;overflow:auto;white-space:pre-wrap}
button{margin:2px;background:var(--vscode-button-background);color:var(--vscode-button-foreground);border:none;padding:3px 8px;cursor:pointer}
.batch{margin-bottom:14px;border:1px solid var(--vscode-panel-border);padding:8px}
.file{margin:8px 0 12px;border-left:3px solid var(--vscode-panel-border);padding-left:8px}
.status{opacity:.7;font-size:10px;margin-left:6px}
</style></head><body>
<div style="margin-bottom:8px">
  <button id="approveAll" style="background:var(--vscode-button-background);color:var(--vscode-button-foreground)">Approve All Backlog</button>
  <button id="rejectAll" style="background:var(--vscode-button-secondaryBackground)">Reject All Backlog</button>
</div>
<div id="root"></div>
<script>
const vscode = acquireVsCodeApi();
function esc(s){return String(s==null?"":s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")}
function render(backlog){
  const batches = backlog || [];
  const ordered = [...batches.filter(b => b.status === "pending"),
                   ...batches.filter(b => b.status !== "pending")];
  document.getElementById("root").innerHTML = ordered.length ? ordered.map(b => {
    const isPending = b.status === "pending" || b.status === "partial";
    const statusText = isPending ? "" : " · " + esc(b.status || "done");
    return (
    '<div class="batch"><div><b>' + esc(b.label || "Pending changes") + '</b>' +
    '<span class="status">' + esc(b.project_slug || "") + ' · ' + (b.files || []).length + ' file(s)' + statusText + '</span></div>' +
    (isPending ?
      '<button data-act="approve" data-batch="' + esc(b.id) + '">Accept Batch</button>' +
      '<button data-act="reject" data-batch="' + esc(b.id) + '">Reject Batch</button>' : "") +
    (b.files || []).map(f => {
      const isNew = (b.new_files || []).indexOf(f) >= 0;
      return '<div class="file"><div><b>' + esc(f) + '</b>' +
        (isNew ? '<span class="status"> (new)</span>' : "") +
        ' <button data-act="open" data-batch="' + esc(b.id) + '" data-file="' + esc(f) + '" data-content="' +
        esc((b.contents || {})[f] || "") + '">Open file</button>' +
        (isPending ?
          ' <button data-act="approve" data-batch="' + esc(b.id) + '" data-file="' + esc(f) + '">Accept</button>' +
          '<button data-act="reject" data-batch="' + esc(b.id) + '" data-file="' + esc(f) + '">Reject</button>' : "") +
        '</div><pre>' + esc((b.diffs || {})[f] || "") + '</pre></div>';
    }).join("") +
    '</div>');
  }).join("") : "<p>No batches yet.</p>";
}
window.addEventListener("message", e => { if (e.data.type === "diffs") render(e.data.backlog || e.data.diffs); });
document.addEventListener("click", e => {
  const el = e.target.closest("button");
  if (!el) return;
  if (el.id === "approveAll") return vscode.postMessage({ type: "approve_all" });
  if (el.id === "rejectAll") return vscode.postMessage({ type: "reject_all" });
  if (el.dataset.act === "open") return vscode.postMessage({ type: "open_file", file: el.dataset.file, content: el.dataset.content });
  if (el.dataset.act) vscode.postMessage({ type: el.dataset.act, batch_id: el.dataset.batch, file: el.dataset.file });
});
vscode.postMessage({ type: "refresh" });
</script></body></html>`;
}

module.exports = { sidePanelHtml, chatHtml, diffViewerHtml, diffHtml };
