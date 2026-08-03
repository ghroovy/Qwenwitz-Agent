# Owner: STABLE
"""Identifier index access (loaded once per process; never rebuilt at runtime)."""

from __future__ import annotations

import difflib
import json
from pathlib import Path

from .config import CONFIG

CATEGORY_LABELS = {
    "focuses": "national focus",
    "events": "event",
    "decisions": "decision",
    "ideas": "idea/national spirit",
    "scripted_effects": "scripted effect",
    "scripted_triggers": "scripted trigger",
    "on_actions": "on_action",
    "countries": "country tag",
    "states": "state",
    "modifiers": "modifier",
    "dynamic_variables": "dynamic variable",
    "localisation": "localisation key",
}


class IdentifierIndex:
    """Exact + fuzzy lookups over the prebuilt vanilla identifier index."""

    def __init__(self, index_dir: Path | None = None):
        self.index_dir = Path(index_dir or CONFIG.index_dir)
        self._data: dict[str, dict[str, str]] = {}
        self._known: set[str] = set()
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        for name in ("focuses", "events", "decisions", "ideas", "scripted_effects",
                     "scripted_triggers", "on_actions", "countries", "states",
                     "modifiers", "dynamic_variables", "localisation"):
            path = self.index_dir / f"{name}.json"
            if path.exists():
                self._data[name] = json.loads(path.read_text(encoding="utf-8"))
                self._known.update(self._data[name])
        self._loaded = True

    def categories(self) -> dict[str, dict[str, str]]:
        self._load()
        return self._data

    def exact(self, name: str) -> list[dict]:
        """Case-sensitive exact match across categories."""
        self._load()
        out = []
        for cat, mapping in self._data.items():
            if name in mapping:
                out.append({
                    "identifier": name,
                    "category": cat,
                    "label": CATEGORY_LABELS.get(cat, cat),
                    "source": mapping[name],
                })
        return out

    def search(self, name: str) -> list[dict]:
        """Case-insensitive exact + prefix lookups (localisation via prefix only)."""
        self._load()
        out = self.exact(name)
        low = name.lower()
        if not out:
            for cat, mapping in self._data.items():
                if cat == "localisation":
                    continue
                for key in mapping:
                    if key.lower() == low:
                        out.append({"identifier": key, "category": cat,
                                    "label": CATEGORY_LABELS.get(cat, cat), "source": mapping[key]})
        if not out:
            for cat in ("focuses", "events", "decisions", "ideas", "scripted_effects",
                        "scripted_triggers", "on_actions", "countries"):
                matches = [k for k in self._data.get(cat, {}) if k.lower().startswith(low)][:5]
                out.extend({"identifier": k, "category": cat,
                            "label": CATEGORY_LABELS.get(cat, cat), "source": self._data[cat][k]}
                           for k in matches)
        return out

    def fuzzy(self, name: str, limit: int = 8) -> list[dict]:
        """Edit-distance suggestions over the fuzzy categories."""
        self._load()
        out: list[dict] = []
        scored: list[tuple[float, dict]] = []
        for cat in CONFIG.fuzzy_categories:
            mapping = self._data.get(cat, {})
            if not mapping:
                continue
            matches = difflib.get_close_matches(name, list(mapping), n=limit, cutoff=0.45)
            for m in matches:
                ratio = difflib.SequenceMatcher(None, name.lower(), m.lower()).ratio()
                scored.append((ratio, {"identifier": m, "category": cat,
                                       "label": CATEGORY_LABELS.get(cat, cat), "source": mapping[m]}))
        scored.sort(key=lambda kv: -kv[0])
        return [r for _, r in scored[:limit]]

    def contains(self, name: str) -> bool:
        self._load()
        return name in self._known

    def known_set(self) -> set[str]:
        self._load()
        # Copy: callers (Validator.register_workspace) must be able to add
        # workspace-defined ids without polluting the vanilla index, which is
        # what `_new_id`-style collision checks depend on.
        return set(self._known)
