# Owner: ACTIVE
"""Refactor engine: detect duplicated code and repeated effects/triggers.

Never changes behaviour: only identical duplicates are removed, and reusable
scripted-effect/trigger extractions are reported as suggestions.
"""

from __future__ import annotations

import re
from collections import Counter

from .filesystem import workspace
from .project_scan import SCAN_DIRS


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"#[^\n]*", "", text)).strip().lower()


class RefactorEngine:
    def __init__(self, index):
        self.index = index

    def analyze(self, files: list[str] | None = None) -> dict:
        targets = files or [f"{d}/" for d in SCAN_DIRS if d != "localisation/english"]
        content_by_file: dict[str, str] = {}
        for prefix in targets:
            base = workspace() / prefix
            if not base.exists():
                continue
            for f in base.rglob("*"):
                if f.is_file() and f.suffix in (".txt", ".gui", ".gfx", ".yml"):
                    content_by_file[f.relative_to(workspace()).as_posix()] = \
                        f.read_text(encoding="utf-8", errors="replace")
        blocks: dict[str, list[dict]] = {}
        for rel, text in content_by_file.items():
            for m in re.finditer(r"^[ \t]*([A-Za-z0-9_.]+)\s*=\s*\{", text, re.M):
                blocks.setdefault(_normalize(m.group(0)), []).append(
                    {"file": rel, "line": text.count("\n", 0, m.start()) + 1})
        duplicate_blocks = [
            {"signature": sig, "occurrences": occ, "count": len(occ)}
            for sig, occ in blocks.items() if len(occ) > 1
        ]
        # repeated effect sequences inside completion_reward / options
        effect_seqs: Counter = Counter()
        for text in content_by_file.values():
            for m in re.finditer(r"(completion_reward\s*=\s*\{[^}]*\}|options\s*=\s*\{[^}]*\})", text):
                body = _normalize(m.group(1))
                if body:
                    effect_seqs[body] += 1
        repeated_effects = [
            {"signature": sig[:160], "count": cnt}
            for sig, cnt in effect_seqs.items() if cnt >= 3
        ]
        return {
            "duplicate_blocks": duplicate_blocks,
            "repeated_effect_sequences": repeated_effects,
            "suggestions": [
                {
                    "suggestion": "extract scripted effect",
                    "name": f"refactor_agent_effect_{i + 1:02d}",
                    "body": sig,
                    "occurrences": cnt,
                }
                for i, (sig, cnt) in enumerate(
                    sorted(effect_seqs.items(), key=lambda kv: -kv[1])[:5])
                if cnt >= 3
            ],
        }

    def dedupe(self, proposals: dict[str, str]) -> tuple[dict[str, str], int]:
        """Remove later occurrences of identical blocks. Returns proposals + removed count."""
        removed = 0
        seen: set[str] = set()
        for rel, text in proposals.items():
            lines = text.splitlines()
            kept: list[str] = []
            i = 0
            while i < len(lines):
                m = re.match(r"^[ \t]*([A-Za-z0-9_.]+)\s*=\s*\{", lines[i])
                if m:
                    depth = 0
                    j = i
                    while j < len(lines):
                        depth += lines[j].count("{") - lines[j].count("}")
                        if depth <= 0 and j > i:
                            break
                        j += 1
                    raw = "\n".join(lines[i:j + 1])
                    sig = _normalize(raw)
                    if sig in seen:
                        removed += 1
                        i = j + 1
                        continue
                    seen.add(sig)
                    kept.extend(lines[i:j + 1])
                    i = j + 1
                    continue
                kept.append(lines[i])
                i += 1
            proposals[rel] = "\n".join(kept) + "\n"
        return proposals, removed

