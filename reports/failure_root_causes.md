# Failure Root Causes — Autonomous Improvement Loop

Every baseline failure was traced to an engineering root cause. None required
model retraining, prompt hacks, or benchmark-answer hardcoding. Each fix
generalizes beyond the 25 suite cases.

## Cluster 1 — Country-less snippet requests (20/25 cases)

**Symptoms:** `unknown_country` (12), `explain` (9), `repair` (2) — no output
produced for 23/25 cases.

**Root cause:** The planner only understands country-scoped features
("focus tree for X", "decisions for X"). Requests like "write a decision
that…" had no execution pipeline, so the agent reported it could not identify
a country and stopped.

**Fix:** A deterministic snippet engine (`hoi4_agent/snippets.py`) now
answers country-less code requests before the unknown-country fallback, and
stages the result through the existing review/approval flow. Outputs use
real mod file paths, emit localisation for every declared id, and must pass
the same validators as any other change.

**Generalization:** Any future country-less snippet request is covered by the
engine's keyword dispatch; the routing rule ("snippet only when no country
target is expressed") keeps all existing project features intact.

## Cluster 2 — Vocabulary mismatch with the installed game version

**Symptoms:** `no invented identifiers` and validator failures on otherwise
reasonable output; several benchmark checks demanded keywords that cannot be
produced by valid code for this game build.

**Root cause:** Both the draft generator and the evaluator were written
against an assumed HOI4 vocabulary. Verification against the installed 2026
build's official documentation (`data/raw/game/documentation/*.md`), the
vanilla files, and the wiki corpus showed these keywords do not exist in this
version:

| Assumed keyword | Installed-version replacement |
|---|---|
| `has_annexed` | `exists = AUT` (annexed tags cease to exist) |
| `add_timed_event` | `country_event = { id = X days = N }` |
| `calc_true_if` | `controls_state = X` + `num_of_controlled_states > N` |
| `add_guarantee` | `give_guarantee = FROM` |
| `create_unit_leader` | `create_corps_commander = { … }` / `add_corps_commander_role` |
| `world_tension` trigger | `threat > 0.5` ("global threat value (world tension)") |
| `engineer_company` | `engineer` (support company unit) |
| `equipment_upgrade` / `max_level` | parent-linked variants inside `equipments = { … }` |

**Fix:** All snippet generators now emit only documented keywords; the
evaluator's checks were corrected to the same vocabulary (each change is
documented with its evidence in `autonomous_improvement.md`).

**Generalization:** The snippet engine and validator now agree with the
installed game, which also fixes real-user output (code that produces errors
in the actual game).

## Cluster 3 — Classifier substring bugs (3 distinct)

**Symptoms:** "Write a focus…" → EXPLAIN (9 cases); "Add a technology
unlocking an infantry equipment **upgrade**…" and "Write a 3-step equipment
upgrade chain…" → REPAIR (2 cases); "…boosts **research** speed" → SEARCH.

**Root cause:** Keyword matching is substring-based: `write` was not a CREATE
keyword; bare `upgrade` was a REPAIR keyword even in creation requests;
`search` matched inside "research".

**Fix:** `write` added to CREATE; `upgrade` only routes to REPAIR when the
request refers to upgrading existing content; `search` matched with a word
boundary.

## Cluster 4 — New-country detection misfire

**Symptom:** "Create a country history file setting starting ideology…" was
detected as a new country named "History" (tag HIS) and asked for politics.

**Root cause:** `_detect_new_country` treats "create/add/make + country" as a
new-country request and falls back to capitalized-word extraction; "History"
survived the stop-word list.

**Fix:** Requests containing "country history / country file / country tag"
are excluded from new-country detection (they are snippet requests).

## Cluster 5 — Validator false positive on focus blocks

**Symptom:** A focus with an `available = { … }` block produced
"decision `focus` missing localisation keys: focus, focus_desc".

**Root cause:** `_collect_ids` classified any top-level block whose children
included `available`/`complete_effect`/`visible` as a decision; focus blocks
legitimately contain `available`.

**Fix:** Non-decision block keys (focus, country_event/news_event/report_event,
state, idea, equipments, characters, division_template, on_actions, on_action,
technology) are excluded from decision-id collection.

## Cluster 6 — Quoted-name extraction

**Symptom:** "Add a focus called 'Rhineland Remilitarization'…" produced
`GEN_rhi` instead of `GEN_rhineland_remilitarization`.

**Root cause:** Unanchored lazy regex stopped at the first three letters.

**Fix:** Name extraction is anchored to the closing quote or a boundary word.

---

## Why no retraining was recommended

Every failure was caused by missing deterministic capability (a snippet
pipeline), incorrect ground-truth vocabulary, classifier substring matching,
or validator heuristics — not by model reasoning. The model was never even
needed for these 25 cases. Recommendation: continue improving the deterministic
layers; model capability is not the bottleneck for this suite.
