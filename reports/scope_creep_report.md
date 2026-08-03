# Scope-Creep Report

## The problem being measured

The agent historically generated more than requested: a "focus tree" request
produced ideas, events, decisions, and AI strategy files; a civil-war focus
tree shipped a whole content package. This benchmark enforces the opposite:
exactly what the prompt asks for, nothing else.

## Enforcement

`hoi4_agent/strict.py` infers an execution budget from the literal request and
`benchmarks/evaluate_fidelity.py` counts generated objects structurally
(top-level and nested blocks). Violations are recorded per case:

- `generated > requested` for a kind;
- an unrequested object *kind* appears (unless it is a dependency the prompt
  requires: the idea a focus grants, the second event in an explicit chain);
- files changed exceed the budget (2 for a single object, 3 with an idea
  dependency).

Tree/branch/plural requests ("focus tree", "decisions", "15 focuses") are
routed to the project pipeline by design — the snippet engine only answers
single-object requests.

## Measured results (final pass)

- 27/27 cases: generated objects == requested objects.
- No case produced unrequested files. Focus+idea cases produced exactly 3
  files (focus, idea, localisation); single objects produced 1–2.
- Randomized stress (300 tasks): 300/300 had ≤1 generated object and passed
  validation.

## Regression history

Pass 1 failures included scope violations caused by:

- the `ai_will_do` fragment being miscounted as an "idea" (a block with a
  `modifier` child) — fixed in `count_objects`;
- the old civil-war package behavior for focus-tree requests — fixed in the
  earlier milestone and preserved (regression suite covers it).

No scope-creep failures remain.
