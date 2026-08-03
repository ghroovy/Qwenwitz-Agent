# Production Readiness — Qwenwitz Agent

Date: 2026-08-03 · Suite: `benchmarks/acceptance/` · Raw data:
[acceptance_results.json](../benchmarks/results/acceptance_results.json)

## Verdict

**YES — I would ship this today**, with three documented feature gaps in the
release notes. The deterministic core survived a 1,309-request end-to-end
acceptance pass with **zero data loss, zero duplicate ids, 100% of sessions
ending in a loadable mod, and no memory/backlog growth** over 250-prompt
sessions. The model layer cannot corrupt a mod (validated, diff-gated,
approval-required) and its migration benchmark improved.

## Method

110 realistic user sessions (1,309 sequential requests), each 10-40 steps,
covering: focus/event/decision lifecycles, contextual follow-ups ("make it
cheaper", "rename it", "move it", "remove its bonus", "make the AI ignore it"),
localisation repair, merge, vanilla-grounded generation, vague/contradictory/
unsafe prompts, and country creation. Every request was measured for success,
clarification need, validator result, repair count, runtime, files touched,
objects created, diff quality, instruction fidelity, and hallucinations.
Additionally: long-session stability (50/100/250 prompts with restart
determinism), a simulated VS Code workflow (8/8), recovery tests (4/4), and
performance probes.

## Results

| Metric | Result |
|---|---|
| Request pass rate | 96.3% (1,309 requests) |
| Sessions ending in a valid, loadable mod | **100%** |
| Duplicate-id sessions | 0 |
| Real hallucinated identifiers | 0 (54 flags were scanner false positives) |
| Unnecessary clarifications | 0 (3 necessary: country politics) |
| Avg request runtime | 13 ms |
| Long sessions (50/100/250) | valid, 0 duplicates, no slowdown, restart-identical |
| VS Code workflow | 8/8 |
| Recovery (partial apply, crash, corrupt state, delete-then-apply) | 4/4 |
| Cold/warm startup | 0.36 s / 0.50 s |
| 3,000-file project scan / incremental edit | 13.9 s / 0.59 s (1 file re-parsed) |

## Bugs found and fixed during this acceptance pass

Every fix was driven by a failing acceptance scenario and verified by the full
regression matrix afterwards:

1. **Diagnostics/validation were stale after edits** — the agent flagged its own
   newly created focuses as unknown identifiers. Validators now refresh the
   workspace registry before disk validation.
2. **A second single focus wiped the first** — snippet creates replaced the
   shared file instead of appending. Creates now merge into existing files.
3. **"Approve All Backlog" duplicated content** — stale stage-time diffs
   appended duplicates when several pending batches shared a file. Approve now
   reconciles against the current disk (append-only for snippet batches,
   key-wise merge for localisation, recomputed diffs for edits).
4. **Repeated snippet creates grew the file exponentially** (12→38→90→194
   lines). Apply-time double-merge eliminated; growth is now linear.
5. **Contextual edits didn't exist** — "make it cheaper / rename / move /
   remove bonus / AI-ignore / add localisation / event option+effect" now edit
   the previously created object deterministically, including resolving
   vanilla-collision renames and last-mentioned targets.
6. **Repair didn't fix duplicate localisation keys** — now deduped
   deterministically.
7. **`_check_scope` was quadratic** (per-line `re.escape` per effect) — patterns
   are pre-compiled; 250-prompt sessions validate in milliseconds.

## Remaining feature gaps (documented, not correctness bugs)

- Decision-object edits ("remove the decision", "add an available condition")
  are unsupported; the agent falls back to snippet creation, which is staged
  for review before anything is written.
- Chat-based "undo the previous change" is not wired to the extension's undo;
  the extension Undo button works (verified 8/8).
- Model-assisted repair is honest but incomplete: strict repair pass 46%
  (Qwen3.5-2B) — it never corrupts, but leaves some errors unfixed.

## Conclusion

The agent is safe, deterministic, and production-usable for the vast majority
of real modding sessions. Ship as v0.1 with the three gaps above in the release
notes; the highest-value next work is decision-object editing and chat undo.
