# Owner: ACTIVE
"""Event preview: parse vanilla/workspace event files into a graph."""

from __future__ import annotations

from pathlib import Path

from hoi4_agent._runtime.hoi4parser import first_kv, parse_tree, walk  # noqa: E402

from ..config import CONFIG  # noqa: E402
from . import raw_game_dir  # noqa: E402
from .localisation import localisation_text  # noqa: E402


def _resolve(path: str | None, max_events: int) -> tuple[list[Path], str]:
    if path:
        p = Path(path)
        if not p.is_absolute():
            for root in (CONFIG.workspace_root, raw_game_dir()):
                cand = root / p
                if cand.exists():
                    return [cand], "file"
        if p.exists():
            return [p], "file"
        return [], "missing"
    files: list[Path] = []
    for root in (CONFIG.workspace_root, raw_game_dir()):
        base = root / "events"
        if base.exists():
            files.extend(sorted(base.glob("*.txt")))
        if files:
            break
    return files, "directory"


def _kv_values(children: list, key: str) -> list[str]:
    return [c["value"] for c in walk(children, key) if c.get("kind") == "kv" and c.get("key") == key]


EVENT_KEYS = ("country_event", "state_event", "news_event", "event")
_PAYLOAD_CACHE: dict = {"key": None, "data": None}


def _fingerprint(files: list[Path]) -> tuple:
    return tuple((str(f), f.stat().st_size, f.stat().st_mtime_ns) for f in files)


def _parse_event(text: str) -> list[dict]:
    out: list[dict] = []
    for node in parse_tree(text):
        if node.get("kind") != "block" or node.get("key") not in EVENT_KEYS:
            continue
        key = node.get("key")
        children = node.get("children", [])
        ev_id = first_kv(children, "id")
        if not ev_id:
            continue
        options = [c for c in children if c.get("kind") == "block" and c.get("key") == "option"]
        ai_chance = 0.0
        for opt in options:
            for chance in walk(opt.get("children", []), "ai_chance"):
                if chance.get("kind") == "kv":
                    try:
                        ai_chance += float(chance["value"])
                    except ValueError:
                        pass
        refs: list[str] = []
        for val in _kv_values(children, "fire_event"):
            if val not in refs:
                refs.append(val)
        for ce in (c for c in walk(children, None) if c.get("kind") == "block" and c.get("key") in ("country_event", "news_event", "state_event", "report_event")):
            cid = first_kv(ce.get("children", []), "id")
            if cid and cid not in refs:
                refs.append(cid)
        out.append({
            "id": ev_id,
            "type": key,
            "title": localisation_text(ev_id + ".t") or localisation_text(ev_id),
            "desc": (localisation_text(ev_id + ".desc") or localisation_text(ev_id + "_desc") or "")[:400],
            "picture": first_kv(children, "picture"),
            "is_triggered_only": any(c.get("kind") == "kv" and c.get("key") == "is_triggered_only" for c in children),
            "fire_only_once": any(c.get("kind") == "kv" and c.get("key") == "fire_only_once" for c in children),
            "hidden": any(c.get("kind") == "kv" and c.get("key") == "hidden" for c in children),
            "option_count": len(options),
            "ai_chance": round(ai_chance, 3),
            "refs": refs,
        })
    return out


def preview_events(path: str | None = None, max_events: int = 300) -> dict:
    files, how = _resolve(path, max_events)
    if not files:
        return {"kind": "events", "error": "no event files found"}
    key = (path, max_events, _fingerprint(files))
    if _PAYLOAD_CACHE["key"] == key:
        return _PAYLOAD_CACHE["data"]
    events: list[dict] = []
    for f in files:
        text = f.read_text(encoding="utf-8", errors="replace")
        events.extend(_parse_event(text))
        if len(events) >= max_events:
            break
    events = events[:max_events]
    ids = {e["id"] for e in events}
    edges: list[dict] = []
    for e in events:
        for ref in e["refs"]:
            if ref in ids and ref != e["id"]:
                edges.append({"from": e["id"], "to": ref, "type": "fire_event"})
    payload = {
        "kind": "events",
        "source_mode": how,
        "files": [str(f) for f in files],
        "source_root": "workspace" if files and files[0].is_relative_to(CONFIG.workspace_root) else "vanilla",
        "count": len(events),
        "events": events,
        "edges": edges,
    }
    _PAYLOAD_CACHE["key"] = key
    _PAYLOAD_CACHE["data"] = payload
    return payload
