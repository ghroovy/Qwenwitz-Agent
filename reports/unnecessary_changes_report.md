# Unnecessary-Changes Report

## Idempotence

Running the identical prompt twice must produce no unnecessary edits. The
engine now:

- checks the generated object's id against the workspace before staging
  (`SnippetEngine.generate` returns `None` → the agent answers "already
  exists, nothing changed");
- treats workspace collisions as "already exists" instead of silently
  renumbering (`_new_id` only avoids vanilla-index collisions);
- verifies fragment/single-object files by path existence.

Measured: 27/27 cases pass the apply → re-run → no-op check; workspace hashes
before/after the second run are identical (preservation), and no duplicate ids
or localisation keys are produced.

## Root causes fixed

1. **Silent renumbering broke idempotence.** Run 1 emitted
   `ITA_mare_nostrum_2`; run 2 saw it in the workspace and emitted `_3`.
   Workspace collisions are now idempotence, not renumbering.
2. **Fragment paths couldn't be applied.** `snippets/` was rejected by
   `filesystem.classify`, so the applied state never contained the object and
   re-runs duplicated it. `snippets/` is now a writable, game-ignored dir.
3. **Wrong id extraction.** Character/equipment ids were extracted from the
   wrapper key (`characters`, `equipments`) instead of the object id; per-kind
   extraction now matches the real identifiers (nested block ids for
   character/equipment, `scripted_effect <name>` for scripted blocks).

## Preservation

Every seeded case (repair, modify) changes only the file named in the prompt.
The repair case fixes exactly the missing brace; the modify case changes
exactly one line (`cost = 10` → `cost = 15`) inside the named focus block and
touches nothing else.
