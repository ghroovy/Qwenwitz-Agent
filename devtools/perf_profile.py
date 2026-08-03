"""Profile the hot paths of the HOI4 agent (no model inference).

Measures: agent startup, project indexing, prompt construction, tool
dispatch, and validation. Writes docs/PERFORMANCE.md.

Run:  .venv\\Scripts\\python.exe devtools\\perf_profile.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def timed(fn, *args, **kwargs) -> tuple[float, object]:
    t0 = time.perf_counter()
    result = fn(*args, **kwargs)
    return time.perf_counter() - t0, result


def fmt(sec: float) -> str:
    if sec < 0.001:
        return f"{sec * 1e6:.0f} µs"
    if sec < 1:
        return f"{sec * 1e3:.1f} ms"
    return f"{sec:.2f} s"


def main() -> None:
    rows: list[tuple[str, str, str]] = []

    # 1) agent startup (cold, includes index + validator docs)
    from hoi4_agent.agent import Agent

    t, _ = timed(Agent, auto_approve=True, use_model=False)
    rows.append(("agent startup (cold)", fmt(t), "index load + validator docs"))

    # 2) project indexing (workspace scan)
    from hoi4_agent.project_scan import ProjectScan

    t, scan = timed(ProjectScan().build)
    rows.append(("project indexing", fmt(t),
                 f"{len(scan.get('files', {}))} files in workspace graph"))

    # 3) prompt construction (repair model prompt)
    from hoi4_agent.agent import Agent as A2

    agent = A2(auto_approve=True, use_model=False)
    t, _ = timed(agent.repair._model_repair, {"a.txt": "x"}, [{"type": "brace_mismatch",
                                                               "message": "unbalanced"}])
    rows.append(("repair prompt construction", fmt(t), "model prompt string build"))

    # 4) tool dispatch
    t, res = timed(agent.tools.call, "search_identifier", name="GER_oppose_hitler")
    rows.append(("tool: search_identifier", fmt(t), f"ok={res.ok}"))
    t, res = timed(agent.tools.call, "find_vanilla_examples", query="add_political_power")
    rows.append(("tool: find_vanilla_examples", fmt(t), f"ok={res.ok}"))

    # 5) validation
    proposals = {
        "common/national_focus/can_agent_focus.txt": (
            "focus_tree = {\n\tid = can_tree\n"
            "\tfocus = {\n\t\tid = CAN_test_focus_01\n"
            "\t\tcompletion_reward = {\n\t\t\tadd_political_power = 50\n\t\t}\n\t}\n}\n"
        ),
        "localisation/english/can_l_english.yml": (
            "l_english:\n CAN_test_focus_01:0 \"Test\"\n"
            " CAN_test_focus_01_desc:0 \"Desc\"\n"
        ),
    }
    t, v = timed(agent.validator.validate_proposal, proposals)
    rows.append(("validate_proposal (2 files)", fmt(t), f"valid={v['valid']}"))
    t, v = timed(agent.validator.validate_localisation)
    rows.append(("validate_localisation (workspace)", fmt(t), f"errors={len(v.get('errors', []))}"))

    out = [
        "# Performance Profile",
        "",
        f"Measured: {time.strftime('%Y-%m-%d %H:%M')} — local machine, "
        "no model inference, single call per row.",
        "",
        "| Benchmark | Time | Detail |",
        "|---|---|---|",
    ]
    for name, dur, detail in rows:
        out.append(f"| {name} | {dur} | {detail} |")
    out += [
        "",
        "## Caching recommendations",
        "",
        "- **Agent startup** is dominated by `IdentifierIndex._load()` and the "
        "validator docs JSON. If it becomes a bottleneck, precompile the index "
        "into a single binary/`npz` blob and mmap it (index is STABLE).",
        "- **Project indexing** already caches to "
        "`data/agent_state/project_scan_cache.json` keyed by file fingerprints; "
        "incremental mode avoids rescanning unchanged files.",
        "- **Localisation validation** scans every workspace `.yml` per call; "
        "cache the workspace loc scan with the same mtime/size fingerprint used "
        "by `preview/localisation.py`.",
        "- **Tool dispatch** is a thin dict lookup; the cost is inside tools "
        "(index fuzzy match, file scans). Add limits + result caps before "
        "optimizing dispatch itself.",
        "- **Map preview** caches the parsed map in-process; the first call pays "
        "for BMP decode + LUT build (~1-2 s). Mode meta (state names) is cached "
        "after the first build.",
    ]
    (ROOT / "docs").mkdir(parents=True, exist_ok=True)
    (ROOT / "docs" / "PERFORMANCE.md").write_text("\n".join(out), encoding="utf-8")
    print("\n".join(out))


if __name__ == "__main__":
    sys.exit(main())
