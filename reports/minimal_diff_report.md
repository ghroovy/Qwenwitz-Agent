# Minimal-Diff Report

## Metric

The benchmark measures *actual edits vs minimum edits*:

- files changed by the proposal vs the file budget for the object kind;
- lines changed for modify/repair tasks (the named file only);
- object count in the diff vs the requested count.

## Results

| Case class | Minimum | Actual (final pass) |
|---|---|---|
| Single focus/event/decision | focus+loc (2 files) | 2 files |
| Idea / tech / equipment / division / character / on_action / AI / history | 1 file | 1 file |
| Focus granting an idea | focus+idea+loc (3 files) | 3 files |
| 2-event chain | events+loc (2 files) | 2 files |
| Repair (missing brace) | fix the broken file only | 1 file, brace only |
| Modify (focus cost) | 1 line in 1 file | `cost = 10` → `cost = 15`, one line |

## How minimality is enforced

1. **Budget check** (`strict.check_budget`): the agent's result includes a
   budget report; the harness fails any proposal that exceeds the requested
   object/file budget.
2. **Deterministic generators**: snippet generators emit exactly one object
   (plus required localisation / explicitly required dependencies).
3. **Modify handler**: `SnippetEngine.modify` locates the exact focus block
   declaring the id and replaces only its `cost` line via `re.subn(count=1)`;
   if the value already matches, it returns no change.
4. **No reformatting**: repair/modify edits preserve every other byte
   (verified by workspace hashing in the preservation check).

## Examples of what is deliberately NOT done

- No icons, pictures, or cosmetic fields unless requested.
- No events/ideas/decisions/AI files for focus-tree or single-focus requests.
- No localisation beyond the keys the generated ids require.
- No reordering or renaming of existing identifiers when modifying.
