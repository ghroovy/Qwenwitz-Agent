# Stress Testing Report

Run: `benchmarks/adversarial/stress.py` (isolated temp workspaces throughout;
the user's mod is never touched).

## Results

| Test | Size | Result |
|---|---:|---|
| Random prompts (generation + validation) | 10,000 | 0 invalid; 361 prompts/s |
| Repeated identical prompt | 1,000 | 0 differing outputs |
| Interruption / recovery (apply → "crash" → new agent) | 500 | 500/500 recovered the applied batch |
| Crash / restart with corrupted state files | 500 | 500/500 survived and produced valid work |
| Concurrent interleaved requests (one workspace) | 500 | 0 duplicate ids across all applied files |
| Large workspace (10k focus files + 50k-line event file) | — | scan 1.96 s cold; run 1.06 s; see below |
| Memory (3,000 generations, psutil RSS) | 3,000 | +0.1 MB (no leak) |

## Throughput

- Snippet generation: ~361 prompts/s (validated).
- Median full `agent.run` latency across the 1,000-prompt corpus: ~5 ms
  (p95 ≈ 0.32 s; project-level requests dominate the tail).
- 1,000 repeated identical prompts in 2.2 s.

## Interruption / crash resilience

- Batches are persisted to disk on every `_prepare_pending`; a new process
  reloads them (`_load_backlog`) and the review/approval flow continues.
- File writes are atomic (`write_text` stages to a temp file in the memory dir
  then `os.replace`), so an interrupted patch application cannot leave a
  half-written mod file.
- Corrupted `pending_backlog.json` / `project_scan_cache.json` are ignored on
  load (500/500 restart tests passed).

## Concurrency

The server is single-threaded by design, so "concurrent" here means
interleaved request/apply cycles in one workspace: 500 mixed focus/decision
creates applied sequentially produced **zero duplicate ids**.

## Known scale findings (documented, not hidden)

- **Cold first scan of a 10,000-file workspace ≈ 1.96 s** and the agent
  startup (including that scan) measured **≈ 56 s cold** on this machine
  (Windows, cold disk cache). Subsequent scans are stat-bound and the disk
  cache showed **~0% speedup** (1.96 s → 1.96 s) because every file is still
  stat'ed and the cache metadata is rewritten wholesale. Mitigations
  (directory-mtime short-circuiting, per-directory cache granularity) are
  documented in `performance_scaling.md` — not implemented, because no
  correctness failure was found.
- Memory stayed flat over 3,000 generations (+0.1 MB), but long-running
  extension sessions were not exercised end-to-end (no headless VS Code
  harness); see `final_ship_readiness.md`.
