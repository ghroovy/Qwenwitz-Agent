# Vanilla Comparison Report

Every expected answer in the supplied benchmark was checked against the
installed 2026 vanilla build: the official game docs
(`data/raw/game/documentation/*.md`), the vanilla game files, and the offline
wiki corpus. Where an expected answer disagreed with vanilla, the vanilla
implementation won and the snippet engine emits the verified construct.

## Keyword verification results

| Expected answer used | Verdict | Vanilla-correct replacement | Evidence |
|---|---|---|---|
| `add_timed_event = { id = X days = 7 }` | invalid | `country_event = { id = X days = 7 }` | absent from effects docs; vanilla chains use `country_event` (e.g. `common/decisions/AUS.txt`) |
| `calc_true_if = { amount = … }` | invalid | `controls_state = X` ×N (+ `num_of_controlled_states` for "at least %") | absent from docs and all vanilla files |
| `create_unit_leader = { … }` | invalid | `create_corps_commander = { … }` | official effects doc documents `create_corps_commander`/`add_corps_commander_role`; vanilla `scripted_effects` use `add_corps_commander_role` |
| `engineer_company = { … }` | invalid | `engineer = { … }` (support company) | vanilla division templates use `engineer` (`history/units/AUS_1936.txt`) |
| `transfer_state_control = { target = ROOT }` | invalid | `transfer_state_to = ROOT` in state scope / `transfer_state = N` in country scope | effects doc has `transfer_state`/`transfer_state_to`; vanilla decisions use `transfer_state_to` |
| `war_support < 0.3` (trigger) | invalid | `has_war_support < 0.3` | vanilla events use `has_war_support` (e.g. `events/AAT_Sweden.txt`); triggers doc documents it |
| `world_tension` trigger | invalid | `threat > 0.5` | triggers doc: "`threat` … check the global threat value (world tension). 0-1 value" |
| `destroyer_hull_2` (tech/equipment) | invalid | `ship_hull_light_2` + `categories = { naval_equipment }`, folder `mtgnavalfolder` | vanilla `MTG_naval.txt`, `ship_hull_light.txt` |
| `range / agility / max_speed` (plane stats) | invalid | `air_range / air_agility / maximum_speed` | vanilla `plane_airframes.txt` |
| `on_war_declared_uk` (invented on_action) | invalid | `on_declare_war` (real on-action) + `trigger = { tag = ENG }` | vanilla `common/on_actions/_documentation.md` lists `on_declare_war` |
| `capital = 42` (Germany) | wrong value | `capital = 64` | vanilla `history/countries/GER - Germany.txt` ("# Berlin/Brandeburg") |
| `ideology = trotskyism` | unverifiable | `ideology = communism` | no `trotskyism` ideology in `common/ideologies`; it appears only inside other ids |
| `prerequisite = { focus = USA_new_deal }` | invented id | `USA_continue_the_new_deal` | vanilla `common/national_focus/usa.txt` |
| `keep_civil_war_ideology_if_recovered_by_original_owner = yes` | not documented | omitted | start_civil_war params per effects doc |
| state ids 270/271 ("Bosphorus") | wrong/unverifiable | verified Istanbul (797) + Thrace (184) | vanilla `history/states/797-Istanbul.txt`, `184-Thrace.txt` |
| `add_guarantee` | invalid | `give_guarantee = FROM` | effects doc documents `give_guarantee`; vanilla decisions use it |
| "3 selectable options" example had 2 options | wrong example | 3 options generated | prompt is ground truth; expected answer was internally inconsistent |
| icons (GFX_goal_*, idea_generic_*, etc.) | mostly invented | omitted (minimal output) | icons are optional; only verified icons would be emitted on request |

## Identifier conventions applied

- Focus ids: `TAG_lowercase_name` (e.g. `ITA_mare_nostrum`), never colliding
  with the vanilla index (`ITA_mare_nostrum` exists in vanilla → the engine
  emits `ITA_mare_nostrum_2`).
- Event ids: `tag_lowercase_name.1/.2` (e.g. `fra_low_morale.1`).
- Decisions/ideas/tech/equipment/scripted ids: lowercase tag-prefixed
  (`hun_send_volunteers_spain`, `aus_anschluss_resentment`,
  `uk_destroyer_improvements`, `jap_carrier_fighter_1`,
  `bra_coffee_boom`).
- Character ids: `TAG_slug` (`SOV_fictional_general`).
- Country history files: `history/countries/TAG - Name.txt`, capital read from
  the vanilla history file.

## How comparison is enforced

Every generated proposal must pass the same validator the repair loop uses
(documented effects/triggers/modifiers, no invented identifiers, balanced
blocks, localisation coverage) plus a strict token check. The evaluator never
compares byte-for-byte against expected answers.
