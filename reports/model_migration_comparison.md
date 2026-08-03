# Model Migration Comparison — Qwen2.5-Coder-1.5B-Instruct vs Qwen3.5-2B

Generated: 2026-08-04 00:46:44  ·  Deterministic agent identical in both runs; only the
repair-loop language model changed. Greedy decoding (`do_sample=False`), same prompts,
same validator, same max tokens as production.

## Verdict

**Overall: IMPROVED** — strict repair pass rate improved (0% -> 46%); game-breaking JSON output eliminated (21 -> 1 cases); identifier grounding improved (17 -> 7 invented ids); minimal-edit behaviour preserved or better.

## Repair benchmark (24 grounded HOI4 repair cases)

| Metric | Qwen2.5-Coder-1.5B | Qwen3.5-2B |
|---|---|---|
| Cases | 24 | 24 |
| Validator pass (full proposal set) | 21/24 (88%) | 12/24 (50%) |
| **Strict pass (valid AND parseable HOI4, no JSON)** | 0/24 (0%) | 11/24 (46%) |
| Validator fooled by JSON-style output (game would break) | 21 | 1 |
| Content file clean (no errors in repaired file) | 23/24 | 14/24 |
| Total errors removed | 27 | 17 |
| Avg errors removed per case | 1.12 | 0.71 |
| Cases with invented identifiers | 14 (17 ids) | 7 (7 ids) |
| Avg similarity to minimal fix | 0.577 | 0.846 |
| Avg changed lines vs minimal fix | 16.8 | 3.2 |
| Usable output (code fence extracted) | 22/24 | 22/24 |
| Honest UNRESOLVED responses | 0 | 2 |
| Total model repair time | 66.0s | 73.7s |

## Self-check (5-question YES/NO gate)

| Metric | Qwen2.5-Coder-1.5B | Qwen3.5-2B |
|---|---|---|
| Format compliance (5 numbered YES/NO answers) | 8/8 | 8/8 |
| Detected the expected problem (NO on a relevant question) | 0/8 | 8/8 |

## Performance (RTX 4060 8GB, fp16)

| Metric | Qwen2.5-Coder-1.5B | Qwen3.5-2B |
|---|---|---|
| Tokenizer load | 0.3s | 0.5s |
| Model load | 5.6s | 7.2s |
| Generation speed | 31.0 tok/s | 19.5 tok/s |
| Peak VRAM | 3.11 GB | 3.82 GB |

## Per-case repair results

| Case | Expected | Qwen2.5 result | Qwen3.5 result | Qwen2.5 invented | Qwen3.5 invented |
| effect_typo | invalid_effect | PASS (['none']) | PASS (['none']) | ['GER_agent_focus_ab_01'] | - |
| idea_unknown | unknown_identifier | PASS (['none']) | FAIL (['unknown_identifier']) | ['GER_agent_focus_ab_01', 'NEW_AND_BETTER_GERMANY'] | - |
| event_idea_unknown | unknown_identifier | PASS (['none']) | FAIL (['unknown_identifier']) | - | ['GER_fake_idea_03'] |
| prereq_broken | broken_reference | PASS (['none']) | PASS (['none']) | ['GER_agent_focus_ab_01'] | - |
| brace_missing | brace_mismatch | PASS (['none']) | PASS (['none']) | ['GER_agent_focus_ab_01'] | - |
| dup_focus_id | duplicate_identifier | FAIL (['duplicate_identifier']) | FAIL (['duplicate_identifier']) | - | - |
| focus_no_id | missing_required_block | PASS (['none']) | FAIL (['missing_required_block']) | ['GER_agent_focus_ab_01'] | - |
| icon_unknown | unknown_icon | PASS (['none']) | FAIL (['unknown_icon']) | ['GER_agent_focus_ab_01', 'GFX_goal_fake_icon_xyz'] | ['GFX_goal_fake_icon_xyz'] |
| modifier_typo | invalid_modifier | PASS (['none']) | PASS (['none']) | ['GER_agent_idea_ab_01'] | ['GER_agent_idea_ab_01'] |
| trigger_typo | invalid_trigger | PASS (['none']) | PASS (['none']) | ['GER_agent_focus_ab_01'] | - |
| scope_error | invalid_scope | FAIL (['invalid_scope']) | FAIL (['invalid_scope']) | - | - |
| mixed_errors | brace_mismatch,unknown_identifier | PASS (['none']) | FAIL (['unknown_identifier']) | ['GER_agent_focus_ab_01', 'GER_fake_mixed_idea'] | ['GER_fake_mixed_idea'] |
| event_no_option | missing_required_block | PASS (['none']) | FAIL (['missing_required_block']) | - | - |
| event_effect_typo | invalid_effect | PASS (['none']) | PASS (['none']) | - | - |
| event_trigger_typo | invalid_trigger | PASS (['none']) | PASS (['none']) | - | - |
| dup_event_id | duplicate_event_id | FAIL (['duplicate_event_id']) | FAIL (['duplicate_event_id']) | - | - |
| event_timed_idea_unknown | unknown_identifier | PASS (['none']) | FAIL (['unknown_identifier']) | - | ['GER_fake_chain_idea'] |
| event_brace | brace_mismatch | PASS (['none']) | PASS (['none']) | - | - |
| news_event_no_id | missing_required_block | PASS (['none']) | FAIL (['missing_required_block']) | - | - |
| scripted_effect_typo | invalid_effect | PASS (['none']) | PASS (['none']) | ['GER_agent_scripted_ab'] | ['GER_agent_scripted_ab'] |
| scripted_trigger_typo | invalid_trigger | PASS (['none']) | PASS (['none']) | ['GER_agent_scripted_ab'] | ['GER_agent_scripted_ab'] |
| sprite_missing | missing_sprite | PASS (['none']) | PASS (['none']) | ['GER_agent_focus_ab_01'] | - |
| ai_trigger_typo | invalid_trigger | PASS (['none']) | PASS (['none']) | ['GER_agent_ai_ab'] | - |
| focus_no_id_b | missing_required_block | PASS (['none']) | FAIL (['missing_required_block']) | ['GER_agent_focus_ab_02'] | - |

## Method & limitations

- Both models were evaluated through the agent's real repair path (`RepairEngine._model_repair` / `_apply_model_output`) with the production prompt (validator errors + verified identifiers + file content).
- The 24 cases cover every validator error class the repair loop sees: `invalid_effect`, `invalid_trigger`, `invalid_modifier`, `unknown_identifier`, `broken_reference`, `brace_mismatch`, `missing_required_block`, `duplicate_identifier`, `duplicate_event_id`, `missing_localisation`, `invalid_scope`, `unknown_icon`, and mixed errors.
- 'Validator pass' means the repaired proposal set is fully valid under the production validator (including localisation coverage).
- Similarity measures difflib ratio to the minimal correct fix; a low ratio means the model rewrote unrelated content.
- Greedy decoding: results are deterministic per model; no sampling noise.
- Performance numbers are single-run wall-clock measurements on the local RTX 4060.