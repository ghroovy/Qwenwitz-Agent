# Owner: ACTIVE
"""World map preview grounded in vanilla map data.

Pipeline: definition.csv (RGB -> province id) + provinces.bmp (pixel colors)
-> full-resolution province id grid -> mode-pooled preview grid -> PNG image
plus per-pixel id/owner arrays for the webview (click a province -> inspect).

The raw bitmap is 5632x2048; the preview grid is 1/4 of that (1408x512),
which keeps payloads in the low megabytes while preserving click precision.
"""

from __future__ import annotations

import base64
import io
import re
from pathlib import Path

import numpy as np
from PIL import Image

from . import raw_game_dir

DEFAULT_SCALE = 4  # 5632/4 x 2048/4
MAX_PROVINCE = 1 << 16

_STATE_RE = re.compile(r"state\s*=\s*\{")
_ID_RE = re.compile(r"\bid\s*=\s*(\d+)")
_PROVS_RE = re.compile(r"provinces\s*=\s*\{([^}]*)\}")
_OWNER_RE = re.compile(r"\bowner\s*=\s*([A-Z0-9]{1,3})")
_NAME_RE = re.compile(r'name\s*=\s*"([^"]+)"')
_STATES_RE = re.compile(r"states\s*=\s*\{([^}]*)\}")

MODE_LABELS = {
    "province": "Province",
    "state": "State",
    "country": "Country",
    "strategic_region": "Strategic Region",
    "supply_area": "Supply Area",
}


class _MapData:
    def __init__(self) -> None:
        self.definition = []          # list of (id, r, g, b, type, coastal, terrain)
        self.id_meta: dict[int, dict] = {}
        self.rgb_lut: np.ndarray | None = None
        self.palette: np.ndarray | None = None
        self.ids_full: np.ndarray | None = None
        self.states: dict[int, dict] = {}
        self.states_loaded = False
        self.regions: dict[int, dict] = {}
        self.region_of_province: dict[int, int] = {}
        self.supply_areas: dict[int, dict] = {}
        self.supply_of_state: dict[int, int] = {}
        self.supply_of_province: dict[int, int] = {}
        self.mode_meta_cache: dict[str, list[dict]] = {}


_CACHE: _MapData | None = None


def _load() -> _MapData:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    d = _MapData()
    map_dir = raw_game_dir() / "map"
    csv = map_dir / "definition.csv"
    if csv.exists():
        for line in csv.read_text(encoding="utf-8", errors="ignore").splitlines():
            parts = line.split(";")
            if len(parts) < 7:
                continue
            try:
                pid = int(parts[0])
                r, g, b = int(parts[1]), int(parts[2]), int(parts[3])
            except ValueError:
                continue
            if pid < 0 or pid >= MAX_PROVINCE:
                continue
            d.definition.append((pid, r, g, b, parts[4], parts[5], parts[6]))
            d.id_meta[pid] = {
                "id": pid,
                "r": r,
                "g": g,
                "b": b,
                "type": parts[4],
                "coastal": parts[5] == "true",
                "terrain": parts[6],
            }
    if d.definition:
        lut = np.zeros(1 << 24, dtype=np.uint16)
        keys = np.array([(r << 16) | (g << 8) | b for _, r, g, b, *_ in d.definition], dtype=np.uint32)
        ids = np.array([pid for pid, *_ in d.definition], dtype=np.uint16)
        lut[keys] = ids
        d.rgb_lut = lut
        palette = np.zeros((MAX_PROVINCE, 3), dtype=np.uint8)
        palette[ids] = np.array([(r, g, b) for _, r, g, b, *_ in d.definition], dtype=np.uint8)
        d.palette = palette
    bmp = map_dir / "provinces.bmp"
    if bmp.exists():
        arr = np.array(Image.open(bmp).convert("RGB"))
        d.ids_full = d.rgb_lut[((arr[:, :, 0].astype(np.uint32) << 16)
                                | (arr[:, :, 1].astype(np.uint32) << 8)
                                | arr[:, :, 2].astype(np.uint32))]
    _load_states(d, vanilla_only=False)
    _load_regions(d)
    _load_supply_areas(d)
    _CACHE = d
    return d


def _load_states(d: _MapData, vanilla_only: bool = False) -> None:
    """state id -> {provinces, owner}; vanilla cached, workspace always fresh."""
    if not d.states_loaded:
        d.states = _parse_states(raw_game_dir() / "history" / "states")
        d.states_loaded = True
    if not vanilla_only:
        from ..config import CONFIG

        ws = CONFIG.workspace_root / "history" / "states"
        if ws.exists():
            d.states.update(_parse_states(ws))


def _parse_states(base: Path) -> dict[int, dict]:
    out: dict[int, dict] = {}
    if not base.exists():
        return out
    for f in base.glob("*.txt"):
        text = f.read_text(encoding="utf-8", errors="ignore")
        for m in _STATE_RE.finditer(text):
            start = m.end()
            depth, i = 1, start
            while i < len(text) and depth > 0:
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                i += 1
            block = text[m.start():i]
            sid_m = _ID_RE.search(block)
            if not sid_m:
                continue
            provs = _PROVS_RE.search(block)
            owner = _OWNER_RE.search(block)
            prov_text = re.sub(r"#[^\n]*", "", provs.group(1)) if provs else ""
            out[int(sid_m.group(1))] = {
                "provinces": [int(p) for p in prov_text.split() if p.lstrip("-").isdigit()],
                "owner": owner.group(1) if owner else "",
            }
    return out


def _load_regions(d: _MapData) -> None:
    """map/strategicregions/*.txt: region id -> {name, provinces}."""
    if d.regions:
        return
    sr_dir = raw_game_dir() / "map" / "strategicregions"
    if not sr_dir.exists():
        return
    for f in sr_dir.glob("*.txt"):
        text = f.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"strategic_region\s*=\s*\{", text):
            start = m.end()
            depth, i = 1, start
            while i < len(text) and depth > 0:
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                i += 1
            block = text[m.start():i]
            rid = _ID_RE.search(block)
            if not rid:
                continue
            name = _NAME_RE.search(block)
            provs = _PROVS_RE.search(block)
            prov_text = re.sub(r"#[^\n]*", "", provs.group(1)) if provs else ""
            prov_ids = [int(p) for p in prov_text.split() if p.lstrip("-").isdigit()]
            rid = int(rid.group(1))
            d.regions[rid] = {
                "id": rid,
                "name": name.group(1) if name else f"STRATEGICREGION_{rid}",
                "provinces": prov_ids,
            }
            for p in prov_ids:
                d.region_of_province[p] = rid


def _load_supply_areas(d: _MapData) -> None:
    """map/supplyareas/*.txt: supply area id -> {name, states}."""
    if d.supply_areas:
        return
    sa_dir = raw_game_dir() / "map" / "supplyareas"
    if not sa_dir.exists():
        return
    for f in sa_dir.glob("*.txt"):
        text = f.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"supply_area\s*=\s*\{", text):
            start = m.end()
            depth, i = 1, start
            while i < len(text) and depth > 0:
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                i += 1
            block = text[m.start():i]
            aid = _ID_RE.search(block)
            if not aid:
                continue
            name = _NAME_RE.search(block)
            states = _STATES_RE.search(block)
            state_text = re.sub(r"#[^\n]*", "", states.group(1)) if states else ""
            state_ids = [int(s) for s in state_text.split() if s.lstrip("-").isdigit()]
            aid = int(aid.group(1))
            d.supply_areas[aid] = {
                "id": aid,
                "name": name.group(1) if name else f"SUPPLYAREA_{aid}",
                "states": state_ids,
            }
            for sid in state_ids:
                d.supply_of_state[sid] = aid


def _mode_pool(grid: np.ndarray, scale: int) -> np.ndarray:
    """Vectorized majority-ish pooling: median of each scale x scale block."""
    h, w = grid.shape
    h2, w2 = h // scale * scale, w // scale * scale
    blocks = grid[:h2, :w2].reshape(h2 // scale, scale, w2 // scale, scale)
    blocks = blocks.transpose(0, 2, 1, 3).reshape(h2 // scale, w2 // scale, scale * scale)
    return np.sort(blocks, axis=-1)[..., (scale * scale) // 2]


def _province_owners(d: _MapData) -> tuple[list[str], dict[int, int]]:
    """Sorted tag list + province id -> owner index (0 = none)."""
    tags: set[str] = set()
    prov_owner: dict[int, str] = {}
    for info in d.states.values():
        if info["owner"]:
            tags.add(info["owner"])
            for p in info["provinces"]:
                prov_owner[p] = info["owner"]
    ordered = sorted(tags)
    index = {t: i + 1 for i, t in enumerate(ordered)}
    return ordered, {p: index[t] for p, t in prov_owner.items()}


def _tag_color(tag: str) -> tuple[int, int, int]:
    """Deterministic pleasant color for a tag (no country file dependency)."""
    h = 0
    for ch in tag:
        h = (h * 31 + ord(ch)) & 0xFFFF
    hue = (h % 360) / 360.0
    import colorsys

    r, g, b = colorsys.hsv_to_rgb(hue, 0.65, 0.85)
    return int(r * 255), int(g * 255), int(b * 255)


def _b64(arr: np.ndarray) -> str:
    return base64.b64encode(arr.tobytes()).decode("ascii")


def _province_state_map(d: _MapData) -> dict[int, int]:
    out: dict[int, int] = {}
    for sid, info in d.states.items():
        for p in info["provinces"]:
            out[p] = int(sid)
    return out


def _mode_grid(d: _MapData, mode: str, scale: int) -> tuple[np.ndarray, list[dict]]:
    """Per-pixel id grid + id->meta list for a view mode."""
    tags, prov_owner = _province_owners(d)
    owner_lut = np.zeros(MAX_PROVINCE, dtype=np.uint8)
    for pid, idx in prov_owner.items():
        owner_lut[pid] = idx
    if mode == "province":
        return _mode_pool(d.ids_full, scale), []
    if mode == "country":
        grid = _mode_pool(owner_lut[d.ids_full], scale).astype(np.uint16)
        meta = [{"id": t, "name": t} for t in tags]
        return grid, meta
    if mode == "state":
        # State ids can exceed 65535 (mods use ids like 900796), so the grid
        # stores a compact 1-based index instead of the raw id.
        ids = sorted(d.states)
        index_of = {sid: i + 1 for i, sid in enumerate(ids)}
        lut = np.zeros(MAX_PROVINCE, dtype=np.uint16)
        for p, sid in _province_state_map(d).items():
            lut[p] = index_of[sid]
        grid = _mode_pool(lut[d.ids_full], scale).astype(np.uint16)
        meta = d.mode_meta_cache.get("state")
        if meta is None:
            from .localisation import _vanilla, _workspace

            loc: dict[str, str] = {}
            loc.update(_vanilla())
            loc.update(_workspace())
            meta = []
            for sid in ids:
                info = d.states[sid]
                name = loc.get(f"STATE_{sid}") or f"STATE_{sid}"
                meta.append({"id": sid, "name": name, "owner": info["owner"],
                             "provinces": len(info["provinces"])})
            d.mode_meta_cache["state"] = meta
        return grid, meta
    if mode == "strategic_region":
        lut = np.zeros(MAX_PROVINCE, dtype=np.uint32)
        for p, rid in d.region_of_province.items():
            lut[p] = rid
        grid = _mode_pool(lut[d.ids_full], scale).astype(np.uint16)
        meta = [{"id": rid, "name": r["name"], "provinces": len(r["provinces"])}
                for rid, r in sorted(d.regions.items())]
        return grid, meta
    if mode == "supply_area":
        # A supply area owns states; paint its state provinces.
        prov_supply: dict[int, int] = {}
        for sid, aid in d.supply_of_state.items():
            info = d.states.get(sid)
            if info:
                for p in info["provinces"]:
                    prov_supply[p] = aid
        lut = np.zeros(MAX_PROVINCE, dtype=np.uint32)
        for p, aid in prov_supply.items():
            lut[p] = aid
        grid = _mode_pool(lut[d.ids_full], scale).astype(np.uint16)
        meta = [{"id": aid, "name": a["name"], "states": len(a["states"])}
                for aid, a in sorted(d.supply_areas.items())]
        return grid, meta
    return _mode_pool(d.ids_full, scale), []


def preview_map(highlight_tag: str = "", max_width: int = 1408, mode: str = "province") -> dict:
    """Build the map preview payload."""
    d = _load()
    if d.ids_full is None or d.palette is None:
        return {"error": "map data unavailable (definition.csv / provinces.bmp missing)"}
    _load_states(d, vanilla_only=False)
    _load_regions(d)
    _load_supply_areas(d)
    from ..config import CONFIG

    ws_states = CONFIG.workspace_root / "history" / "states"
    workspace_overlay = ws_states.exists() and any(ws_states.glob("*.txt"))
    scale = max(1, int(np.ceil(d.ids_full.shape[1] / max_width)))
    ids_pool = _mode_pool(d.ids_full, scale)
    tags, prov_owner = _province_owners(d)
    owner_lut = np.zeros(MAX_PROVINCE, dtype=np.uint8)
    for pid, idx in prov_owner.items():
        owner_lut[pid] = idx
    owner_full = owner_lut[d.ids_full]
    owner_pool = _mode_pool(owner_full, scale)
    mode_ids, mode_meta = _mode_grid(d, mode, scale)
    img = d.palette[ids_pool]
    highlight_mask = None
    if highlight_tag:
        mask = np.zeros(ids_pool.shape, dtype=np.uint8)
        idx = tags.index(highlight_tag) + 1 if highlight_tag in tags else 0
        if idx:
            mask[owner_pool == idx] = 1
        highlight_mask = mask
    png = io.BytesIO()
    Image.fromarray(img).save(png, format="PNG")
    types: dict[str, int] = {}
    for meta in d.id_meta.values():
        types[meta["type"]] = types.get(meta["type"], 0) + 1
    payload = {
        "kind": "map",
        "source_root": "vanilla",
        "workspace_overlay": workspace_overlay,
        "width": int(ids_pool.shape[1]),
        "height": int(ids_pool.shape[0]),
        "scale": scale,
        "image": "data:image/png;base64," + base64.b64encode(png.getvalue()).decode("ascii"),
        "ids": _b64(ids_pool),
        "owners": _b64(owner_pool),
        "owner_tags": tags,
        "mode": mode,
        "mode_label": MODE_LABELS.get(mode, mode),
        "mode_ids": _b64(mode_ids),
        "mode_meta": mode_meta,
        "province_types": types,
        "highlight_tag": highlight_tag,
        "sources": [
            "map/definition.csv",
            "map/provinces.bmp",
            "history/states/*.txt",
            "map/default.map",
        ],
    }
    if highlight_mask is not None:
        payload["highlight_mask"] = _b64(highlight_mask)
    return payload


def _state_name(state_id: int) -> str:
    from .localisation import localisation_text

    return localisation_text(f"STATE_{state_id}") or f"STATE_{state_id}"


def state_info(state_id: int) -> dict:
    d = _load()
    _load_states(d, vanilla_only=False)
    info = d.states.get(int(state_id))
    if not info:
        return {"ok": False, "message": f"state {state_id} not found in history/states"}
    return {
        "ok": True,
        "type": "state",
        "id": int(state_id),
        "name": _state_name(state_id),
        "owner": info["owner"] or "",
        "provinces": list(info["provinces"]),
        "province_count": len(info["provinces"]),
    }


def strategic_region_info(region_id: int) -> dict:
    d = _load()
    _load_regions(d)
    r = d.regions.get(int(region_id))
    if not r:
        return {"ok": False, "message": f"strategic region {region_id} not found"}
    from .localisation import localisation_text

    return {
        "ok": True,
        "type": "strategic_region",
        "id": int(region_id),
        "name": localisation_text(r["name"]) or r["name"],
        "provinces": list(r["provinces"]),
        "province_count": len(r["provinces"]),
    }


def supply_area_info(area_id: int) -> dict:
    d = _load()
    _load_supply_areas(d)
    a = d.supply_areas.get(int(area_id))
    if not a:
        return {"ok": False, "message": f"supply area {area_id} not found"}
    from .localisation import localisation_text

    return {
        "ok": True,
        "type": "supply_area",
        "id": int(area_id),
        "name": localisation_text(a["name"]) or a["name"],
        "states": list(a["states"]),
        "state_count": len(a["states"]),
    }


def province_info(province_id: int) -> dict:
    """Details for a clicked province: meta, state, owner, neighbors not included."""
    d = _load()
    _load_states(d, vanilla_only=False)
    meta = dict(d.id_meta.get(int(province_id), {}))
    if not meta:
        return {"ok": False, "message": f"province {province_id} not in definition.csv"}
    state = None
    owner = ""
    for sid, info in d.states.items():
        if int(province_id) in info["provinces"]:
            state = sid
            owner = info["owner"]
            break
    meta["state"] = state
    meta["owner"] = owner
    meta["state_name"] = _state_name(state) if state else ""
    meta["state_info"] = state_info(state) if state else None
    meta["ok"] = True
    return meta
