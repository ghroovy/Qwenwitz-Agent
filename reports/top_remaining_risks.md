# Top Remaining Risks — ranked

Date: 2026-08-03 · Ranking: Severity (1-5) / Likelihood (1-5) / User impact /
Implementation effort.

| # | Risk | Sev | Lik | Impact | Effort | Notes |
|---|---|---|---|---|---|
| 1 | Decision edits unsupported: "remove the decision" creates a *new* decision (staged for review, not applied) | 3 | 3 | Medium — confusing, but approval-gated | Medium | Extend the contextual edit engine to decisions (remove/available/effects) |
| 2 | Chat "undo the previous change" not wired to undo; users must use the extension button | 2 | 4 | Low-Medium | Low | Route an undo intent to `undo_applied` |
| 3 | Model repair leaves some errors unfixed (46% strict pass) — honest no-ops, no corruption | 3 | 3 | Medium | High | Validator quote-check + category-aware identifiers (migration report follow-ups) |
| 4 | Snippet idempotence relies on per-kind directory scans; new kinds must remember to add dirs | 2 | 2 | Low | Low | Centralize scan dirs in one place |
| 5 | Concurrent agent processes share `_write_<name>.tmp` staging name | 2 | 1 | Low | Low | Per-process temp suffix |
| 6 | Non-UTF-8 files byte-preserve, but generators that intentionally edit those lines operate on surrogates | 2 | 1 | Low | Medium | Warn on non-UTF-8 workspace files |
| 7 | `.txt` BOM dropped on rewrite (`.yml` BOM preserved) | 1 | 2 | Negligible | Low | Cosmetic |
| 8 | Very large single-file prompts (~240 KB) take ~45 s | 1 | 2 | Low | Low | Length cap with early refusal |
| 9 | Session memory (`steps`/`notes`) unbounded within one process | 1 | 2 | Low | Low | Cap stored steps |

## Blocker assessment

No item is a release blocker: all data-mutating paths are deterministic,
validated, and approval-gated; the acceptance pass produced zero corruptions,
zero duplicate ids, and 100% loadable end states across 110 sessions. Items 1-3
should be the next engineering cycle.
