# Adversarial QA Report

## Method

1. Generated **1,000 realistic user prompts** (`benchmarks/adversarial/corpus.json`)
   imitating Discord/Reddit/Workshop modders: typos, incomplete sentences,
   contradictions, vague requests, error.log snippets, pasted code, mixed
   natural language + code, unicode, malformed braces, duplicate identifiers,
   partially finished mods. Categories: snippets (150), repair (150), projects
   (120), modify (100), vague (100), typo (100), pasted code (100), edge (80),
   contradictory (50), error logs (50).
2. Ran every prompt through the agent in an isolated temp workspace
   (`benchmarks/adversarial/harness.py`) measuring: instruction fidelity,
   files modified, object counts, validator success, repair iterations,
   runtime, idempotence, untouched-file preservation, comment/formatting/
   ordering/line-ending preservation.
3. Every failure was reproduced deterministically, generalized, fixed, and the
   entire regression suite re-run.
4. Targeted deterministic probes for the 30 failure classes
   (`benchmarks/adversarial/probes.py`) — **11/11 pass**.
5. Phase-5 stress suite (`benchmarks/adversarial/stress.py`).

## Corpus results

| Iteration | Failures | Notes |
|---|---:|---|
| v1 | 31 | 26 idempotence + 5 project failures |
| v2 | 0 | after routing/modify/remove fixes |
| v3 | 0 | after CRLF/validator fixes (final code) |

Final behavior: **0 crashes, 0 invalid proposals, 0 preservation failures,
0 idempotence failures, 0 "failed task(s)"** across 1,000 prompts.

## Bugs found and fixed (all generalized)

### 1. agent.run routed project features through a legacy V1 path
Realistic requests ("make a focus tree for canada with 10 focuses", "add an
advisor for australia") fell through to the old `planner.plan` workflow: they
wrote to the **real country focus file** (`canada.txt` instead of the
agent-owned `can_agent_focus.txt`) and re-running appended **duplicate tree
ids** (`CAN_reward_agent_tree` appeared twice). Fix: every remaining project
feature now routes through the same `ProjectExecutor` the extension uses
(unifying CLI/server behavior), staging into the pending review backlog.

### 2. remove_content: retry-loop failure + vanilla-delete risk
"remove all events from the british raj tree" raised "no events file found"
and retried 3× pointlessly; a missing workspace file could fall back to a
**vanilla file** (read-only → apply failure). Fix: missing target file ⇒
graceful no-op ("no changes"); vanilla files are never edited. Country-less
removals now ask which country.

### 3. MODIFY with no resolvable target returned an opaque failure
"change every focus cost to 5" produced "failed task(s): single". Fix: the
MODIFY branch reports a helpful "could not identify what to modify" message
when no country/feature resolves.

### 4. Focus-cost modify conflated "not found" and "already set"
"change the cost of the focus GER_army_recruitment_bolster to 15" (file not in
workspace) claimed the focus "already has that value". Fix: distinct
`not_found` / `already_set` outcomes; a focus with no `cost` line gets one
added rather than failing.

### 5. "prereqs" (no "u") misrouted as a tree request
"write a focus … that prereqs two other focuses" generated a **10-focus tree**
(plural-blocker matched "focuses"). Fix: match the `prereq` prefix
(covers prereq / prereqs / prerequires).

### 6. "add a country hisoory file for spain" became a fake new country
The typo bypassed the artifact-file guard and generated a new country named
"Hisoory File For Spain With Fascist Ruling Party" (tag HIS). Fix: the guard
now covers `country … file/history/tag` across up to 60 characters (typos
between the words).

### 7. CRLF files were destroyed on every edit
Three compounding bugs: `Path.read_text` translated `\r\n`→`\n`; the patch
writer re-translated `\n`→`\r\n` (producing `\r\r\n`); and diff hunks kept a
trailing `\r` that could not match `splitlines()` output (PatchError). Fix:
newline-preserving reads (`filesystem.read_text_keep`), newline-preserving
writes (`newline=""`), EOL-aware diff application, and `\r`-stripping in the
hunk parser. Verified by the CRLF probe (11/11 probes pass).

### 8. Validator missed undocumented triggers/effects on plain lines
`has_annexed = POL` (not in the installed version's docs) passed validation —
only `= {` lines were checked. Fix: verb/trigger-prefixed keys on plain
`=`/`<`/`>` lines are now flagged as invalid_effect/invalid_trigger.
Valid vanilla constructs (`infrastructure > 4`, `threat < 0.5`, `has_war_support
< 0.3`, `is_buildable = yes`) still pass (probe-verified).

### 9. Backlog path was cached at agent init
If the memory directory moved after startup, `_save_backlog`/`_load_backlog`
wrote to a stale path. This broke benchmark isolation and (in one test) wrote
to the real agent-state file. Fix: the backlog path is resolved from
`CONFIG.memory_dir` on every save/load.

### 10. Benchmark hygiene
Benchmark runs pollute `data/projects` (now stubbed in all harnesses) and, in
an earlier broken test, mutated the real `pending_backlog.json` — recovered by
restoring "applied" batches whose edits were never written to the real mod
(102 batches restored to reviewable state).

## Honest remaining weaknesses (not fixed — documented instead)

- **Vague prompts produce low-value answers**: 100/100 vague prompts and
  228 prompts overall route to `explain` (identifier search) or
  `unknown_country` — no crash, but a real user would find it unhelpful. A
  "please clarify" turn for unparseable requests is the natural next step.
- **Repair with nothing to repair** (empty workspace / already-valid mod)
  returns "no validator errors" with no diff — correct but abrupt.
- **Modify coverage** is focused on focus-cost edits; "change every focus
  cost to 5" and "rename all focus ids" are not implemented (they ask
  clarifying questions or report no changes).
- The validator's new plain-line check catches prefix-styled hallucinations
  (`has_annexed`, `add_political_powr`) but not non-prefixed invented triggers
  (`world_tension > 0.5`) — documented limitation.
