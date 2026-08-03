# Owner: ACTIVE
"""Project scan: cached, incremental graphs over the workspace.

Builds file / identifier / dependency / localisation / event / focus / idea /
decision / AI graphs and re-parses only files whose mtime or size changed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .config import CONFIG

ID_SLOT = re.compile(r"\bid\s*=\s*([A-Za-z0-9_.]+)")
PREREQ_FOCUS = re.compile(r"prerequisite\s*=\s*\{\s*focus\s*=\s*([A-Za-z0-9_]+)")
EVENT_REF = re.compile(r"country_event\s*=\s*\{\s*id\s*=\s*([A-Za-z0-9_.]+)")
IDEA_LIST = re.compile(r"(?:add_ideas|remove_ideas|add_timed_idea)\s*=\s*\{([^}]*)\}")
ADVISOR_IDEA = re.compile(r"advisor\s*=\s*\{[^}]*?idea\s*=\s*([A-Za-z0-9_]+)")
LOC_KEY = re.compile(r"^\s*([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*)\s*:\d*\s*\"", re.M)
BLOCK_KEY = re.compile(r"^[ \t]{1,3}([A-Z][A-Za-z0-9_]+)\s*=\s*\{", re.M)

SCAN_DIRS = ("common/national_focus", "events", "common/decisions", "common/ideas",
             "common/characters", "common/ai_strategy", "history/countries",
             "localisation/english", "common/scripted_effects", "common/scripted_triggers")


class ProjectScan:
    def __init__(self, workspace_root: Path | None = None):
        self.root = workspace_root or CONFIG.workspace_root
        self.cache_path = CONFIG.memory_dir / "project_scan_cache.json"
        self.data: dict = {}

    def build(self) -> dict:
        """Return graphs. Re-parses only changed files; results cached on disk."""
        cache = self._load_cache()
        file_meta = cache.get("file_meta", {})
        parsed = cache.get("files", {})
        changed_files: list[str] = []
        candidates: list[Path] = []
        for rel_dir in SCAN_DIRS:
            base = self.root / rel_dir
            if base.exists():
                candidates.extend(p for p in base.rglob("*") if p.is_file())
        for f in sorted(candidates):
            rel = f.relative_to(self.root).as_posix()
            try:
                stat = f.stat()
                key = (stat.st_mtime_ns, stat.st_size)
            except OSError:
                continue
            if file_meta.get(rel) == list(key):
                continue
            file_meta[rel] = list(key)
            changed_files.append(rel)
            try:
                parsed[rel] = self._parse_file(rel, f.read_text(encoding="utf-8", errors="replace"))
            except Exception:  # noqa: BLE001
                parsed[rel] = {"kind": "unknown", "ids": [], "refs": [], "loc_keys": [], "blocks": []}
        for rel in list(parsed):
            if not (self.root / rel).exists():
                parsed.pop(rel, None)
                file_meta.pop(rel, None)
        graphs = self._build_graphs(parsed)
        self.data = {"graphs": graphs, "changed_files": changed_files, "files": parsed}
        self._save_cache({"file_meta": file_meta, "files": parsed})
        return self.data

    # -------------------------------------------------------------- parsing
    def _parse_file(self, rel: str, text: str) -> dict:
        kind = "unknown"
        if rel.startswith("common/national_focus"):
            kind = "focus"
        elif rel.startswith("events"):
            kind = "event"
        elif rel.startswith("common/decisions"):
            kind = "decision"
        elif rel.startswith("common/ideas"):
            kind = "idea"
        elif rel.startswith("common/characters"):
            kind = "character"
        elif rel.startswith("common/scripted_effects"):
            kind = "scripted_effect"
        elif rel.startswith("common/scripted_triggers"):
            kind = "scripted_trigger"
        elif rel.startswith("common/ai_strategy"):
            kind = "ai"
        elif rel.startswith("history/countries"):
            kind = "history"
        elif rel.startswith("localisation/english"):
            kind = "loc"
        ids = list(dict.fromkeys(m.group(1) for m in ID_SLOT.finditer(text)))
        if kind == "decision":
            ids = list(dict.fromkeys(m.group(1) for m in BLOCK_KEY.finditer(text)))
        elif kind == "idea":
            ids = [m.group(1) for m in re.finditer(r"^[ \t]{2,3}([A-Z][A-Za-z0-9_]+)\s*=\s*\{", text, re.M)]
        elif kind == "character":
            ids = [m.group(1) for m in re.finditer(r"^[ \t]{1,2}([A-Z][A-Za-z0-9_]+)\s*=\s*\{", text, re.M)]
        elif kind in ("scripted_effect", "scripted_trigger"):
            ids = [m.group(1) for m in re.finditer(r"^([A-Za-z0-9_]+)\s*=\s*\{", text, re.M)]
        refs = (PREREQ_FOCUS.findall(text) + EVENT_REF.findall(text) +
                [t for m in IDEA_LIST.finditer(text) for t in m.group(1).split()
                 if t not in {"idea", "days", "months", "value", "name", "="} and not t.isdigit()] +
                ADVISOR_IDEA.findall(text))
        loc_keys = list(LOC_KEY.findall(text)) if kind == "loc" else []
        return {"kind": kind, "ids": list(dict.fromkeys(ids)), "refs": list(dict.fromkeys(refs)),
                "loc_keys": loc_keys}

    # -------------------------------------------------------------- graphs
    def _build_graphs(self, parsed: dict[str, dict]) -> dict:
        graphs: dict[str, dict] = {
            "file_graph": {}, "identifier_graph": {}, "localisation_graph": {},
            "event_graph": {}, "focus_graph": {}, "idea_graph": {},
            "decision_graph": {}, "ai_graph": {}, "dependency_graph": {},
        }
        for rel, info in parsed.items():
            graphs["file_graph"][rel] = info["kind"]
            for ident in info["ids"]:
                graphs["identifier_graph"].setdefault(ident, []).append(rel)
                target = {
                    "focus": "focus_graph", "event": "event_graph", "idea": "idea_graph",
                    "decision": "decision_graph",
                }.get(info["kind"])
                if target:
                    graphs[target][ident] = rel
            if info["kind"] == "ai":
                graphs["ai_graph"][rel] = info["ids"]
            for key in info["loc_keys"]:
                graphs["localisation_graph"].setdefault(key, []).append(rel)
            for ref in info["refs"]:
                graphs["dependency_graph"].setdefault(rel, {})[ref] = info["ids"]
        return graphs

    # -------------------------------------------------------------- caching
    def _load_cache(self) -> dict:
        if self.cache_path.exists():
            try:
                return json.loads(self.cache_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save_cache(self, cache: dict) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
