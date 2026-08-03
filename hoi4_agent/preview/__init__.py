"""Mod previews: world map, focus trees, event graphs, decision lists.

This package is a read-only presentation layer. It never writes to the
workspace, never modifies the identifier index, and never trains anything.
Every identifier shown in a preview is grounded in the vanilla data under
``data/raw/game`` (plus the workspace mod when one is open).

The payloads produced here are JSON-serializable so the stdio server can hand
them straight to the VS Code webview.
"""

from __future__ import annotations

from pathlib import Path

from ..config import CONFIG


def raw_game_dir() -> Path:
    """data/raw/game — vanilla files extracted from the install."""
    return CONFIG.index_dir.parent.parent / "raw" / "game"


def resolve_preview_path(path: str | None, sub_dir: str) -> Path | None:
    """Resolve a user-supplied path to a real file, or return None."""
    if path:
        p = Path(path)
        if not p.is_absolute():
            for root in (CONFIG.workspace_root, raw_game_dir()):
                cand = root / p
                if cand.exists():
                    return cand
        if p.exists():
            return p
        return None
    # Default: workspace first, then vanilla.
    ws = CONFIG.workspace_root / sub_dir
    if ws.exists():
        files = sorted(ws.rglob("*"))
        if files:
            return files[0]
    vanilla = raw_game_dir() / sub_dir
    if vanilla.exists():
        files = sorted(vanilla.rglob("*"))
        if files:
            return files[0]
    return None


from . import (  # noqa: E402,F401
    decision_preview,
    event_preview,
    focus_preview,
    inspect_preview,
    localisation,
    map_preview,
)

__all__ = [
    "decision_preview",
    "event_preview",
    "focus_preview",
    "inspect_preview",
    "localisation",
    "map_preview",
    "raw_game_dir",
]
