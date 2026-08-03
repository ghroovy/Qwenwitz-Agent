# Owner: STABLE
"""Unified-diff generation and application. Files are never overwritten
directly: the agent produces a diff, validates it, and only then applies."""

from __future__ import annotations

import difflib
import re
from pathlib import Path

from . import filesystem


class PatchError(Exception):
    pass


def make_diff(path: str, old_text: str, new_text: str) -> str:
    # difflib glues the final line when it has no trailing newline; normalize.
    if old_text and not old_text.endswith("\n"):
        old_text += "\n"
    if new_text and not new_text.endswith("\n"):
        new_text += "\n"
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    diff = difflib.unified_diff(old_lines, new_lines, fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="\n")
    return "".join(diff)


def show_diff(diff: str) -> str:
    if not diff.strip():
        return "(no changes)"
    return diff.rstrip("\n")


def _parse_hunks(diff: str) -> list[dict]:
    """Parse unified diff into hunks with target path and +/-/context lines."""
    hunks: list[dict] = []
    current: dict | None = None
    path = None
    for line in diff.splitlines(keepends=True):
        stripped = line.rstrip("\n")
        m = re.match(r"^\+\+\+ b/(.+)$", stripped)
        if m:
            # git-style headers carry a timestamp after a tab:
            #   +++ b/file.txt\t2026-08-03 12:00:01
            path = m.group(1).split("\t", 1)[0].strip()
            if len(path) >= 2 and path[0] == '"' and path[-1] == '"':
                path = path[1:-1]
            continue
        m = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$", stripped)
        if m:
            current = {"path": path, "old_start": int(m.group(1)), "new_start": int(m.group(3)),
                       "lines": []}
            hunks.append(current)
            continue
        if current is None:
            continue
        if stripped.startswith(("+++", "---", "diff ", "index ", "new file", "deleted file")):
            continue
        if line.startswith(" ") or line.startswith("+") or line.startswith("-"):
            current["lines"].append((line[0], line[1:].rstrip("\r\n")))
    return hunks


def _apply_hunks(file_lines: list[str], hunks: list[dict]) -> list[str]:
    """Apply parsed hunks by locating context lines in the current content."""
    result = list(file_lines)
    offset = 0  # cumulative line shift from hunks already applied to this file
    for hunk in hunks:
        ops = hunk["lines"]
        # Prefer the position the hunk header declares (`@@ -N`), which keeps
        # identical duplicate blocks from being edited in the wrong place.
        expected = ((hunk.get("old_start") or 1) - 1) + offset
        first = next((txt for op, txt in ops if op in (" ", "-")), None)
        if first is None:
            # pure addition (e.g., a new file): append all added lines
            result = result + [txt for op, txt in ops if op == "+"]
            offset += sum(1 for op, _ in ops if op == "+")
            continue
        candidates = [i for i, line in enumerate(result)
                      if line.rstrip("\n") == first]
        candidates.sort(key=lambda i: abs(i - expected))
        applied = False
        for start in candidates:
            out_lines: list[str] = []
            idx = start
            ok = True
            for op, txt in ops:
                if op in (" ", "-"):
                    if idx >= len(result) or result[idx].rstrip("\n") != txt:
                        ok = False
                        break
                    if op == " ":
                        out_lines.append(result[idx])
                    idx += 1
                else:
                    out_lines.append(txt)
            if ok:
                result = result[:start] + out_lines + result[idx:]
                offset += (sum(1 for op, _ in ops if op == "+") -
                           sum(1 for op, _ in ops if op == "-"))
                applied = True
                break
        if not applied:
            raise PatchError(f"hunk context not found (looking for: {(first or '')[:60]!r})")
    return result


def apply_diff(diff: str, workspace_root: Path) -> dict:
    """Apply a unified diff to the workspace (atomic write). Returns summary."""
    hunks = _parse_hunks(diff)
    if not hunks:
        raise PatchError("no hunks found in diff")
    by_path: dict[str, list[dict]] = {}
    for h in hunks:
        if h["path"] is None:
            raise PatchError("hunk without target path")
        by_path.setdefault(h["path"], []).append(h)
    applied = []
    for rel_path, path_hunks in by_path.items():
        abs_path = filesystem.resolve_write(rel_path)
        old_text = filesystem.read_text_keep(abs_path) if abs_path.exists() else ""
        # Preserve the file's dominant line-ending style (CRLF mods must stay
        # CRLF; a patch application must never rewrite the whole file's EOLs).
        eol = "\r\n" if "\r\n" in old_text else "\n"
        old_lines = old_text.splitlines()
        new_lines = _apply_hunks(old_lines, path_hunks)
        new_text = eol.join(new_lines) + (eol if new_lines else "")
        if new_text == old_text:
            continue
        filesystem.write_text(rel_path, new_text)
        added = sum(1 for h in path_hunks for op, _ in h["lines"] if op == "+")
        removed = sum(1 for h in path_hunks for op, _ in h["lines"] if op == "-")
        applied.append({"path": rel_path, "added_lines": added, "removed_lines": removed,
                        "was_new_file": not old_text})
    return {"applied": applied}
