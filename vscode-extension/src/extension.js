// SPDX-License-Identifier: MIT
"use strict";

const vscode = require("vscode");
const fs = require("fs");
const path = require("path");
const { Hoi4Rpc, defaultPythonPath } = require("./rpc");
const { sidePanelHtml, chatHtml, diffViewerHtml } = require("./panels");
const { previewHtml } = require("./previewHtml");

let rpc = null;
let repoRoot = null;
let wsRoot = null;
let sidePanelView = null;
let chatView = null;
let diffPanel = null;
let previewPanel = null;
let previewKind = "map";
let previewPath = "";
let previewMode = "province";
let pendingQuestion = null;

function findRepoRoot(start) {
  let d = start;
  while (d) {
    if (fs.existsSync(path.join(d, "hoi4_agent", "server.py"))) return d;
    const parent = path.dirname(d);
    if (parent === d) break;
    d = parent;
  }
  return start;
}

function resolveWorkspaceRoot(repoRoot, config) {
  const setting = config.get("workspacePath", "");
  if (setting) return setting;
  try {
    const envText = fs.readFileSync(path.join(repoRoot, ".env"), "utf8");
    const line = envText.split(/\r?\n/).find((l) => l.startsWith("HOI4_WORKSPACE_PATH="));
    if (line) {
      return line.slice("HOI4_WORKSPACE_PATH=".length).trim().replace(/^["']|["']$/g, "");
    }
  } catch (err) { /* no .env; fall back to default */ }
  return path.join(repoRoot, "workspace");
}

const PHASES = {
  createFeature: ["Planning...", "Searching vanilla...", "Generating...", "Repairing...", "Validating...", "Ready to apply"],
  repair: ["Inspecting project...", "Classifying failures...", "Repairing...", "Validating...", "Ready to apply"],
  merge: ["Scanning mods...", "Detecting conflicts...", "Merging...", "Validating...", "Ready to apply"],
  refactor: ["Scanning for duplicates...", "Refactoring...", "Validating...", "Ready to apply"],
  explain: ["Tracing dependencies...", "Checking identifiers...", "Running validators..."],
  transfer: ["Scanning countries...", "Transferring states...", "Validating...", "Ready to apply"],
  default: ["Working..."],
};

function chatRouter(text) {
  const low = text.toLowerCase();
  if (/(new country|create a country|add a country|new nation|create a nation)/.test(low) ||
      /\b(make|create|add|turn|form)\b[\s\S]*\b(country|nation)\b/.test(low)) {
    return { m: "create_feature", p: { request: text } };
  }
  // Generic code-snippet requests with no country: the server routes them to
  // the snippet engine, which appends into the currently open file when it is
  // applicable.
  if (/\b(write|create|add|make|build)\b[\s\S]*(focus|event|decision|scripted|spirit|idea|technology|equipment|division|modifier|on_action|ai strategy|character|state history|country history|national spirit|template)/.test(low)) {
    return { m: "create_feature", p: { request: text } };
  }
  if (/(add|create|make|new).*(focus|branch|tree|event|decision|idea|path)/.test(low)) return { m: "create_feature", p: { request: text } };
  // Order of battle / army requests need the multi-step OOB flow (name +
  // spawn questions), so they must go through create_feature.
  if (/\b(oob|order of battle|starting army)\b/.test(low) ||
      (/\barmy\b/.test(low) && !/\barmy experience\b/.test(low)) ||
      /\b\d+\s+divisions?\b/.test(low) ||
      (/\bdivisions?\b/.test(low) && /\b(add|create|make|new|spawn|build)\b/.test(low))) {
    return { m: "create_feature", p: { request: text } };
  }
  if (/\b(merge|combine)\b/.test(low)) return { m: "merge", p: { request: text } };
  if (/\b(refactor|dedup)\b/.test(low)) return { m: "refactor", p: { request: text } };
  if (/\b(transfer|give)\b/.test(low) && /\bstates?\b/.test(low)) return { m: "create_feature", p: { request: text } };
  if (/\b(change|modify|edit|update)\b[\s\S]*\bfocus\b/.test(low)) return { m: "create_feature", p: { request: text } };
  if (/\b(remove|clear|strip|delete|drop)\b[\s\S]*(focus|decision|event|idea|spirit)/.test(low)) return { m: "create_feature", p: { request: text } };
  if (/\b(explain|why|root cause)\b/.test(low)) return { m: "explain", p: { request: text } };
  if (/\b(validate|check)\b/.test(low)) return { m: "validate", p: {} };
  if (/\b(fix|repair|broken|error)\b/.test(low)) return { m: "repair", p: { request: text } };
  return { m: "explain", p: { request: text } };
}

// Relative path of the file currently open in the editor, or "" when there is
// none / it lives outside the mod workspace. Snippet requests with no country
// are routed into this file when it is applicable.
function activeFileRel() {
  const editor = vscode.window.activeTextEditor;
  if (!editor || !wsRoot) return "";
  const rel = path.relative(wsRoot, editor.document.uri.fsPath).split(path.sep).join("/");
  if (!rel || rel.startsWith("..") || path.isAbsolute(rel)) return "";
  return rel;
}

function postChat(msg) {
  if (chatView) chatView.webview.postMessage(msg);
}

function handleCreateResult(result) {
  if (result && result.needs_input) {
    // The country needs an ideology before anything is generated: remember the
    // project so the next chat message answers this question instead of being
    // routed as a brand-new request (e.g. "communist" -> explain).
    pendingQuestion = { slug: result.project_slug, field: result.needs_input };
    postChat({ type: "result", ok: true, text: result.question || "?", pending: [] });
    return;
  }
  showResult("Create Feature", result);
  refreshPanels();
}

function refreshPanels() {
  if (sidePanelView) sidePanelView.webview.postMessage({ type: "refresh" });
  if (diffPanel) diffPanel.webview.postMessage({ type: "refresh" });
}

async function openWorkspaceFile(file, proposedContent) {
  if (!file) return;
  const abs = path.isAbsolute(file) ? file : path.join(wsRoot, file);
  if (fs.existsSync(abs)) {
    const doc = await vscode.workspace.openTextDocument(abs);
    await vscode.window.showTextDocument(doc, vscode.ViewColumn.One);
  } else if (proposedContent) {
    // The file doesn't exist on disk yet: open a preview of the proposed
    // content so the change is reviewable before Accept.
    const doc = await vscode.workspace.openTextDocument({
      language: "plaintext",
      content: proposedContent,
    });
    await vscode.window.showTextDocument(doc, vscode.ViewColumn.One);
    vscode.window.setStatusBarMessage(
      `Qwenwitz preview — proposed content for ${file}. Will be created when you Accept.`, 6000);
  } else {
    vscode.window.showInformationMessage(
      "This file doesn't exist yet — it will be created when you Accept the change: " + file);
  }
}

const PREVIEW_RPC = {
  map: "preview_map",
  focus_tree: "preview_focus_tree",
  events: "preview_events",
  decisions: "preview_decisions",
};

async function fetchPreview() {
  const method = PREVIEW_RPC[previewKind];
  if (!method) return;
  const params = previewPath ? { path: previewPath } : {};
  if (previewKind === "map") params.mode = previewMode;
  const payload = await rpc.call(method, params);
  if (previewPanel) previewPanel.webview.postMessage({ type: "preview", kind: previewKind, payload });
}

async function openPreview(kind, filePath) {
  previewKind = kind;
  previewPath = filePath || "";
  if (!previewPanel) {
    previewPanel = vscode.window.createWebviewPanel(
      "hoi4Preview", "Qwenwitz Preview", vscode.ViewColumn.Beside, { enableScripts: true }
    );
    previewPanel.webview.html = previewHtml();
    previewPanel.webview.onDidReceiveMessage(async (msg) => {
      try {
        if (msg.type === "ready") {
          await fetchPreview();
        } else if (msg.type === "switch") {
          if (msg.kind && PREVIEW_RPC[msg.kind]) {
            previewKind = msg.kind;
            if (msg.kind === "map") previewMode = "province";
            await fetchPreview();
          }
        } else if (msg.type === "map_mode") {
          previewMode = msg.mode || "province";
          await fetchPreview();
        } else if (msg.type === "create_from_states") {
          const states = (msg.states || []).map((n) => parseInt(n, 10)).filter((n) => Number.isInteger(n) && n > 0);
          if (!states.length) return;
          const name = await vscode.window.showInputBox({
            prompt: "Name the new country for the selected states (" + states.length + " state(s))",
            placeHolder: "e.g. Bajookistan",
          });
          if (!name) return;
          const request = `create a new country called ${name} using states ${states.join(", ")}`;
          runWithProgress("Create Feature", PHASES.createFeature, async () => {
            const result = await rpc.call("create_feature", { request });
            handleCreateResult(result);
            return result;
          });
        } else if (msg.type === "transfer_from_states") {
          const states = (msg.states || []).map((n) => parseInt(n, 10)).filter((n) => Number.isInteger(n) && n > 0);
          if (!states.length) return;
          const countries = await rpc.call("list_countries", {});
          const items = [];
          const seen = new Set();
          for (const b of (countries.agent_built || [])) {
            if (!b.tag || seen.has(b.tag)) continue;
            seen.add(b.tag);
            items.push({
              label: `$(globe) ${b.name || b.tag} (${b.tag})`,
              description: "Built by the agent",
              tag: b.tag,
            });
          }
          for (const t of (countries.tags || [])) {
            if (!t.tag || seen.has(t.tag)) continue;
            seen.add(t.tag);
            items.push({
              label: `${t.name || t.tag} (${t.tag})`,
              description: t.source === "workspace" ? "Your mod" : "Vanilla",
              tag: t.tag,
            });
          }
          if (!items.length) {
            vscode.window.showWarningMessage("No countries found to transfer the states to.");
            return;
          }
          const picked = await vscode.window.showQuickPick(items, {
            placeHolder: `Transfer ${states.length} selected state(s) to which country?`,
          });
          if (!picked) return;
          await runWithProgress("Transfer States", PHASES.transfer, async () => {
            const result = await rpc.call("transfer_states", { tag: picked.tag, states });
            showResult("Transfer States", result);
            refreshPanels();
            return result;
          });
        } else if (msg.type === "pick") {
          if (previewKind === "map") {
            vscode.window.showInformationMessage(
              "The map always shows vanilla data (with workspace states overlaid). " +
              "Use the view-mode dropdown: Province / State / Country / Strategic Region / Supply Area. " +
              "In State mode, click states to select them, then create a country from the selection."
            );
          } else {
            const sub = {
              focus_tree: path.join("common", "national_focus"),
              events: "events",
              decisions: path.join("common", "decisions"),
            }[previewKind];
            if (!sub) {
              vscode.window.showWarningMessage(`No file picker available for this view (${previewKind}).`);
              return;
            }
            const file = await pickPreviewFile("Preview file", sub);
            if (file !== null) {
              previewPath = file;
              await fetchPreview();
            }
          }
        } else if (msg.type === "inspect") {
          const r = await rpc.call("preview_inspect", { kind: msg.kind, id: msg.id });
          if (previewPanel) previewPanel.webview.postMessage({ type: "inspect-result", result: r });
        } else if (msg.type === "ask") {
          postChat({ type: "progress", text: "Explaining " + msg.id + "..." });
          const r = await rpc.call("explain", {
            request: `Explain this HOI4 ${msg.kind} \`${msg.id}\` in the context of the current mod.`,
          });
          postChat({ type: "result", ok: true, text: r.summary || JSON.stringify(r) });
        } else if (msg.type === "open") {
          const target = msg.file || "";
          if (!target) return;
          const abs = path.isAbsolute(target) ? target : path.join(repoRoot, target);
          if (fs.existsSync(abs)) {
            const doc = await vscode.workspace.openTextDocument(abs);
            await vscode.window.showTextDocument(doc, vscode.ViewColumn.One);
          }
        } else if (msg.type === "open_file") {
          await openWorkspaceFile(msg.file);
        }
      } catch (err) {
        vscode.window.showErrorMessage(`Qwenwitz Preview: ${err.message}`);
      }
    });
    previewPanel.onDidDispose(() => { previewPanel = null; });
  }
  previewPanel.reveal();
  await fetchPreview();
}

async function pickPreviewFile(label, subPath) {
  const entries = [];
  const roots = [
    { root: path.join(wsRoot, subPath), prefix: "Workspace: " },
    { root: path.join(repoRoot, "data", "raw", "game", subPath), prefix: "Vanilla: " },
  ];
  for (const { root: base, prefix } of roots) {
    if (!fs.existsSync(base)) continue;
    const files = fs.readdirSync(base).filter((f) => f.endsWith(".txt")).sort();
    for (const f of files) entries.push({ path: path.join(base, f), label: prefix + f });
  }
  if (!entries.length) return "";
  const pick = await vscode.window.showQuickPick(
    ["Default (first available)", ...entries.map((e) => e.label)],
    { placeHolder: label }
  );
  if (!pick) return null; // cancelled
  if (pick === "Default (first available)") return "";
  return entries.find((e) => e.label === pick)?.path || pick;
}

function runWithProgress(name, phases, task) {
  return vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: `Qwenwitz: ${name}`, cancellable: false },
    async (progress) => {
      const sequence = phases.length ? phases : PHASES.default;
      let i = 0;
      const timer = setInterval(() => {
        progress.report({ message: sequence[i % sequence.length] });
        postChat({ type: "progress", text: sequence[i % sequence.length] });
        i++;
      }, 350);
      try {
        return await task();
      } finally {
        clearInterval(timer);
      }
    }
  );
}

async function approveAll() {
  const r = await rpc.call("approve");
  postChat({ type: "result", ok: true, text: "Applied: " + (r.applied.join(", ") || "nothing"), pending: [] });
  refreshPanels();
  return r;
}

async function rejectAll() {
  const state = await rpc.call("get_state");
  for (const file of Object.keys(state.pending_diffs || {})) await rpc.call("reject", { file });
  postChat({ type: "result", ok: true, text: "Rejected all pending diffs.", pending: [] });
  refreshPanels();
}

function startPhases(phases, onPhase) {
  const sequence = phases.length ? phases : PHASES.default;
  let i = 0;
  onPhase(sequence[i]);
  const timer = setInterval(() => {
    i++;
    onPhase(sequence[i % sequence.length]);
  }, 350);
  return () => clearInterval(timer);
}

function showResult(name, result) {
  const pf = (result && result.pending_diffs) || (result && result.pending_files) || [];
  const pending = Array.isArray(pf) ? pf : Object.keys(pf);
  postChat({ type: "result", ok: true, text: (result && result.summary) || JSON.stringify(result), pending });
  if (pending.length) {
    const n = pending.length;
    vscode.window.showInformationMessage(
      `Qwenwitz ${name}: ${n} file(s) ready to apply — review the diff, then accept or reject.`,
      "Accept All", "Reject All"
    ).then((choice) => {
      if (choice === "Accept All") approveAll().catch((e) => vscode.window.showErrorMessage(String(e.message)));
      if (choice === "Reject All") rejectAll().catch((e) => vscode.window.showErrorMessage(String(e.message)));
    });
  } else {
    vscode.window.showInformationMessage(`Qwenwitz ${name}: ${(result && result.summary) || "done"}`);
  }
  refreshPanels();
}

async function runCommand(name, rpcCall, phases) {
  try {
    await runWithProgress(name, phases || PHASES[name] || PHASES.default, async () => {
      const result = await rpcCall();
      if (result && result.needs_input) handleCreateResult(result);
      else showResult(name, result);
      return result;
    });
  } catch (err) {
    vscode.window.showErrorMessage(`Qwenwitz ${name}: ${err.message}`);
    postChat({ type: "result", ok: false, text: `Error: ${err.message}` });
  }
}

function activate(context) {
  const config = vscode.workspace.getConfiguration("hoi4Agent");
  const workspaceFolder = vscode.workspace.workspaceFolders && vscode.workspace.workspaceFolders[0].uri.fsPath;
  repoRoot = config.get("repoRoot") ||
    (workspaceFolder && findRepoRoot(workspaceFolder)) ||
    process.env.HOI4_REPO_ROOT ||
    findRepoRoot(__dirname);
  wsRoot = resolveWorkspaceRoot(repoRoot, config);
  const pythonPath = config.get("pythonPath") || defaultPythonPath(repoRoot);
  const serverPath = path.join(repoRoot, "hoi4_agent", "server.py");
  rpc = new Hoi4Rpc(pythonPath, serverPath, { HOI4_WORKSPACE_PATH: wsRoot });
  if (config.get("autoStartServer", true)) {
    rpc.start();
  }

  // ---- views ---------------------------------------------------------------
  context.subscriptions.push(vscode.window.registerWebviewViewProvider("hoi4Agent.sidePanel", {
    resolveWebviewView(view) {
      sidePanelView = view;
      view.webview.options = { enableScripts: true };
      view.webview.html = sidePanelHtml();
      view.webview.onDidReceiveMessage(async (msg) => {
        try {
          if (msg.type === "refresh") {
            const state = await rpc.call("get_state");
            view.webview.postMessage({ type: "state", state });
          } else if (msg.type === "validate") {
            const r = await rpc.call("validate");
            view.webview.postMessage({ type: "state", state: { project_status: r.valid ? "valid" : "invalid" } });
            vscode.window.showInformationMessage(`Qwenwitz validate: ${r.valid ? "all validators pass" : r.errors.length + " error(s)"}`);
          } else if (msg.type === "approve") {
            const r = await rpc.call("approve", { file: msg.file });
            refreshPanels();
            vscode.window.showInformationMessage(`Qwenwitz applied: ${r.applied.join(", ")}`);
          } else if (msg.type === "reject") {
            await rpc.call("reject", { file: msg.file });
            refreshPanels();
          } else if (msg.type === "undo") {
            const r = await rpc.call("undo", { file: msg.file });
            vscode.window.showInformationMessage(`Qwenwitz undo: ${r.undone ? "restored " + msg.file : "no snapshot"}`);
            refreshPanels();
          } else if (msg.type === "open_file") {
            await openWorkspaceFile(msg.file);
          }
        } catch (err) {
          vscode.window.showErrorMessage(String(err.message));
        }
      });
    },
  }));

  context.subscriptions.push(vscode.window.registerWebviewViewProvider("hoi4Agent.chat", {
    resolveWebviewView(view) {
      chatView = view;
      view.webview.options = { enableScripts: true };
      view.webview.html = chatHtml();
      view.webview.onDidReceiveMessage(async (msg) => {
        try {
          if (msg.type === "chat") {
            if (pendingQuestion) {
              const q = pendingQuestion;
              pendingQuestion = null;
              postChat({ type: "progress", text: "Setting " + (q.field || "politics") + "..." });
              const result = await rpc.call("continue_feature", {
                project_slug: q.slug, answer: msg.text, field: q.field,
              });
              handleCreateResult(result);
              return;
            }
            const route = chatRouter(msg.text);
            const params = { ...route.p, active_file: activeFileRel() };
            const stop = startPhases(PHASES[route.m] || PHASES.default, (t) => postChat({ type: "progress", text: t }));
            try {
              const result = await rpc.call(route.m, params);
              postChat({ type: "progress", text: "Ready to apply" });
              if (result && result.needs_input) {
                handleCreateResult(result);
                return;
              }
              showResult(route.m, result);
              refreshPanels();
            } finally {
              stop();
            }
          } else if (msg.type === "approve") {
            await approveAll();
          } else if (msg.type === "reject") {
            await rejectAll();
          } else if (msg.type === "open_file") {
            const file = msg.file;
            const state = await rpc.call("get_state");
            let content = "";
            for (const b of (state.backlog || [])) {
              if (b.contents && b.contents[file]) { content = b.contents[file]; break; }
            }
            await openWorkspaceFile(file, content);
          } else if (msg.type === "review") {
            await vscode.commands.executeCommand("hoi4.showDiff");
          }
        } catch (err) {
          postChat({ type: "result", ok: false, text: "Error: " + err.message });
        }
      });
    },
  }));

  // ---- commands -------------------------------------------------------------
  const cmd = (id, fn) => context.subscriptions.push(vscode.commands.registerCommand(id, fn));

  cmd("hoi4.createFeature", async () => {
    const request = await vscode.window.showInputBox({ prompt: "Feature request (e.g. add a communist path for Canada)", placeHolder: "Add a communist path." });
    if (request) await runCommand("Create Feature", () => rpc.call("create_feature", { request, active_file: activeFileRel() }), PHASES.createFeature);
  });
  cmd("hoi4.repairProject", () => runCommand("Repair Project", () => rpc.call("repair"), PHASES.repair));
  cmd("hoi4.explainError", async () => {
    const request = await vscode.window.showInputBox({ prompt: "Error text to explain", placeHolder: "Explain this error..." });
    if (request) await runCommand("Explain Error", () => rpc.call("explain", { request }), PHASES.explain);
  });
  cmd("hoi4.mergeMods", () => runCommand("Merge Mods", () => rpc.call("merge"), PHASES.merge));
  cmd("hoi4.refactorProject", () => runCommand("Refactor Project", () => rpc.call("refactor"), PHASES.refactor));
  cmd("hoi4.validate", () => runCommand("Validate", () => rpc.call("validate"), ["Running validators..."]));
  cmd("hoi4.findVanillaExample", async () => {
    const query = await vscode.window.showInputBox({ prompt: "Search vanilla code for", placeHolder: "add_political_power" });
    if (query) {
      const r = await rpc.call("find_vanilla_example", { query });
      postChat({ type: "diff", diff: (r.data.examples || []).map((e) => e.file + "\n" + (e.snippet || "")).join("\n---\n") });
    }
  });
  cmd("hoi4.inspectIdentifier", async () => {
    const name = await vscode.window.showInputBox({ prompt: "Identifier", placeHolder: "GER_oppose_hitler" });
    if (name) {
      const r = await rpc.call("inspect_identifier", { name });
      postChat({ type: "diff", diff: JSON.stringify(r.data, null, 2) });
    }
  });
  cmd("hoi4.searchDocumentation", async () => {
    const query = await vscode.window.showInputBox({ prompt: "Documentation query", placeHolder: "add_stability" });
    if (query) {
      const r = await rpc.call("search_documentation", { query });
      postChat({ type: "diff", diff: JSON.stringify(r.data, null, 2) });
    }
  });
  cmd("hoi4.chat", async () => {
    await vscode.commands.executeCommand("workbench.view.extension.hoi4Agent");
  });
  cmd("hoi4.showDiff", async () => {
    const state = await rpc.call("get_state");
    if (!diffPanel) {
      diffPanel = vscode.window.createWebviewPanel("hoi4Diff", "Qwenwitz Diffs", vscode.ViewColumn.Beside, { enableScripts: true });
      diffPanel.webview.html = diffViewerHtml();
      diffPanel.webview.onDidReceiveMessage(async (msg) => {
        const refresh = async () => {
          const s = await rpc.call("get_state");
          if (diffPanel) diffPanel.webview.postMessage({ type: "diffs", backlog: s.backlog || [] });
        };
        if (msg.type === "refresh") {
          await refresh();
        } else if (msg.type === "approve" || msg.type === "reject") {
          await rpc.call(msg.type, { file: msg.file, batch_id: msg.batch_id });
          await refresh();
          refreshPanels();
        } else if (msg.type === "approve_all") {
          const before = await rpc.call("get_state");
          const pending = (before.backlog || []).filter((b) => b.status === "pending");
          if (!pending.length) {
            postChat({ type: "result", ok: true, text: "No pending batches to approve.", pending: [] });
            await refresh();
            return;
          }
          const list = pending
            .map((b) => (b.label || "Pending changes") + (b.project_slug ? " (" + b.project_slug + ")" : "") + " — " + (b.files || []).length + " file(s)")
            .join("\n");
          const choice = await vscode.window.showInformationMessage(
            "Qwenwitz: Apply all " + pending.length + " pending batch(es)?\n" + list,
            { modal: true }, "Apply All", "Cancel");
          if (choice !== "Apply All") {
            postChat({ type: "result", ok: true, text: "Approve All cancelled.", pending: [] });
            await refresh();
            return;
          }
          const r = await rpc.call("approve", { all: true });
          postChat({ type: "result", ok: true, text:
            "Applied all backlog: " + (r.applied.join(", ") || "nothing") +
            (r.failed && r.failed.length ? " | failed: " + r.failed.join("; ") : ""), pending: [] });
          await refresh();
          refreshPanels();
        } else if (msg.type === "open_file") {
          await openWorkspaceFile(msg.file, msg.content);
        } else if (msg.type === "reject_all") {
          const s = await rpc.call("get_state");
          for (const b of (s.backlog || [])) {
            if (b.status === "pending") await rpc.call("reject", { batch_id: b.id });
          }
          postChat({ type: "result", ok: true, text: "Rejected all backlog batches.", pending: [] });
          await refresh();
          refreshPanels();
        }
      });
      diffPanel.onDidDispose(() => { diffPanel = null; });
    }
    diffPanel.webview.postMessage({ type: "diffs", backlog: state.backlog || [] });
    diffPanel.reveal();
  });
  cmd("hoi4.approvedChanges", async () => {
    const state = await rpc.call("get_state");
    const approved = state.approved_files || [];
    if (!approved.length) {
      return vscode.window.showInformationMessage("No approved changes yet.");
    }
    const picked = await vscode.window.showQuickPick(
      approved.map((f) => ({ label: f.file, description: f.label || "approved change", file: f.file })),
      { placeHolder: "Approved changes — pick a file to open" }
    );
    if (picked) await openWorkspaceFile(picked.file);
  });
  cmd("hoi4.undo", async () => {
    const file = await vscode.window.showInputBox({ prompt: "File to undo (relative workspace path)" });
    if (file) {
      const r = await rpc.call("undo", { file });
      vscode.window.showInformationMessage(r.undone ? "Restored " + file : "no undo snapshot");
      refreshPanels();
    }
  });

  // ---- previews ------------------------------------------------------------
  cmd("hoi4.previewMap", () => openPreview("map", ""));
  cmd("hoi4.previewFocusTree", async () => {
    const file = await pickPreviewFile("Focus tree file", path.join("common", "national_focus"));
    if (file !== null) await openPreview("focus_tree", file);
  });
  cmd("hoi4.previewEvents", async () => {
    const file = await pickPreviewFile("Event file", "events");
    if (file !== null) await openPreview("events", file);
  });
  cmd("hoi4.previewDecisions", async () => {
    const file = await pickPreviewFile("Decisions file", path.join("common", "decisions"));
    if (file !== null) await openPreview("decisions", file);
  });
  cmd("hoi4.previewFile", async () => {
    const editor = vscode.window.activeTextEditor;
    if (!editor) return vscode.window.showWarningMessage("Open a HOI4 file first.");
    const rel = vscode.workspace.asRelativePath(editor.document.uri, false);
    const r = await rpc.call("preview_file", { path: rel });
    if (r.kind === "unknown") {
      return vscode.window.showInformationMessage("No preview available for this file type (map, focuses, events, decisions).");
    }
    await openPreview(r.kind, rel);
  });

  // selection actions (editor context menu)
  const selectionCmd = (verb, label) => cmd(`hoi4.${verb}Selection`, async () => {
    const editor = vscode.window.activeTextEditor;
    const text = editor ? editor.document.getText(editor.selection) : "";
    const request = `${label} ${text}`.trim();
    const route = chatRouter(request);
    await runCommand(label, () => rpc.call(route.m, { ...route.p, active_file: activeFileRel() }), PHASES[route.m] || PHASES.default);
  });
  selectionCmd("explain", "explain");
  selectionCmd("repair", "repair");
  selectionCmd("convert", "convert");
  selectionCmd("generate", "generate");
  selectionCmd("refactor", "refactor");

  // code actions on diagnostics
  cmd("hoi4.repairFile", () => runCommand("Repair Automatically", () => rpc.call("code_action", { kind: "quick_fix" }), PHASES.repair));
  cmd("hoi4.generateLocalisation", async (diag) => {
    const ident = diag && diag.identifier;
    if (!ident) return vscode.window.showWarningMessage("No identifier in diagnostic");
    await runCommand("Generate Localisation", () => rpc.call("code_action", { kind: "generate_localisation", identifier: ident }), ["Generating localisation..."]);
  });
  cmd("hoi4.renameIdentifier", async (diag) => {
    const oldName = diag && diag.identifier;
    const newName = await vscode.window.showInputBox({ prompt: "New identifier", value: oldName });
    if (oldName && newName) {
      await runCommand("Rename Identifier", () => rpc.call("code_action", { kind: "rename_identifier", identifier: oldName, new_identifier: newName }), ["Renaming..."]);
    }
  });
  cmd("hoi4.replaceVerified", async (diag) => {
    const oldName = diag && diag.identifier;
    if (!oldName) return;
    await runCommand("Replace with Verified Identifier", () => rpc.call("code_action", { kind: "replace_verified", identifier: oldName }), ["Searching verified identifiers..."]);
  });

  // ---- diagnostics -----------------------------------------------------------
  const diagCollection = vscode.languages.createDiagnosticCollection("hoi4");
  context.subscriptions.push(diagCollection);
  let diagTimer = null;
  const refreshDiagnostics = () => {
    if (diagTimer) clearTimeout(diagTimer);
    diagTimer = setTimeout(async () => {
      try {
        const result = await rpc.call("diagnostics");
        const byFile = new Map();
        for (const e of result.errors || []) {
          if (!e.file) continue;
          const uri = vscode.Uri.file(path.join(wsRoot, e.file));
          const sev = e.type === "missing_localisation" ? vscode.DiagnosticSeverity.Warning : vscode.DiagnosticSeverity.Error;
          const line = Math.max(0, (e.line || 1) - 1);
          const diag = new vscode.Diagnostic(new vscode.Range(line, 0, line, 1), e.message, sev);
          diag.code = e.type;
          diag.source = "hoi4-agent";
          diag.identifier = e.identifier;
          if (!byFile.has(uri)) byFile.set(uri, []);
          byFile.get(uri).push(diag);
        }
        diagCollection.clear();
        for (const [uri, list] of byFile) diagCollection.set(uri, list);
      } catch (err) {
        /* server not ready yet */
      }
    }, 600);
  };
  context.subscriptions.push(vscode.workspace.onDidOpenTextDocument(refreshDiagnostics));
  context.subscriptions.push(vscode.workspace.onDidSaveTextDocument(refreshDiagnostics));
  context.subscriptions.push(vscode.workspace.onDidChangeTextDocument((e) => {
    if (/\.(txt|yml|gui|gfx)$/.test(e.document.fileName)) refreshDiagnostics();
  }));

  // ---- code actions provider ---------------------------------------------------
  context.subscriptions.push(vscode.languages.registerCodeActionsProvider(
    [{ scheme: "file", language: "hoi4" }, { scheme: "file" }],
    {
      provideCodeActions(document, range, context) {
        const actions = [];
        for (const diag of context.diagnostics) {
          const mk = (title, command) => {
            const a = new vscode.CodeAction(title, vscode.CodeActionKind.QuickFix);
            a.command = { command, title, arguments: [diag] };
            return a;
          };
          actions.push(mk("Qwenwitz: Quick Fix (repair)", "hoi4.repairFile"));
          actions.push(mk("Qwenwitz: Repair Automatically", "hoi4.repairFile"));
          if (diag.identifier) {
            actions.push(mk("Qwenwitz: Generate Localisation", "hoi4.generateLocalisation"));
            actions.push(mk("Qwenwitz: Rename Identifier", "hoi4.renameIdentifier"));
            actions.push(mk("Qwenwitz: Replace with Verified Identifier", "hoi4.replaceVerified"));
          }
        }
        return actions;
      },
    },
    { providedCodeActionKinds: [vscode.CodeActionKind.QuickFix] }
  ));
}

function deactivate() {
  if (rpc) rpc.stop();
}

module.exports = { activate, deactivate };
