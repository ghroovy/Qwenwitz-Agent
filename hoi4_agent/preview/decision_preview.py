# Owner: ACTIVE
"""Decision preview: parse vanilla/workspace common/decisions files."""

from __future__ import annotations

from pathlib import Path

from hoi4_agent._runtime.hoi4parser import first_kv, parse_tree, walk  # noqa: E402

from ..config import CONFIG  # noqa: E402
from . import raw_game_dir  # noqa: E402
from .localisation import title_and_desc  # noqa: E402


def _resolve(path: str | None) -> Path | None:
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
    for root in (CONFIG.workspace_root, raw_game_dir()):
        base = root / "common" / "decisions"
        if base.exists():
            for f in sorted(base.glob("*.txt")):
                # Skip debug/stub files that contain no decision blocks.
                text = f.read_text(encoding="utf-8", errors="replace")
                if _contains_decisions(text):
                    return f
    return None


def _contains_decisions(text: str) -> bool:
    nodes = parse_tree(text)
    for cat in nodes:
        if cat.get("kind") != "block" or cat.get("key") in ("decisions", "decision_categories"):
            continue
        if any(c.get("kind") == "block" and c.get("key") != "decision" for c in cat.get("children", [])):
            return True
    for decs in (n for n in nodes if n.get("kind") == "block" and n.get("key") == "decisions"):
        for cat in decs.get("children", []):
            if any(c.get("kind") == "block" and c.get("key") == "decision" for c in cat.get("children", [])):
                return True
    return False


def preview_decisions(path: str | None = None, max_decisions: int = 300) -> dict:
    file = _resolve(path)
    if file is None:
        return {"kind": "decisions", "error": "no decisions file found"}
    text = file.read_text(encoding="utf-8", errors="replace")
    nodes = parse_tree(text)
    entries: list[tuple[str, dict]] = []  # (category, decision node)
    categories: list[str] = []
    # Modern format: top-level blocks are categories, their block children are decisions.
    for cat in nodes:
        if cat.get("kind") != "block":
            continue
        if cat.get("key") in ("decisions", "decision_categories"):
            continue
        categories.append(cat["key"])
        for dec in cat.get("children", []):
            if dec.get("kind") == "block" and dec.get("key") != "decision":
                entries.append((cat["key"], dec))
    # Legacy format: decisions = { category = { decision = { id = X } } }.
    for decs in (n for n in nodes if n.get("kind") == "block" and n.get("key") == "decisions"):
        for cat in decs.get("children", []):
            if cat.get("kind") != "block":
                continue
            categories.append(cat["key"])
            for dec in cat.get("children", []):
                if dec.get("kind") == "block" and dec.get("key") == "decision":
                    entries.append((cat["key"], dec))
    decisions: list[dict] = []
    for category, dec in entries:
        children = dec.get("children", [])
        did = dec.get("key") or first_kv(children, "id")
        if not did:
            continue
        loc = title_and_desc(did)
        decisions.append({
            "id": did,
            "category": category,
            "title": loc["title"],
            "desc": (loc["desc"] or "")[:400],
            "icon": first_kv(children, "icon"),
            "cost": first_kv(children, "cost"),
            "days_remove": first_kv(children, "days_remove"),
            "visible": any(c.get("kind") == "kv" and c.get("key") == "visible" for c in children),
            "available": any(c.get("kind") == "kv" and c.get("key") == "available" for c in children),
            "refs": [c["value"] for c in walk(children, "fire_event") if c.get("kind") == "kv"],
        })
        if len(decisions) >= max_decisions:
            break
    return {
        "kind": "decisions",
        "file": str(file),
        "source_root": "workspace" if file.is_relative_to(CONFIG.workspace_root) else "vanilla",
        "count": len(decisions),
        "categories": list(dict.fromkeys(categories)),
        "decisions": decisions,
    }
