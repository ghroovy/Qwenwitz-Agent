# Owner: STABLE
"""Safe filesystem access: workspace (writable) + vanilla (read-only)."""

from __future__ import annotations

import os
import re
from pathlib import Path

from .config import CONFIG

SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules", "gfx", "sound", "music",
             "dlc", "integrated_dlc", "portraits", "map", "cef", "pdx_launcher",
             "pdx_online_assets", "tweakergui_assets", "previewer_assets", "browser",
             "crash_reporter", "assets"}
TEXT_EXTS = {".txt", ".gui", ".gfx", ".yml", ".yaml", ".lua", ".md", ".csv", ".json", ".toml", ".py"}


class FilesystemError(Exception):
    pass


def _norm(p: Path) -> Path:
    return p.resolve()


def workspace() -> Path:
    return _norm(CONFIG.workspace_root)


def vanilla() -> Path:
    return _norm(CONFIG.vanilla_root)


def read_text_keep(path: Path, errors: str = "surrogateescape") -> str:
    """Read text without newline translation so CRLF files stay CRLF through
    diffs and patches (Path.read_text translates \\r\\n to \\n by default).
    Undecodable bytes are kept as surrogates so a write-back restores the
    original bytes exactly (non-UTF-8 comments must not be corrupted)."""
    with path.open(encoding="utf-8-sig", newline="", errors=errors) as fh:
        return fh.read()


def classify(path: str) -> tuple[str, Path]:
    """Return (root_name, absolute path) for a user-facing path."""
    p = Path(path)
    if p.is_absolute():
        if _norm(p).is_relative_to(workspace()):
            return "workspace", _norm(p)
        if _norm(p).is_relative_to(vanilla()):
            return "vanilla", _norm(p)
        raise FilesystemError(f"path outside allowed roots: {path}")
    for name, root in (("workspace", workspace()), ("vanilla", vanilla())):
        cand = _norm(root / p)
        if cand.is_relative_to(root) and (cand.exists() or p.parts[0] in ("common", "events", "history", "localisation", "interface", "script", "snippets")):
            return name, cand
    raise FilesystemError(f"cannot resolve path: {path}")


def resolve_write(path: str) -> Path:
    name, abs_path = classify(path)
    if name == "vanilla":
        raise FilesystemError("vanilla files are read-only")
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    return abs_path


def list_directory(path: str, max_entries: int = 200) -> dict:
    name, abs_path = classify(path)
    if not abs_path.exists() or not abs_path.is_dir():
        raise FilesystemError(f"directory not found: {path}")
    entries = []
    try:
        items = sorted(abs_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError as exc:
        raise FilesystemError(f"permission denied: {path}") from exc
    for item in items:
        if item.name in SKIP_DIRS or item.name.startswith("."):
            continue
        entries.append({
            "name": item.name,
            "type": "dir" if item.is_dir() else "file",
            "size": item.stat().st_size if item.is_file() else None,
        })
        if len(entries) >= max_entries:
            break
    return {"root": path, "entries": entries, "truncated": len(entries) >= max_entries}


def read_file(path: str, start_line: int = 1, end_line: int | None = None) -> dict:
    name, abs_path = classify(path)
    if not abs_path.exists() or not abs_path.is_file():
        raise FilesystemError(f"file not found: {path}")
    if abs_path.suffix.lower() not in TEXT_EXTS:
        raise FilesystemError(f"not a text file: {path}")
    data = abs_path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise FilesystemError(f"cannot decode text: {path}")
    lines = text.splitlines()
    end = min(end_line or len(lines), len(lines))
    start = max(1, start_line)
    if start > len(lines):
        raise FilesystemError(f"start_line {start} beyond file length {len(lines)}")
    return {
        "root": name,
        "path": str(abs_path),
        "total_lines": len(lines),
        "start_line": start,
        "end_line": end,
        "content": "\n".join(lines[start - 1 : end]),
    }


def write_text(path: str, content: str) -> None:
    abs_path = resolve_write(path)
    # HOI4 requires localisation .yml files to carry a UTF-8 BOM.
    encoding = "utf-8-sig" if abs_path.suffix.lower() == ".yml" else "utf-8"
    # Never write the temporary file inside the mod tree: the game's VFS
    # picks up *.tmp files (e.g. events/*.txt.tmp) and logs errors for them.
    # Stage in the agent memory dir instead, then atomically replace.
    mem_dir = CONFIG.memory_dir
    tmp = mem_dir / f"_write_{abs_path.name}.tmp"
    mem_dir.mkdir(parents=True, exist_ok=True)
    # newline="" prevents Windows write translation: line endings must be
    # preserved exactly as the caller produced them (no \r\r\n doubling).
    with tmp.open("w", encoding=encoding, newline="", errors="surrogateescape") as fh:
        fh.write(content)
    os.replace(tmp, abs_path)


def walk_text_files(root: Path) -> list[Path]:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if Path(fn).suffix.lower() in TEXT_EXTS:
                out.append(Path(dirpath) / fn)
    return sorted(out)


def find_in_files(files: list[Path], query: str, max_results: int, regex: bool = False) -> list[dict]:
    try:
        pattern = re.compile(query, re.IGNORECASE) if regex else re.compile(re.escape(query), re.IGNORECASE)
    except re.error as exc:
        raise FilesystemError(f"invalid pattern: {exc}") from exc
    results = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                results.append({
                    "file": str(f),
                    "line": i,
                    "content": line.strip()[:200],
                })
                if len(results) >= max_results:
                    return results
    return results
