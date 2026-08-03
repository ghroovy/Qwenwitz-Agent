# Instruction-Fidelity Benchmark

**Suite:** `benchmarks/fidelity_suite.json` (25 cases, parsed from the supplied
source) + `benchmarks/fidelity_extra_suite.json` (2 seeded cases: repair,
modify) = **27 cases**. Run: `benchmarks/evaluate_fidelity.py`.

## What is measured

For every case the agent runs in an isolated temp workspace (never the real
mod) and is scored on:

1. **Object budget** — requested objects vs generated objects, per kind
   (`strict.count_objects` parses blocks structurally). Generated > requested
   fails unless the extra object is a dependency the prompt requires (the idea
   a focus grants, the second event in an explicitly requested chain).
2. **File budget** — at most 2 files for a single object (content +
   required localisation); 3 for focus+idea cases. No scope-creep files.
3. **Vanilla validity** — balanced blocks, validators pass, no invented
   identifiers, documented effects/triggers/modifiers (against the installed
   2026 build's official docs).
4. **Prompt-derived checks** — each case has vanilla-verified requirement
   checks (focus id/cost/reward, event options/triggers, decision
   target/transfer, scripted constructs, modifiers, tech/equipment keys…).
5. **Idempotence** — run once, apply, run again: the second run must be a
   no-op ("already exists", zero duplicate ids, zero workspace changes).
6. **Preservation** — workspace files are hashed before/after the second run;
   anything outside the requested change counts as a failure.

Expected answers are treated as *examples only*. Where they disagreed with the
installed vanilla, vanilla won (see `vanilla_comparison_report.md`).

## Pass history

| Pass | Suite | Stress (randomized) | Notes |
|---|---:|---:|---|
| 1 | 11/27 (41%) | 182/200 | routing, idempotence, harness gates |
| 2 | 22/27 (81%) | 182/200 | dispatch order, country-target preference, validator allowed-ids |
| 3 | 26/27 (96%) | 182/200 | fragment paths, modify handler, token allow-list |
| 4 | **27/27 (100%)** | **300/300** | id-extraction per kind, isolated stress, seeded localisation |
| 5 | 27/27 (100%) | 300/300 | clean re-run |
| 6 | 27/27 (100%) | 300/300 | clean re-run |

## Root causes fixed along the way

1. **Plural/tree phrasing blocked snippets** ("prerequires two other focuses"
   is descriptive, not a tree request) → blocker refined with a create-verb +
   plural rule and a `prerequ` exception.
2. **New-country misfire**: "Add a national spirit for Austria…" matched
   `nation` inside "national" → new-country flow. Fixed with word boundaries
   in `_detect_new_country`.
3. **"focused" hijacked focus routing** ("offensive-focused trait") →
   `\bfocus\b` in dispatch and harness gates.
4. **Trigger-block requests hijacked by focus dispatch** (complex/nested
   trigger cases) → moved before focus in dispatch order.
5. **Idempotence**: re-running a prompt silently renumbered ids
   (`ITA_mare_nostrum_2`, `_3`, …) because workspace collisions fed back into
   id generation → workspace collisions now mean "already exists" (no-op);
   only vanilla-index collisions trigger renumbering.
6. **Fragment files under `snippets/` could not be applied** (filesystem
   classify rejected the prefix) → `snippets/` added as a writable neutral
   directory (ignored by the game).
7. **"United States" prerequisite**: "Great Depression-related prerequisites"
   picked an alphabetical USA focus instead of `USA_continue_the_new_deal` →
   explicit rule order.
8. **Country-target preference**: "for Poland … against Germany" resolved to
   GER → target-preposition extraction picks the "for <country>" target.
9. **Validator allowed-ids**: lowercase block-defined ids (declared ideas,
   techs, equipment) were flagged unknown → the proposal pre-pass now declares
   them.
10. **Modify support**: "change the cost of focus X to N" now performs a
    single-line edit inside exactly that focus block (see
    `minimal_diff_report.md`).

## Remaining limitations

- Modify currently supports the focus-cost field specifically; other
  single-field edits (icon, x/y, prerequisites) are not implemented yet.
- `snippets/` fragment outputs (ai_will_do block, available block) are
  paste-in fragments; the game ignores the directory.
- Random stress templates cover 13 request shapes × 20 countries; broader
  shapes would need more templates (extend `TEMPLATES`).
