# Red-Team Review — Qwenwitz Agent (release readiness)

Date: 2026-08-03 · Scope: runtime only; no architecture changes, no retraining,
no benchmark changes. Harness: [redteam.py](../benchmarks/redteam/redteam.py),
results: [redteam_results.json](../benchmarks/results/redteam_results.json).

## Result

**28/28 red-team probes pass after fixing 4 real bugs.** Every fix was driven by
a deterministic failing test, is small and general, and full regression is green
afterwards (below).

## Bugs discovered, reproduced, and fixed

### 1. Non-UTF-8 mod files were silently corrupted on every edit

- **Reproduction:** a focus file containing a latin-1 byte in a comment
  (`# café`) was read with `errors="replace"` (byte → U+FFFD) and written back
  as UTF-8 (`0xE9` → `EF BF BD`). Byte-for-byte roundtrip probe failed.
- **Root cause:** encoding assumption in `filesystem.read_text_keep` /
  `write_text`; non-UTF-8 bytes did not survive an edit, damaging comments in
  unrelated parts of the file.
- **Fix:** `surrogateescape` end-to-end (`read_text_keep`, `write_text`,
  backlog save/load, and every read-for-edit path in snippets, project, merge,
  repair, server, agent). Undecodable bytes now roundtrip exactly.

### 2. Active-file routing could wipe an existing non-UTF-8 file

- **Reproduction:** with a latin-1 focus file open, generating a snippet routed
  into it produced a proposal containing *only* the new snippet — the original
  file content disappeared.
- **Root cause:** `SnippetEngine._read_workspace_text` used a strict UTF-8 read;
  the decode error was swallowed and treated as "empty file".
- **Fix:** use `filesystem.read_text_keep` (surrogateescape). Probe now confirms
  existing content and comments are preserved.

### 3. Git-style diffs with timestamps were rejected (or mis-filed)

- **Reproduction:** a diff header `+++ b/ger.txt\t2026-08-03 12:00:01` parsed
  the tab and timestamp into the target path → "hunk context not found" (or a
  tab-named file for pure additions).
- **Root cause:** `_parse_hunks` captured the whole `+++ b/(.+)` line.
- **Fix:** strip the tab-suffix and surrounding quotes from the header path.

### 4. Ambiguous hunk context silently edited the WRONG identical block

- **Reproduction:** a diff targeting the second of two identical focus blocks
  (`@@ -9,6 +9,6 @@`, context lines identical in both blocks) applied the
  change to the **first** block.
- **Root cause:** `_apply_hunks` matched context against the first candidate and
  ignored the hunk header line numbers.
- **Fix:** prefer the candidate nearest the declared `@@ -N` start (tracking the
  cumulative offset from earlier hunks in the same file).

### 5. Idempotence broken: rerunning the same request created `GEN_twice_2`

- **Reproduction:** "add a focus called Twice …" → apply → run the identical
  request again. Expected "already exists"; got a second focus with id
  `GEN_twice_2` (duplicate content).
- **Root cause:** `IdentifierIndex.known_set()` returned the live internal set;
  `Validator.register_workspace` merged workspace ids into the "vanilla" index,
  so `_new_id` renumbered instead of letting the idempotence check fire.
- **Fix:** `known_set()` returns a copy; workspace-defined ids stay out of the
  vanilla index.

## What was tested and held (no bug found)

| Area | Probes (all pass) |
|---|---|
| Path safety | relative/absolute traversal refused; junction escape refused (resolves to read-only vanilla root); vanilla files read-only |
| Patcher | traversal diffs refused; CRLF preserved; malformed diff refused; stale-batch apply cannot clobber external edits; empty proposal deletes only the intended file |
| Parser/validator | 2,500-deep malformed nesting handled; 20k-focus file scans fast; duplicate localisation keys across files detected |
| Agent behaviour | prompt injection via workspace file cannot trigger edits; path-traversal prompts refused gracefully; 240 KB prompt handled; vanilla active_file cannot route a write; repeated edits produce fresh unique ids; apply→re-run is idempotent |
| Soak | 40 sessions: 0 failures, no temp-file leaks, backlog ~25 KB, startup ~0.4 s, corrupted backlog JSON safe, backlog restart applies only pending batches |
| Security | malformed diff via tool refused; server rejects bad params/unknown methods without crashing; code-action rename stays inside the workspace |
| UX | no tracebacks reach the user; clean summaries on refusal |

## Regression status (after all fixes)

- Red-team harness: **28/28**
- Unit/integration tests: **125/125**
- Agent benchmark: **25/25**
- Instruction fidelity: **27/27 + 200/200 randomized stress**
- Adversarial probes: **11/11**
- Model migration benchmark: unchanged verdict (**IMPROVED**, strict repair
  pass 0% → 42%, game-breaking JSON eliminated) — the model layer was not
  touched by this pass.

## Remaining production risks (honest, not fixed)

- **Validator accepts JSON-style quoted keys.** A model output of
  `"id": "GER_..."` passes validation but would break the game. Qwen3.5-2B no
  longer produces this, but the validator gap remains; a quote/JSON-ism check in
  `validate_code` is the highest-value follow-up.
- **Non-UTF-8 handling is now byte-preserving**, but generators that
  *intentionally edit* such lines operate on surrogate characters; the
  validator treats them as ordinary text. Acceptable, but worth a user-facing
  warning when a workspace contains non-UTF-8 files.
- **BOM on `.txt` files is dropped on rewrite** (BOM on `.yml` is preserved,
  which is what HOI4 requires). Cosmetic; game tolerates BOM-less txt.
- **Concurrent agent processes** staging the same file share the
  `_write_<name>.tmp` name in the memory dir. Single-user tool, low risk.
- **Session memory (`steps`/`notes`) grows unbounded** within one long session
  (soak: small after 40 runs). A cap on stored steps is a cheap future
  hardening.
- **Very long prompts (240 KB) take ~45 s.** Bounded, but a chat-layer length
  cap or early "too long" response would improve UX.
- **Junction paths inside the workspace classify as "vanilla"** (read-only —
  safe, but the label is misleading for diagnostics).
- Migration report follow-ups stand: category-aware verified-identifier context
  (Qwen3.5 picked a verified focus where an idea was needed) and the validator
  quote check above.

## Stop condition

No new bugs were found after the fixes (two consecutive full harness passes),
and the remaining items are either low-risk observations or documented
follow-ups — none require architecture changes.
