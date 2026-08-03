# Performance & Scaling Report

## Measured numbers (this machine, isolated workspaces)

| Scenario | Time |
|---|---:|
| Snippet generation throughput | ~361 prompts/s (validated) |
| Full `agent.run`, median (1,000-prompt corpus) | ~5 ms |
| Full `agent.run`, p95 | ~0.32 s |
| Project-level run ("make a focus tree for canada with 10 focuses") | ~0.5–1.1 s |
| `ProjectScan.build()` on 10,000 focus files + 50k-line event file (cold) | 1.96 s |
| Same scan with disk cache | 1.96 s (**no speedup**) |
| Agent startup including first workspace scan (cold, 10k files) | ≈ 56 s |
| Memory growth over 3,000 snippet generations | +0.1 MB |

## Where the time goes

1. **Workspace scan** (`ProjectScan.build`): O(files) stat + per-file regex
   parse on change. 10k files ≈ 2 s once warm.
2. **Startup**: the scan runs synchronously in `Agent.__init__`; a cold
   10k-file mod adds ~54 s before the first prompt can be answered.
3. **Cache effectiveness**: the disk cache avoids re-parsing unchanged files,
   but every file is still stat'ed and the whole cache metadata JSON is
   rewritten each build — the warm path is stat-bound, so the cache showed
   ~0% wall-clock improvement at 10k files.

## Recommendations (not implemented — no correctness failure found)

- **Short-circuit unchanged directories** by mtime before descending (would
  cut the warm scan to near-zero for untouched trees).
- **Per-directory cache granularity** so a single edited file doesn't rewrite
  metadata for all 10k files.
- **Lazy/deferred workspace scan** on agent startup (scan on first
  workspace-touching tool call), so "chat" and snippet requests don't pay the
  cold-scan cost.
- Keep memory-dir writes out of the mod tree (already true: temp files are
  staged in the memory dir and atomically replaced).

## Verified non-issues

- No memory leak over 3,000 generations (+0.1 MB).
- File writes are atomic (temp + `os.replace`); interrupted applies cannot
  corrupt files.
- The 50k-line event file parses and validates without pathological
  slowdowns (part of the 1.96 s scan).
