# Performance Profile

Measured: 2026-08-02 11:45 — local machine, no model inference, single call per row.

| Benchmark | Time | Detail |
|---|---|---|
| agent startup (cold) | 613.9 ms | index load + validator docs |
| project indexing | 214.4 ms | 934 files in workspace graph |
| repair prompt construction | 3 µs | model prompt string build |
| tool: search_identifier | 50 µs | ok=True |
| tool: find_vanilla_examples | 81 µs | ok=True |
| validate_proposal (2 files) | 309.2 ms | valid=True |
| validate_localisation (workspace) | 38.4 ms | errors=4 |

## Caching recommendations

- **Agent startup** is dominated by `IdentifierIndex._load()` and the validator docs JSON. If it becomes a bottleneck, precompile the index into a single binary/`npz` blob and mmap it (index is STABLE).
- **Project indexing** already caches to `data/agent_state/project_scan_cache.json` keyed by file fingerprints; incremental mode avoids rescanning unchanged files.
- **Localisation validation** scans every workspace `.yml` per call; cache the workspace loc scan with the same mtime/size fingerprint used by `preview/localisation.py`.
- **Tool dispatch** is a thin dict lookup; the cost is inside tools (index fuzzy match, file scans). Add limits + result caps before optimizing dispatch itself.
- **Map preview** caches the parsed map in-process; the first call pays for BMP decode + LUT build (~1-2 s). Mode meta (state names) is cached after the first build.