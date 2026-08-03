# Qwenwitz Agent — VS Code Extension

Exposes the Qwenwitz agent (repair, merge, refactor, explain, create feature,
validate) as a VS Code extension with live diagnostics, code actions, a side
panel, a chat panel, a per-file diff viewer with Accept/Reject/Undo, and
interactive previews (world map, focus tree, events, decisions).

## Architecture

```
VS Code webviews (side panel, chat, diff viewer)
  -> extension.js (commands, diagnostics, code actions, progress)
  -> src/rpc.js (spawns the Python agent as a stdio JSON-RPC child process)
  -> hoi4_agent/server.py (thin facade; backend unchanged)
  -> Agent / ProjectExecutor / Tools / Validator / RepairEngine / MergeEngine / RefactorEngine
```

## Install / run (development)

1. Open this folder in VS Code and press `F5` (Extension Development Host), or
   copy the folder to `~/.vscode/extensions/qwenwitz-agent` and reload.
2. Settings: `hoi4Agent.pythonPath` (defaults to `<repo>/.venv/Scripts/python.exe`)
   and `hoi4Agent.repoRoot` (defaults to the first workspace folder; it must
   contain `hoi4_agent/server.py` and `workspace/`).

## Install for end users (VSIX)

The extension is a thin client for the engine in this repository; the VSIX
does not bundle the Python backend. End users:

1. Download the repository and run `.\setup.ps1` once (creates the Python
   environment, writes `.env`, **downloads the Qwen3.5-2B reasoning model**,
   and builds the vanilla index from their game).
2. Install the extension: `code --install-extension qwenwitz-agent-0.1.0.vsix`
   (or install from the VS Code Marketplace once published).
3. Set `Qwenwitz Agent: Repo Root` to the downloaded repository and
   `Qwenwitz Agent: Workspace Path` to their mod folder.
4. Open the **Qwenwitz Agent** activity-bar panel and start chatting.

See `PUBLISHING.md` in the repository root for building/publishing the VSIX and
running the GitHub repository.

## Pointing the agent at your mod

The agent edits exactly one folder: the **workspace**. By default that is
`<repo>/workspace`. To edit your real mod instead:

1. **Easiest:** put your mod path in `<repo>/.env`:
   `HOI4_WORKSPACE_PATH=C:\path\to\your\mod` — then restart the extension host
   (press `F5` again). This works for the CLI too.
2. **Or** use the VS Code setting: `Settings` → search `qwenwitz` →
   `Qwenwitz Agent: Workspace Path` → paste the mod folder. This setting overrides
   the `.env` value.

The vanilla game files stay read-only; only the configured workspace is ever
modified. Previews, diagnostics, repair, merge, refactor, and feature creation
all operate on that folder, and mod-defined identifiers (focuses, events,
decisions, ideas, scripted effects, localisation) are recognized automatically
instead of being flagged as unknown.

## Commands

`Qwenwitz: Create Feature`, `Repair Project`, `Explain Error`, `Merge Mods`,
`Refactor Project`, `Validate`, `Find Vanilla Example`, `Inspect Identifier`,
`Search Documentation`, `Open Chat`, `Review Pending Diffs`, `Undo Last Apply`,
`Preview Map`, `Preview Focus Tree`, `Preview Events`, `Preview Decisions`,
`Preview File`.

Editor context menu: Explain / Repair / Convert / Generate / Refactor on the
current selection, plus **Preview** on any open HOI4 file (focuses, events,
decisions, map data).

## Diagnostics & code actions

Unknown identifiers, broken localisation, scope errors, duplicate ids, missing
icons, and validator failures appear as editor diagnostics (errors/warnings
with the error type as the code). Inline quick fixes: Quick Fix (repair),
Repair Automatically, Generate Localisation, Rename Identifier, Replace with
Verified Identifier.

## Panels

- **Project** (activity bar): task status, pending diffs with per-file
  Accept/Reject/Undo, verified identifiers, modified files, notes.
- **Chat**: type requests like "Add a communist path.", "Repair my mod.",
  "Explain this error." — progress phases appear inline (Planning... Searching
  vanilla... Generating... Repairing... Validating... Ready to apply), then
  Accept All / Reject All for pending diffs.
- **Review Pending Diffs**: per-file unified diffs with Accept/Reject buttons.
- **Preview** (side panel): a single webview with Map / Focus Tree / Events /
  Decisions tabs. The map renders the actual vanilla provinces (from
  `provinces.bmp` + `definition.csv`); hovering shows the province id and
  owner, clicking opens the province details (state, terrain, owner), and a
  political-color toggle colors provinces by owner. Focus trees and event
  graphs are clickable: a node click shows localisation title/description,
  the source file, and the raw code, then "Ask agent to explain this" hands
  the item to the agent in the Chat panel. Focus Tree / Events / Decisions
  commands let you pick a file (your mod first, then vanilla).

## Benchmark

`node benchmark.js` measures server response, tool, repair, feature-creation,
and simulated webview round-trip latencies; writes `reports/vscode_extension.md`.
