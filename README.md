# Qwenwitz Agent

Qwenwitz is a local modding agent for Hearts of Iron IV. **Qwen 3.5 2B (`Qwen/Qwen3.5-2B`) is the recommended reasoning layer**: it reasons about prompts and repair requests, and its output is then parsed and grounded by a line of deterministic Python scripts. All decisions about identifiers, validation rules, and file edits are made by deterministic scripts, so the model can reason but never decide on its own. Nothing is written to your mod unless you review the diff and approve it.

Qwenwitz is very early in development and is not recommended for serious modding usage at this current point of development.

## Requirements

- Windows (tested on Windows 10)
- Python 3.10+
- VS Code 1.85+ (for the extension)
- A local Hearts of Iron IV install (for the vanilla index)
- **A local `Qwen/Qwen3.5-2B` model (~4.5 GB)** — the recommended reasoning
  model. Smaller Qwen models (e.g. `Qwen/Qwen2.5-0.5B-Instruct` or
  `Qwen/Qwen3.5-0.8B`) also work; see "Reasoning model" below.

## Install

```powershell
.\setup.ps1
```

`setup.ps1` creates the Python environment, installs dependencies, writes
`.env` with your game + mod paths, **downloads the Qwen reasoning model
(`Qwen/Qwen3.5-2B`)**, and builds the vanilla identifier index from your local
install. It is safe to re-run.

To use a different Qwen model instead:

```powershell
.\setup.ps1 -Model "Qwen/Qwen2.5-0.5B-Instruct"
```

Manual equivalent (if you prefer):

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
# copy .env.example to .env and fill in:
#   HOI4_GAME_PATH=...Hearts of Iron IV
#   HOI4_WORKSPACE_PATH=...\mod\MyMod
#   HOI4_AGENT_MODEL=Qwen/Qwen3.5-2B
hf download Qwen/Qwen3.5-2B
.venv\Scripts\python.exe archive\training\scripts\01_extract_sources.py
.venv\Scripts\python.exe archive\training\scripts\03_make_index.py
.venv\Scripts\python.exe -m hoi4_agent.cli
```

### Pointing the agent at your own mod

The agent only ever edits `HOI4_WORKSPACE_PATH` — your mod folder. In VS Code:

1. Open this repository as the workspace folder and press **F5**, **or** install
   the built VSIX (see [PUBLISHING.md](PUBLISHING.md)).
2. Set `Qwenwitz Agent: Workspace Path` (settings → search `qwenwitz`) to your
   mod folder, e.g. `C:\Users\YOU\Documents\Paradox Interactive\Hearts of
   Iron IV\mod\MyMod`. This overrides the `.env` value for the extension.
3. Use the **Qwenwitz Agent** activity-bar panel: chat, previews, repair,
   merge, refactor — everything is staged as diffs and only applied after you
   click Accept.

The vanilla game files are read-only; your mod is the only writable root.

### Reasoning model (recommended)

**Using Qwen as the reasoning layer is the recommended configuration.**
`Qwen/Qwen3.5-2B` is the default and best-tested model: it powers
model-assisted repair and explain responses, and it never hallucinates
identifiers into your files because every identifier decision is still made
and validated by deterministic tools.

Running **without any reasoning model is possible but not recommended**: the
deterministic agent still works, but repair and explanation quality drops
significantly. Pass `-SkipModel` to `setup.ps1` if you must, or set
`HOI4_AGENT_USE_MODEL=0` in `.env`.

**Other Qwen models are supported.** Any cached Qwen model that provides a
chat template works — for example `Qwen/Qwen2.5-0.5B-Instruct` for a much
smaller (~1 GB) footprint, or `Qwen/Qwen3.5-0.8B`. Set the model with
`HOI4_AGENT_MODEL` in `.env`, or pass `-Model` to `setup.ps1`, then run
`hf download <model>` once so it is cached locally:

```powershell
hf download Qwen/Qwen2.5-0.5B-Instruct
```

The model is loaded strictly from the local Hugging Face cache and never
contacts the network at runtime (transformers runs with `HF_HUB_OFFLINE=1`).

The index is derived from your local game files and never distributed (see
"Which files to omit"). Skipping step 3 works only if you already have a
prebuilt `data/processed/index` from your own install.

For the VS Code extension: open this repository as the workspace folder in
VS Code, press **F5** (Extension Development Host), and use the **Qwenwitz
Agent** chat panel. The extension starts the local agent server automatically.

### Building a VSIX for distribution

```powershell
cd vscode-extension
npx @vscode/vsce package
```

The extension is a companion to this repository: it launches
`hoi4_agent/server.py` from the repo. A standalone marketplace package that
bundles the engine is planned; until then, users install the extension from
this repo.

## Privacy & safety

- Everything runs locally. There is no telemetry, and the vanilla index is built from your local game install.
- Nothing is written without review. Every change lands in a pending-diff review (Accept/Reject per file or batch) before touching your mod.
- The vanilla tree is read-only. Mod files outside the configured workspace are never edited.
