# Regression Report — acceptance suite vs all prior benchmark suites

Date: 2026-08-03

Every suite was re-run after each fix in the autonomous improvement loop.
Final state, all green:

| Suite | Before this pass | After this pass |
|---|---|---|
| Unit/integration tests | 125/125 | **125/125** |
| Agent benchmark (evaluate.py) | 25/25 | **25/25** |
| Instruction fidelity (fixed + stress) | 27/27 + 200/200 | **27/27 + 200/200** |
| Adversarial probes | 11/11 | **11/11** |
| Red-team harness | 28/28 | (unchanged surface; re-verified green) |
| Model migration benchmark | IMPROVED | **IMPROVED** (strict repair 0% → 46%) |
| Acceptance suite | — | 96.3% request pass, 100% loadable, 0 duplicates |

## Regressions observed mid-loop and their resolution

1. **Fidelity idempotence broke for technology/equipment snippets** — the
   snippet workspace-scan directory list didn't cover `common/technologies` /
   `common/units/equipment`, so the second run of the same prompt duplicated
   the object. Fixed by scanning `project_scan.SCAN_DIRS` + those two dirs.
2. **Adversarial probe "CRLF preserved on repair" failed** — root cause: the
   probe's seed (`original[:-2]` on a `\r\n` file) left the file balanced, so
   "nothing to repair" was the *correct* new behaviour (the probe previously
   passed only because the stale validator falsely flagged the mod's own ids).
   The probe seed was corrected to actually break the brace; probes are 11/11.
3. **Rename batches were treated as incremental** (double-appended trees) —
   modify batches now carry `project_slug="modify"` so approve replaces instead
   of merging.

No product regressions remain; every fix was generalized (registry refresh,
merge semantics, scanner coverage, pre-compiled validation) rather than
special-cased to a benchmark case.
