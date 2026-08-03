# Determinism Report

## Identical prompts → identical output

- 1,000 consecutive identical prompts: **0 differing outputs**.
- 10,000 random prompts: every repeated prompt (9,727 pairs) produced the
  **same proposal hash**.
- All generators are deterministic (seeded RNGs only in benchmark tooling, not
  in the agent); no time/randomness leaks into proposals.

## Idempotence (same prompt, same workspace, applied state)

Measured in the fidelity harness and the 1,000-prompt corpus:

- Running a prompt, applying it, then running it again produces **no new
  batch** and **no workspace change** (hash-verified).
- Objects that already exist are reported ("already exists — nothing
  changed") instead of duplicated; id generation never renumbers a workspace
  collision (vanilla-index collisions still get deterministic suffixes).
- Focus-tree projects regenerate byte-identical files, so `_prepare_pending`
  skips them (no duplicate batches).

## Equivalent phrasings

Structural equivalence is preserved for the routing-critical phrasings
(single-object vs tree/plural, "prereq" vs "prerequires", quoted vs unquoted
names). Phrasing that changes the *request* (e.g. "add decisions" vs "add a
decision") intentionally changes the output — that is correct behavior, not
instability.

## Repeatability of the whole suite

The old 25-case suite, the 27-case fidelity suite, and the 11 failure-class
probes are all deterministic and were re-run multiple times during this QA
pass with identical pass rates (25/25, 27/27, 11/11).

## Where determinism is not guaranteed

- The model-backed repair path (`use_model=True`) is non-deterministic by
  nature; every harness and the extension's server run promptless
  (`use_model=False`), so the shipped agent behavior measured here is
  deterministic.
- Workspace-dependent outputs (e.g. "remove all events from X") depend on the
  current files; that is deterministic given the same files.
