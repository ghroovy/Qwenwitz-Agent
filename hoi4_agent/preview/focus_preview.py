# Owner: ACTIVE
"""Focus tree preview: parse vanilla/workspace national_focus files."""

from __future__ import annotations

from pathlib import Path

from hoi4_agent._runtime.hoi4parser import first_kv, node_raw, parse_tree, walk  # noqa: E402

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
    return None


def _has_focus_tree(f: Path) -> bool:
    try:
        return "focus_tree = {" in f.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def _default_files() -> list[Path]:
    """Workspace focus files first (aggregated); vanilla fallback = one file."""
    ws = CONFIG.workspace_root / "common" / "national_focus"
    if ws.exists():
        ws_files = [f for f in sorted(ws.glob("*.txt")) if _has_focus_tree(f)]
        if ws_files:
            return ws_files
    for root in (CONFIG.workspace_root, raw_game_dir()):
        base = root / "common" / "national_focus"
        if base.exists():
            for f in sorted(base.glob("*.txt")):
                if _has_focus_tree(f):
                    return [f]
    return []


def _country_tags(tree_children: list) -> list[str]:
    tags: list[str] = []
    for country in (c for c in tree_children if c.get("kind") == "block" and c.get("key") == "country"):
        for mod in walk(country["children"], "modifier"):
            if mod.get("kind") == "block":
                tag = first_kv(mod["children"], "tag")
                if tag:
                    tags.append(tag)
    return sorted(set(tags))


def _prerequisites(children: list) -> list[str]:
    out: list[str] = []
    for pre in (c for c in children if c.get("kind") == "block" and c.get("key") == "prerequisite"):
        fid = first_kv(pre["children"], "focus")
        if fid:
            out.append(fid)
    return out


def _mutually_exclusive(children: list) -> list[str]:
    out: list[str] = []
    for mut in (c for c in children if c.get("kind") == "block" and c.get("key") == "mutually_exclusive"):
        fid = first_kv(mut["children"], "focus")
        if fid:
            out.append(fid)
    return out


def _parse_file(file: Path) -> tuple[list[dict], list[dict]]:
    text = file.read_text(encoding="utf-8", errors="replace")
    nodes = parse_tree(text)
    trees: list[dict] = []
    edges: list[dict] = []
    for tree in (n for n in nodes if n.get("kind") == "block" and n.get("key") == "focus_tree"):
        children = tree.get("children", [])
        tid = first_kv(children, "id") or "focus_tree"
        focus_nodes: list[dict] = []
        for focus in (c for c in children if c.get("kind") == "block" and c.get("key") == "focus"):
            fc = focus.get("children", [])
            fid = first_kv(fc, "id")
            if not fid:
                continue
            x = first_kv(fc, "x")
            y = first_kv(fc, "y")
            loc = title_and_desc(fid)
            completion = any(c.get("kind") == "block" and c.get("key") == "completion_reward" for c in fc)
            focus_nodes.append({
                "id": fid,
                "x": int(x) if x else None,
                "y": int(y) if y else None,
                "prerequisites": _prerequisites(fc),
                "mutually_exclusive": _mutually_exclusive(fc),
                "relative_position_id": first_kv(fc, "relative_position_id"),
                "has_completion_reward": completion,
                "title": loc["title"],
                "desc": (loc["desc"] or "")[:400],
            })
        if focus_nodes:  # skip trees whose focuses are all commented out
            trees.append({
                "id": tid,
                "country_tags": _country_tags(children),
                "focus_count": len(focus_nodes),
                "initial_show_position": {
                    "x": first_kv(children, "x"),
                    "y": first_kv(children, "y"),
                },
                "nodes": focus_nodes,
            })
    by_id = {n["id"]: n for tree in trees for n in tree["nodes"]}
    for tree in trees:
        for node in tree["nodes"]:
            for pre in node["prerequisites"]:
                if pre != node["id"]:
                    edges.append({"from": pre, "to": node["id"], "type": "prerequisite"})
            for mut in node["mutually_exclusive"]:
                if mut in by_id:
                    edges.append({"from": node["id"], "to": mut, "type": "mutually_exclusive"})
    return trees, edges


def preview_focus_tree(path: str | None = None) -> dict:
    if path:
        file = _resolve(path)
        files = [file] if file else []
    else:
        files = _default_files()
    if not files:
        return {"kind": "focus_tree", "error": "no national_focus file found"}
    all_trees: list[dict] = []
    all_edges: list[dict] = []
    sources: list[str] = []
    for file in files:
        trees, edges = _parse_file(file)
        all_trees.extend(trees)
        all_edges.extend(edges)
        sources.append(str(file))
    return {
        "kind": "focus_tree",
        "file": sources[0],
        "files": sources,
        "source_root": "workspace" if Path(sources[0]).is_relative_to(CONFIG.workspace_root) else "vanilla",
        "trees": all_trees,
        "edges": all_edges,
        "sources": sources,
    }
