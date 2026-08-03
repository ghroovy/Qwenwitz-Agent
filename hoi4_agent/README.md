# Qwenwitz Agent — engine

A modular local agent for Hearts of Iron IV modding. The language model is an
optional reasoning layer; **every identifier decision is made by
deterministic tools**.
The agent never guesses: it searches the vanilla identifier index, reads
vanilla examples, validates edits, and only writes after showing a diff and
receiving approval.

## Requirements

- Windows 10/11, Python 3.10+.
- A local Hearts of Iron IV install (the identifier index is built from it;
  game files are never uploaded).
- **A local `Qwen/Qwen3.5-2B` model (~4.5 GB)** — the recommended reasoning
  layer for model-assisted repair/explain responses. Running without a model
  is possible but not recommended (repair/explain quality drops). Smaller Qwen
  models such as `Qwen/Qwen2.5-0.5B-Instruct` or `Qwen/Qwen3.5-0.8B` are also
  supported via the `HOI4_AGENT_MODEL` environment variable.

Install from the repository root (see `../requirements.txt` and
`../setup.ps1`):

```powershell
.\setup.ps1
```

Or manually:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
# reasoning model (recommended):
hf download Qwen/Qwen3.5-2B
```

The runtime loads the model strictly from the local Hugging Face cache
(`HF_HUB_OFFLINE=1`) and never contacts the network. To use a different Qwen
model, set `HOI4_AGENT_MODEL` (e.g. `Qwen/Qwen2.5-0.5B-Instruct`) in `.env`.

## Architecture

```
User
 -> Planner (rule-based intent + tool plan)
 -> Tools (deterministic; 20 tools)
    -> Filesystem (workspace write / vanilla read-only)
    -> IdentifierIndex (prebuilt, never rebuilt at runtime; fuzzy lookups)
    -> Wiki / official docs
    -> Validator (braces, scope, identifiers, duplicates, localisation, refs)
 -> Patch generation (grounded templates; optional model layer for free-form)
 -> Validation gate
 -> show diff -> approval -> apply (unified diff, atomic write)
```

## Modules

| Module | Responsibility |
|---|---|
| `config.py` | paths, safety rules, caps |
| `filesystem.py` | safe path resolution, list/read, vanilla read-only |
| `identifier_index.py` | exact + fuzzy lookup over `data/processed/index/` |
| `validator.py` | code/file/project validation (reuses project validators) |
| `tools.py` | the 20-tool deterministic framework |
| `patcher.py` | unified diff generation, safe application |
| `planner.py` | intent detection + tool plans + status messages |
| `memory.py` | session history, verified/rejected identifiers |
| `agent.py` | the inspect -> propose -> validate -> approve -> apply loop |
| `cli.py` | interactive CLI + `--one-shot` |
| `preview/` | read-only mod previews: world map, focus tree, events, decisions |

## Tools

`list_directory`, `search_files`, `read_file`, `search_identifier`,
`find_similar_identifier`, `inspect_focus`, `inspect_event`, `inspect_decision`,
`inspect_scripted_effect`, `inspect_scripted_trigger`, `search_documentation`,
`search_wiki`, `find_vanilla_examples`, `validate_code`, `validate_focus_tree`,
`validate_events`, `validate_localisation`, `propose_patch`, `apply_patch`,
`show_diff`.

## Previews

The `preview/` package renders grounded read-only previews:

- `map_preview.py` — world map from `map/provinces.bmp` + `definition.csv`
  (per-pixel province ids, owners, political coloring, click -> province info).
  View modes: **Province / State / Country / Strategic Region / Supply Area**
  (state ids are compact indices in the payload so mod state ids > 65535 work).
  In State mode the webview supports clicking states to build a selection and
  "Create country from selected states" hands the grounded state ids to the
  planner, which transfers ownership via `history/states/{sid}-{Tag}.txt`
  overrides (copy of the real state block with `owner`/`add_core_of` replaced).
  "Transfer to existing country…" does the same for any existing vanilla or
  workspace tag (including countries the agent built before, remembered in
  `data/agent_state/built_countries.json`). Creating a country without selecting
  states never fabricates a synthetic `900xxx` state — the country is built
  territory-less (no `set_oob` either) until states are transferred to it.
- `focus_preview.py` — focus trees with coordinates, prerequisites,
  mutually-exclusive links, and localisation titles.
- `event_preview.py` — event graphs (country/news/state events + chained refs).
- `decision_preview.py` — modern and legacy decision files, grouped by category.
- `inspect_preview.py` — click-to-inspect: grounded details + raw code.

Exposed over the server as `preview_map`, `preview_focus_tree`, `preview_events`,
`preview_decisions`, `preview_inspect`, `preview_file`. The VS Code extension
adds Preview commands and a clickable webview (see `vscode-extension/README.md`).

## Grounding rules

1. Any identifier needed by generated code is looked up in the index first.
2. Not found -> the agent says so and offers similar real identifiers; it
   never substitutes a guess.
3. New content ids are declared with a verified country prefix, checked for
   collisions, and get localisation entries in the same patch.
4. Every patch passes `validate_code` + project validators before it can be
   applied, and application is atomic via unified diff.

## CLI

```powershell
# interactive
python -m hoi4_agent.cli

# one-shot, auto-approve
python -m hoi4_agent.cli --one-shot "add a German focus that gives 50 political power" --yes

# one-shot, review diff before applying
python -m hoi4_agent.cli --one-shot "add a German focus that gives 100 political power"
```

Status messages make the workflow visible:
`Searching identifiers... Reading vanilla examples... Reading focus tree...
Generating patch... Validating... Showing diff... Ready to apply.`

## Projects (multi-file features)

One prompt becomes a dependency-ordered task graph:

```powershell
python -m hoi4_agent.cli --project "create a communist branch for Canada" --yes
```

Planner output:

```
Task
├── ideas          (create national spirits)
├── focuses        (create focus tree, depends on ideas)
├── events         (depends on ideas)
├── decisions      (depends on ideas)
├── ai_strategy    (depends on focuses)
├── references     (history wiring, depends on focuses/ideas)
├── localisation   (depends on all content)
├── validate       (whole-project dependency checks)
└── apply          (after approval)
```

Project state (memory, proposals, task status) is saved after every task under
`data/projects/`, so runs can be resumed:

```powershell
python -m hoi4_agent.cli --project "resume <slug>" --yes
```

Interactive commands: `create project "..."`, `status`, `resume <name>`,
`abort`, `show plan`, `show completed`, `show pending`.

Each task runs the existing repair loop; a failing task is retried alone and
the project applies only if every task completed and cross-file dependency
checks pass (no dangling focus/event/idea/icon/localisation references).

## Workspace

`workspace/` is the mod sandbox (writable). The vanilla install
(`HOI4_GAME_PATH` in `.env`) is read-only reference. Session memory is stored
under `data/agent_state/` and is gitignored.
