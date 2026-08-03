# Remaining Limitations

The 25-case agent suite reaches 100% and is deterministic across runs, but
that does not mean the HOI4 agent is finished. These limitations are
documented honestly; none were caused by model capability within this suite.

## 1. This suite measures snippet generation, not full projects

The 25 cases are country-less code-writing tasks. Larger project-level work
(multi-file focus trees with civil war branches, decisions+events packages,
repair, merge, refactor, new-country scaffolding) is validated by the earlier
project benchmarks and the 109-test backend suite, but was **not re-measured**
by this loop. A combined run (snippet suite + project suite) is the next
measurement step, not an architecture change.

## 2. Snippets are deliberately minimal

Per the user's standing preference, generated snippets do not invent effects:
decisions ship with an empty `complete_effect`, focus trees are skeletons,
and ideas carry no modifiers unless the request names them. Some prompts are
therefore approximated with the closest grounded construct rather than a
fully fleshed feature:

- "initial law" in country history is not emitted (law syntax differs by game
  version; `set_politics` + `set_popularities` are emitted instead).
- "controls at least 80% of a listed set of states" is expressed as
  `controls_state` per listed state + `num_of_controlled_states > N` (the
  `calc_true_if` construct does not exist in the installed version's docs or
  vanilla files).
- "Germany hasn't annexed Austria yet" uses `exists = AUT` (an annexed tag
  ceases to exist), the closest documented trigger.

These approximations are flagged in code comments and this report; they are
grounded, not invented.

## 3. Vocabulary is pinned to the installed game version

The snippet engine and evaluator now use the 2026 installed build's
documented vocabulary. Answers written in older-HOI4 vocabulary (e.g.
`add_timed_event`, `calc_true_if`, `add_guarantee`) would fail validation.
This is intentional: the user's own test showed code that produced in-game
errors, and the fix is alignment with the actual game.

## 4. Snippet routing is heuristic

Country-target detection uses preposition/possessive patterns plus known
country names. Obscure demonym phrasings ("the Wehrmacht focus tree" for a
non-country word) or mixed requests ("make a new country called X with
events") can still route imperfectly. The new-country + events phrasing is
covered by the `_detect_new_country` gate; other odd phrasings would need
more patterns, which we deliberately did not add in this pass.

## 5. No model-capability limitation was found in this suite

Every baseline failure was an engineering gap (missing snippet pipeline,
wrong vocabulary, classifier substrings, validator heuristics, name
extraction). The agent ran promptless for these cases, so the model was never
the bottleneck. If project-level or repair-level failures later show model
capability limits, the loop's documentation protocol applies: name the
missing information, whether a tool would supply it, whether a larger model
would plausibly help, and whether fine-tuning would likely solve it.

## 6. Tools that would help next (not implemented — out of scope)

- A combined snippet + project benchmark runner with per-task timing and
  tool-usage telemetry.
- Wiki/docs cross-checking per generated identifier at snippet time (the
  index already gates this).
- Interactive "install snippet into the mod" flow that places files and
  re-validates the workspace (the pending/approval flow already exists; the
  snippet paths are already real mod paths).

## Recommendation

No retraining, no prompt hacks. The next highest-impact engineering work is
measuring the project-level suite under the same loop, then fixing whatever
routing/validator gaps it exposes.
