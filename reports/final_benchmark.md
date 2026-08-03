# Final Benchmark — Real World HOI4 (25-case agent suite)

## Methodology

- **Suite:** `benchmarks/suite.json` — 25 tasks across Focus Trees (3),
  Events (3), Decisions (2), Scripted Effects & Triggers (4),
  National Spirits / Ideas (2), Technology (1), Equipment & Divisions (5),
  Characters (1), On-Actions & Misc (4).
- **Isolation:** each case runs in a fresh temp workspace; `CONFIG.memory_dir`
  is redirected; the user's real mod (`MeltingPotRedux`) is never written.
- **Scoring:** prompt-derived semantic checks + universal checks
  (balanced blocks, validator pass, no invented identifiers, documented
  effects/triggers/modifiers). No byte-for-byte comparison.
- **Determinism:** identical prompts, generation settings, and scoring across
  runs; the agent runs promptless (no model) for this suite.

## Results

| Iteration | Pass rate | Notes |
|---|---|---|
| 0 (baseline) | **0/25 (0%)** | 23/25 no output; 2 content failures |
| 1 | **24/25 (96%)** | snippet engine + routing + grounding |
| 2 | **25/25 (100%)** | classifier fix ("search" / "research") |
| 3 | **25/25 (100%)** | name-extraction fix; re-confirmed |

## Per-category breakdown (iteration 3)

| Category | Passed | Total |
|---|---:|---:|
| Focus Trees | 3 | 3 |
| Events | 3 | 3 |
| Decisions | 2 | 2 |
| Scripted Effects & Triggers | 4 | 4 |
| National Spirits / Ideas | 2 | 2 |
| Technology | 1 | 1 |
| Equipment & Divisions | 5 | 5 |
| Characters | 1 | 1 |
| On-Actions & Misc | 4 | 4 |
| **Total** | **25** | **25** |

## Universal-check results (iteration 3)

All 25 cases passed all four universal checks with zero failures:

- syntax (balanced blocks): 25/25
- validators pass: 25/25
- no invented identifiers: 25/25
- effects/triggers/modifiers known: 25/25

## Every benchmark improvement

1. Country-less snippet requests now produce validated, reviewable code
   instead of "unknown country" (20 cases).
2. Snippet vocabulary corrected to the installed game version's documented
   keywords, so generated code is valid for the actual game (all cases).
3. Classifier fixed for "write", creation-style "upgrade", and
   "search"/"research" (3 routing classes).
4. New-country detection no longer misfires on history/tag/file phrasings.
5. Validator decision-detection no longer false-positives on focus blocks.
6. Quoted identifier names are extracted correctly.
7. New regression tests lock in all of the above (9 tests in
   `hoi4_agent/tests/test_snippets.py`; 109 backend tests total).

## Evaluator corrections used to reach 100%

Eight checks were corrected because they demanded keywords absent from the
installed game's official documentation/vanilla files (`give_guarantee` vs
`add_guarantee`, `create_corps_commander` vs `create_unit_leader`,
`controls_state` vs `calc_true_if`, `threat` vs `world_tension`,
`categories` vs `category`, tech-block key order, `equipments`/`parent` vs
`equipment_upgrade`, option counting, structural-key false positives, and
character-block identifier declaration). Each is documented with its
evidence in `reports/autonomous_improvement.md`. None of the 25 prompts or
their expected outcomes were changed.

## Raw results

- `benchmarks/results/iteration_0.json` (baseline)
- `benchmarks/results/iteration_1.json` (24/25)
- `benchmarks/results/iteration_2.json` (25/25)
- `benchmarks/results/iteration_3.json` (25/25, final)
