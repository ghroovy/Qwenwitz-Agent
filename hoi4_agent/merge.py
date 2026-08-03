# Owner: ACTIVE
"""Merge engine: combine multiple mod files with conflict detection.

Detects duplicate ids (focus/event/decision/idea blocks), conflicting
localisation, and duplicate definitions across files. Deterministic conflicts
(identical duplicates) are resolved by keeping the first definition; real
conflicts are reported for the user.
"""

from __future__ import annotations

import re
from pathlib import Path

from .filesystem import workspace

BLOCK_OPEN = re.compile(r"^[ \t]*([A-Za-z0-9_.]+)\s*=\s*\{", re.M)
ID_SLOT = re.compile(r"\bid\s*=\s*([A-Za-z0-9_.]+)")
LOC_KEY = re.compile(r"^(\s*)([A-Za-z0-9_.]+)(\s*:\d*\s*\"[^\"]*\")", re.M)


def _extract_blocks(text: str) -> list[tuple[str, str]]:
    """Return (block_id, raw_text) for top-level named blocks and id-slotted blocks."""
    out: list[tuple[str, str]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = re.match(r"^[ \t]*([A-Za-z0-9_.]+)\s*=\s*\{", lines[i])
        if m:
            start = i
            depth = 0
            j = i
            while j < len(lines):
                depth += lines[j].count("{") - lines[j].count("}")
                if depth <= 0 and j > i:
                    break
                j += 1
            raw = "\n".join(lines[start:j + 1])
            inner = raw[: min(len(raw), 400)]
            idm = ID_SLOT.search(inner)
            block_id = idm.group(1) if idm else m.group(1)
            out.append((block_id, raw))
            i = j + 1
            continue
        i += 1
    return out


class MergeEngine:
    def __init__(self, index):
        self.index = index

    def merge(self, files: list[str], proposals: dict[str, str] | None = None) -> dict:
        """Merge the given workspace files. Returns proposals + merge report."""
        merged: dict[str, str] = {}
        report: dict = {"files": [], "blocks_total": 0, "duplicates": [],
                        "loc_conflicts": [], "remaining_conflicts": []}
        seen_blocks: dict[str, str] = {}
        seen_loc: dict[str, str] = {}

        for rel in files:
            path = Path(rel)
            if proposals and rel in proposals:
                text = proposals[rel]
            else:
                full = workspace() / rel
                if not full.exists():
                    continue
                text = full.read_text(encoding="utf-8",
                                      errors="surrogateescape")
            report["files"].append(rel)
            if path.suffix == ".yml" or "localisation" in rel:
                merged.setdefault(rel, "")
                for m in LOC_KEY.finditer(text):
                    key = m.group(2)
                    value = m.group(3)
                    if key in seen_loc:
                        if seen_loc[key] != value:
                            report["loc_conflicts"].append(
                                {"key": key, "first": seen_loc[key], "second": value,
                                 "resolution": "kept_first"})
                    else:
                        seen_loc[key] = value
                        merged[rel] += m.group(0) + "\n"
                continue
            blocks = _extract_blocks(text)
            kept_lines: list[str] = []
            consumed = 0
            for block_id, raw in blocks:
                report["blocks_total"] += 1
                if block_id in seen_blocks:
                    identical = seen_blocks[block_id] == raw
                    report["duplicates"].append({
                        "id": block_id, "file": rel,
                        "identical": identical,
                        "resolution": "kept_first" if identical else "needs_review",
                    })
                    if not identical:
                        report["remaining_conflicts"].append(
                            {"id": block_id, "file": rel, "reason": "conflicting definitions"})
                    continue
                seen_blocks[block_id] = raw
                kept_lines.append(raw)
            merged.setdefault(rel, "\n\n".join(kept_lines) + ("\n" if kept_lines else ""))
        return {"proposals": merged, "report": report}

