# Owner: ACTIVE
"""Localisation key -> text index for preview display.

Vanilla english localisation is scanned once and cached per process. The
workspace localisation is re-scanned on every call so freshly generated mod
keys show up without a server restart.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import raw_game_dir

_KEY_LINE = re.compile(r'^\s*([A-Za-z0-9_.]+)\s*:\s*\d*\s*"((?:[^"\\]|\\.)*)"\s*$')

_vanilla_cache: dict[str, str] | None = None
_ws_cache: dict = {"key": None, "data": {}}


def _scan_dir(base: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not base.exists():
        return out
    for f in sorted(base.glob("*.yml")):
        try:
            text = f.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            m = _KEY_LINE.match(line)
            if m:
                out[m.group(1)] = m.group(2).replace('\\"', '"').replace("\\n", "\n")
    return out


def _vanilla() -> dict[str, str]:
    global _vanilla_cache
    if _vanilla_cache is None:
        _vanilla_cache = _scan_dir(raw_game_dir() / "localisation" / "english")
    return _vanilla_cache


def _workspace() -> dict[str, str]:
    """Workspace localisation, cached with mtime/size fingerprint so previews
    that look up many keys (events, focuses) don't re-scan per key."""
    from ..config import CONFIG

    base = CONFIG.workspace_root / "localisation" / "english"
    if not base.exists():
        return {}
    files = sorted(base.glob("*.yml"))
    fingerprint = (len(files), max((f.stat().st_mtime_ns for f in files), default=0),
                   sum(f.stat().st_size for f in files))
    if _ws_cache["key"] != fingerprint:
        _ws_cache["key"] = fingerprint
        _ws_cache["data"] = _scan_dir(base)
    return _ws_cache["data"]


def localisation_text(key: str) -> str | None:
    """Best-effort display text for a localisation key (workspace first)."""
    ws = _workspace()
    if key in ws:
        return ws[key]
    return _vanilla().get(key)


def title_and_desc(ident: str) -> dict[str, str | None]:
    """Common loc pair for focuses/events/decisions: `id` and `id_desc`."""
    return {
        "title": localisation_text(ident),
        "desc": localisation_text(ident + "_desc"),
    }


def localisation_size() -> int:
    return len(_vanilla())
