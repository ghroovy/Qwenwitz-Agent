# Owner: ACTIVE
"""Click-to-inspect: turn a preview node back into grounded details."""

from __future__ import annotations

import re
from pathlib import Path

from . import map_preview  # noqa: E402
from . import raw_game_dir  # noqa: E402
from .localisation import localisation_text  # noqa: E402
from ..config import CONFIG  # noqa: E402

_BLOCK_START = re.compile(r"(^|\n)\s*" + r"([A-Za-z0-9_.]+)" + r"\s*=\s*\{")


def _block_by_key(path: Path, key: str) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    for m in re.finditer(r"(^|\n)\s*" + re.escape(key) + r"\s*=\s*\{", text):
        start = m.end() - 1
        depth, i = 1, start + 1
        while i < len(text) and depth > 0:
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
            i += 1
        return text[m.start():i]
    return ""


def _block_containing_id(path: Path, ident: str, keys: tuple[str, ...]) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    for key in keys:
        for m in re.finditer(r"(^|\n)\s*" + re.escape(key) + r"\s*=\s*\{", text):
            start = m.end() - 1
            depth, i = 1, start + 1
            while i < len(text) and depth > 0:
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                i += 1
            block = text[m.start():i]
            if re.search(r"\bid\s*=\s*" + re.escape(ident) + r"\b", block):
                return block
    return ""


def _raw_block(path: Path, ident: str, max_len: int = 6000) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    for m in _BLOCK_START.finditer(text):
        if m.group(2) == ident:
            start = m.end() - 1  # the '{'
            depth, i = 1, start + 1
            while i < len(text) and depth > 0:
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                i += 1
            return text[m.start():i][:max_len]
    return ""


def _abs(path: Path) -> Path:
    """Resolve relative category paths against the workspace / vanilla roots."""
    if path.is_absolute():
        return path
    for root in (CONFIG.workspace_root, raw_game_dir()):
        cand = root / path
        if cand.exists():
            return cand
    return path


def preview_inspect(kind: str, ident: str, tools=None, index=None, validator=None) -> dict:
    """Return grounded details for one clicked node."""
    ident = str(ident)
    if kind == "province":
        try:
            pid = int(ident)
        except ValueError:
            return {"ok": False, "message": f"invalid province id: {ident}"}
        info = map_preview.province_info(pid)
        info["localisation"] = None
        return info

    if kind == "state":
        try:
            sid = int(ident)
        except ValueError:
            return {"ok": False, "message": f"invalid state id: {ident}"}
        info = map_preview.state_info(sid)
        info["localisation"] = None
        return info

    if kind == "strategic_region":
        try:
            rid = int(ident)
        except ValueError:
            return {"ok": False, "message": f"invalid strategic region id: {ident}"}
        info = map_preview.strategic_region_info(rid)
        info["localisation"] = None
        return info

    if kind == "supply_area":
        try:
            aid = int(ident)
        except ValueError:
            return {"ok": False, "message": f"invalid supply area id: {ident}"}
        info = map_preview.supply_area_info(aid)
        info["localisation"] = None
        return info

    if kind == "identifier":
        categories = validator.categories if validator is not None else (index.categories() if index is not None else None)
        if categories is not None:
            hits = []
            for cat, mapping in categories.items():
                if ident in mapping:
                    hits.append({"identifier": ident, "category": cat, "source": mapping[ident]})
            similar = index.fuzzy(ident, limit=5) if index is not None and not hits else []
            return {
                "ok": bool(hits),
                "message": "verified (vanilla or workspace)" if hits else f"not verified: {ident}",
                "results": hits[:10],
                "similar": similar,
                "localisation": localisation_text(ident),
            }
        return {"ok": False, "message": "no index available"}

    out: dict = {"ok": False, "message": f"unknown kind: {kind}"}
    if tools is not None:
        tool_name = {
            "focus": "inspect_focus",
            "event": "inspect_event",
            "decision": "inspect_decision",
            "scripted_effect": "inspect_scripted_effect",
            "scripted_trigger": "inspect_scripted_trigger",
        }.get(kind)
        if tool_name:
            res = tools.call(tool_name, id=ident)
            out = res.to_dict()
    if not out.get("ok") and (validator is not None or index is not None):
        # Standalone fallback: locate the file in the index and extract the block.
        cat_key = {
            "focus": "focuses",
            "event": "events",
            "decision": "decisions",
            "idea": "ideas",
            "scripted_effect": "scripted_effects",
            "scripted_trigger": "scripted_triggers",
        }.get(kind)
        categories = validator.categories if validator is not None else (index.categories() if index is not None else {})
        path = categories.get(cat_key, {}).get(ident) if cat_key else None
        if path:
            if kind == "idea":
                raw = _raw_block(_abs(Path(path)), ident)
            elif kind in ("focus", "event"):
                keys = ("focus",) if kind == "focus" else ("country_event", "state_event", "news_event", "event")
                raw = _block_containing_id(_abs(Path(path)), ident, keys)
            else:
                raw = _block_containing_id(_abs(Path(path)), ident, ("decision",)) or _raw_block(_abs(Path(path)), ident)
            out = {"ok": bool(raw), "tool": f"inspect_{kind}",
                   "message": "found" if raw else "block not found",
                   "data": {"type": kind, "id": ident, "file": path, "text": raw[:6000]}}
    if kind == "idea" and not out.get("ok"):
        if index is not None:
            categories = validator.categories if validator is not None else index.categories()
            path = categories.get("ideas", {}).get(ident)
            if not path:
                out = {"ok": False, "tool": "inspect_idea", "message": f"idea `{ident}` not found in index"}
    if kind == "event":
        loc = {"title": localisation_text(ident + ".t") or localisation_text(ident),
               "desc": localisation_text(ident + ".desc") or localisation_text(ident + "_desc")}
    else:
        loc = {"title": localisation_text(ident), "desc": localisation_text(ident + "_desc")}
    out["localisation"] = loc if any(loc.values()) else None
    return out
