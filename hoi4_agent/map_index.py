# Owner: ACTIVE
"""Map/province index grounded in vanilla map data.

Parses map/definition.csv (province types) and history/states (state -> owned
provinces) so generated OOBs can place divisions in provinces a country
actually controls, and new states can claim unoccupied land provinces.
"""

from __future__ import annotations

import re
from pathlib import Path

from .config import CONFIG

_CACHE: dict | None = None


def _parse_states(root: Path) -> dict:
    """state id -> {"provinces": [...], "owner": tag} for a states directory."""
    out: dict[int, dict] = {}
    base = root / "history" / "states"
    if not base.exists():
        return out
    for f in base.glob("*.txt"):
        text = f.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"state\s*=\s*\{", text):
            start = m.end()
            depth = 1
            i = start
            while i < len(text) and depth > 0:
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                i += 1
            block = text[m.start():i]
            sid = re.search(r"\bid\s*=\s*(\d+)", block)
            if not sid:
                continue
            provs = re.search(r"provinces\s*=\s*\{([^}]*)\}", block)
            owner = re.search(r"\bowner\s*=\s*([A-Z0-9]{1,3})", block)
            prov_text = re.sub(r"#[^\n]*", "", provs.group(1)) if provs else ""
            out[int(sid.group(1))] = {
                "provinces": [int(p) for p in prov_text.split() if p.lstrip("-").isdigit()],
                "owner": owner.group(1) if owner else "",
            }
    return out


def build_map_index() -> dict:
    """{land_provinces, used_provinces, free_land_provinces, states_by_owner}."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    land: list[int] = []
    csv = CONFIG.index_dir.parent.parent / "raw" / "game" / "map" / "definition.csv"
    if csv.exists():
        for line in csv.read_text(encoding="utf-8", errors="ignore").splitlines():
            parts = line.split(";")
            if len(parts) >= 5 and parts[4].strip().lower() == "land":
                try:
                    pid = int(parts[0])
                except ValueError:
                    continue
                if pid > 0:
                    land.append(pid)
    states = _parse_states(CONFIG.index_dir.parent.parent / "raw" / "game")
    ws_states = _parse_states(CONFIG.workspace_root)
    states.update(ws_states)
    used = {p for s in states.values() for p in s["provinces"]}
    free = [p for p in land if p not in used]
    states_by_owner: dict[str, list[int]] = {}
    for sid, info in states.items():
        if info["owner"]:
            states_by_owner.setdefault(info["owner"], []).append(sid)
    _CACHE = {
        "land_provinces": land,
        "used_provinces": sorted(used),
        "free_land_provinces": free,
        "states_by_owner": states_by_owner,
        "states": states,
    }
    return _CACHE


def free_land_provinces(n: int, skip: set[int] | None = None) -> list[int]:
    idx = build_map_index()
    skip = skip or set()
    pool = [p for p in idx["free_land_provinces"] if p not in skip][:n]
    if len(pool) < n:
        pool += [p for p in idx["land_provinces"] if p not in pool and p not in skip][: n - len(pool)]
    return pool


def owned_provinces(tag: str) -> list[int]:
    """Land provinces owned by a tag across vanilla + workspace states."""
    idx = build_map_index()
    land = set(idx["land_provinces"])
    provs: list[int] = []
    for sid in idx["states_by_owner"].get(tag, []):
        provs.extend(p for p in idx["states"][sid]["provinces"] if p in land)
    return sorted(set(provs))
