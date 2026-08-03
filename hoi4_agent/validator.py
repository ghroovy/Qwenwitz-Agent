# Owner: ACTIVE
"""Deterministic validation with structured, typed errors.

Every error is an object:
  {"type": ..., "identifier": ..., "file": ..., "line": ..., "message": ...}
The repair engine consumes these directly.
"""

from __future__ import annotations

import re

from .config import CONFIG  # noqa: E402
from .filesystem import walk_text_files, workspace  # noqa: E402
from hoi4_agent._runtime.common import check_delimiters  # noqa: E402

VERB_PREFIX = re.compile(
    r"^(add|set|clr|remove|create|transfer|declare|release|give|unlock|activate|save|load|"
    r"print|break|export|copy|clear|change|divide|multiply|subtract|round|clamp|modulo)_"
)
TRIGGER_PREFIX = re.compile(r"^(has_|is_|can_|exists_|count_|any_|all_)")
ID_SLOT = re.compile(r"\bid\s*=\s*([A-Za-z0-9_.]+)")
PREREQ_FOCUS = re.compile(r"prerequisite\s*=\s*\{\s*focus\s*=\s*([A-Za-z0-9_]+)")
IDEA_LIST = re.compile(r"(?:add_ideas|remove_ideas|add_timed_idea)\s*=\s*\{([^}]*)\}")
EFFECT_LINE = re.compile(r"^[ \t]{1,6}([a-z_][a-z0-9_]*)\s*=\s*\{", re.M)
BLOCK_LINE = re.compile(r"^[ \t]{1,6}([a-z_][a-z0-9_]*)\s*=\s*(\{|[^\s{])", re.M)
ICON_LINE = re.compile(r"^\s*icon\s*=\s*(\S+)", re.M)
LOC_KEY = re.compile(r"^\s*([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*)\s*:\d*\s*\"", re.M)

ALLOW_BLOCK_KEYS = {
    "if", "else", "limit", "trigger", "effect", "options", "option",
    "prerequisite", "bypass_if", "available_if_cap", "completion_reward",
    "ai_will_do", "modifier", "factor", "add", "count", "name", "picture",
    "title", "desc", "mean_time_to_happen", "hidden", "fire_only_once",
    "is_triggered_only", "news_event", "country_event", "report_event",
    "focus", "focus_tree", "country", "character", "state", "units",
    "equipment", "random_list", "random", "days", "months", "base", "message",
    "tag", "value", "x", "y", "tooltip", "custom_tooltip", "complete_effect",
    "remove_effect", "available", "visible", "cooldown", "cost", "days_remove",
    "cancel_decision", "priority", "allowed", "set_variable", "change_variable",
    "subtract_from_variable", "multiply_variable", "divide_variable",
    "clamp_variable", "round_variable", "modulo_variable", "export_to_variable",
    "add_to_variable", "set_temp_variable", "random_owned_controlled_state",
    "every_owned_state", "random_core_state", "every_state", "random_state",
    "any_state", "every_country", "random_country", "any_country",
    "every_other_country", "random_other_country", "every_enemy_country",
    "random_enemy_country", "every_neighbor_country", "random_neighbor_country",
    "every_occupied_country", "random_occupied_country", "every_controlled_state",
    "random_controlled_state", "every_possible_country", "every_owned_controlled_state",
    "any_owned_state", "any_owned_controlled_state", "any_enemy_owned_state",
    "any_neighbor_country", "random_enemy_owned_state", "search_filters",
    "cancel_if_invalid", "continue_if_invalid", "available_if_capitulated",
    "will_lead_to_war_with", "relative_position_id", "mutually_exclusive",
    "custom_effect_tooltip", "show_ideas_tooltip", "show_focus_effect",
    "allowed_ideas", "bypass", "add_ideas", "remove_ideas", "add_timed_idea",
    "remove_timed_idea", "add_idea", "remove_idea", "create_country_leader",
    "random_select_amount", "free_building_slots", "add_extra_state_shared_building",
    "add_named_threat", "add_doctrine_cost_reduction", "air_experience",
    "army_experience", "navy_experience", "add_building_construction",
    "set_politics", "set_technology", "set_oob", "set_naval_oob", "set_air_oob",
    "set_popularities", "division_template", "units", "division", "regiments",
    "infantry", "cavalry", "light_armor", "start_experience_factor",
    "division_names_group", "parties", "ruling_party", "elections_allowed",
    "election_frequency", "last_election",
    "is_buildable",
    # OOB / history-units keys (valid in units = { division = {...} } blocks)
    "start_experience_factor", "start_equipment_factor", "start_manpower_factor",
    "start_research_factor", "start_planning_factor", "start_org_factor",
    "start_priority_factor",
}


def build_icon_set() -> set[str]:
    """Verified GFX icons collected from vanilla focus/decision/idea files."""
    icons: set[str] = set()
    roots = [
        CONFIG.index_dir.parent.parent / "raw" / "game" / "common" / "national_focus",
        CONFIG.index_dir.parent.parent / "raw" / "game" / "common" / "decisions",
        CONFIG.index_dir.parent.parent / "raw" / "game" / "common" / "ideas",
    ]
    for root in roots:
        if not root.exists():
            continue
        for f in walk_text_files(root):
            for m in ICON_LINE.finditer(f.read_text(encoding="utf-8", errors="ignore")):
                val = m.group(1).strip()
                if val.startswith("GFX_"):
                    icons.add(val)
    return icons


class Validator:
    def __init__(self, index, effects_docs: dict | None = None, triggers_docs: dict | None = None,
                 modifiers_docs: dict | None = None, icon_set: set[str] | None = None):
        self.index = index
        self.known = index.known_set()
        self._workspace_known: set[str] = set()
        self._workspace_cats: dict[str, set[str]] = {}
        self.effects = effects_docs or {}
        self.triggers = triggers_docs or {}
        self.modifiers = modifiers_docs or {}
        self.icon_set = icon_set if icon_set is not None else set()
        self._icons_loaded = icon_set is not None
        self.categories = index.categories()

    # ------------------------------------------------------------------ utils
    def _ensure_icons(self) -> None:
        if not self._icons_loaded:
            self.icon_set = build_icon_set()
            self._icons_loaded = True

    def register_workspace(self, scan: dict) -> None:
        """Merge identifiers defined by the workspace mod into the known set.

        The vanilla index is authoritative for vanilla content; identifiers a
        mod *defines* in its own files (focuses, events, decisions, ideas,
        characters, scripted effects/triggers, localisation) must not be
        flagged as unknown.
        """
        cat_by_kind = {
            "focus": "focuses",
            "event": "events",
            "decision": "decisions",
            "idea": "ideas",
            "character": "characters",
            "scripted_effect": "scripted_effects",
            "scripted_trigger": "scripted_triggers",
        }
        for rel, info in (scan.get("files") or {}).items():
            kind = info.get("kind")
            cat = cat_by_kind.get(kind)
            for ident in info.get("ids") or []:
                self.known.add(ident)
                self._workspace_known.add(ident)
                if cat and ident not in self.categories.get(cat, {}):
                    self.categories.setdefault(cat, {})[ident] = rel
                    self._workspace_cats.setdefault(cat, set()).add(ident)
            for key in info.get("loc_keys") or []:
                self.known.add(key)
                self._workspace_known.add(key)

    def refresh_workspace(self) -> None:
        """Re-scan the workspace so disk validators never flag the mod's own
        (agent-created or user-added) identifiers as unknown."""
        self.known -= self._workspace_known
        for cat, ids in self._workspace_cats.items():
            cats = self.categories.get(cat)
            if cats:
                for ident in ids:
                    cats.pop(ident, None)
        self._workspace_known = set()
        self._workspace_cats = {}
        try:
            from .project_scan import ProjectScan
            self.register_workspace(ProjectScan().build())
        except Exception:  # noqa: BLE001 - validation must never crash
            pass

    @staticmethod
    def _err(etype: str, message: str, identifier: str | None = None,
             file: str | None = None, line: int | None = None) -> dict:
        return {"type": etype, "identifier": identifier, "file": file,
                "line": line, "message": message}

    @staticmethod
    def _find_line(text: str, needle: str, start: int = 0) -> int | None:
        idx = text.find(needle, start)
        if idx < 0:
            return None
        return text.count("\n", 0, idx) + 1

    # ---------------------------------------------------------------- code
    def validate_code(self, text: str, allowed_new_ids: set[str] | None = None,
                      workspace_focus_ids: set[str] | None = None,
                      source_file: str | None = None) -> dict:
        allowed = set(allowed_new_ids or set())
        wf = set(workspace_focus_ids or set())
        errors: list[dict] = []
        warnings: list[dict] = []

        ok, msg = check_delimiters(text)
        if not ok:
            errors.append(self._err("brace_mismatch", f"unbalanced delimiters: {msg}",
                                    file=source_file))

        scripted_effects = self.categories.get("scripted_effects", {})
        scripted_triggers = self.categories.get("scripted_triggers", {})
        focuses = self.categories.get("focuses", {})
        ideas = self.categories.get("ideas", {})

        for m in ID_SLOT.finditer(text):
            tok = m.group(1)
            if tok in allowed or tok in wf or tok in self.known:
                continue
            errors.append(self._err(
                "unknown_identifier", f"identifier not found in vanilla index: {tok}",
                identifier=tok, file=source_file, line=self._find_line(text, tok, m.start()),
            ))
        for m in PREREQ_FOCUS.finditer(text):
            tok = m.group(1)
            if tok in wf or tok in focuses or tok in allowed:
                continue
            errors.append(self._err(
                "broken_reference", f"prerequisite focus not found (vanilla or workspace): {tok}",
                identifier=tok, file=source_file, line=self._find_line(text, tok, m.start()),
            ))
        for m in IDEA_LIST.finditer(text):
            body = re.sub(r"#[^\n]*", "", m.group(1))
            for tok in body.split():
                if tok in {"idea", "days", "months", "value", "name", "="} or tok.isdigit():
                    continue
                if tok not in ideas and tok not in allowed:
                    errors.append(self._err(
                        "unknown_identifier", f"idea not found in vanilla index: {tok}",
                        identifier=tok, file=source_file,
                        line=self._find_line(text, tok, m.start()),
                    ))

        for m in EFFECT_LINE.finditer(text):
            name = m.group(1)
            if name in ALLOW_BLOCK_KEYS or name in self.effects or name in scripted_effects:
                continue
            if VERB_PREFIX.match(name):
                errors.append(self._err(
                    "invalid_effect", f"effect not in docs or scripted effects: {name}",
                    identifier=name, file=source_file, line=self._find_line(text, name, m.start()),
                ))
            elif TRIGGER_PREFIX.match(name) and name not in self.triggers and name not in scripted_triggers:
                errors.append(self._err(
                    "invalid_trigger", f"trigger not in docs or scripted triggers: {name}",
                    identifier=name, file=source_file, line=self._find_line(text, name, m.start()),
                ))
        # Plain-value lines (`has_annexed = POL`, `add_political_powr = 50`)
        # and comparison lines (`has_war_support < 0.3`) must also be checked:
        # undocumented verb/trigger-prefixed keys are hallucinations.
        for m in re.finditer(r"^[ \t]{1,6}([a-z_][a-z0-9_]*)\s*[=<>\n]", text, re.M):
            name = m.group(1)
            if name in ALLOW_BLOCK_KEYS or name in self.effects \
                    or name in self.triggers or name in scripted_effects \
                    or name in scripted_triggers:
                continue
            if VERB_PREFIX.match(name):
                errors.append(self._err(
                    "invalid_effect",
                    f"effect not in docs or scripted effects: {name}",
                    identifier=name, file=source_file,
                    line=self._find_line(text, name, m.start()),
                ))
            elif TRIGGER_PREFIX.match(name):
                errors.append(self._err(
                    "invalid_trigger",
                    f"trigger not in docs or scripted triggers: {name}",
                    identifier=name, file=source_file,
                    line=self._find_line(text, name, m.start()),
                ))

        self._check_scope(text, errors, source_file)

        self._check_icons(text, errors, source_file)
        return {"valid": not errors, "errors": errors, "warnings": warnings,
                "identifiers_used": sorted(set(ID_SLOT.findall(text)) | set(PREREQ_FOCUS.findall(text)))}

    def _check_scope(self, text: str, errors: list[dict], source_file: str | None) -> None:
        state_scopes = {
            "random_owned_controlled_state", "every_owned_state", "random_core_state",
            "every_state", "random_state", "any_state", "every_owned_controlled_state",
            "random_owned_state", "every_controlled_state", "random_controlled_state",
            "random_enemy_owned_state", "any_owned_controlled_state",
            "random_neighbor_state", "every_neighbor_state",
        }
        state_only = {
            k for k, v in self.effects.items()
            if "STATE" in v.get("scopes", []) and "COUNTRY" not in v.get("scopes", [])
        } - state_scopes
        if not state_only:
            return
        # Pre-compile once: matching per line against ~200 effects must not
        # re-escape/re-compile a pattern per (line, effect) pair.
        scope_effect_re = re.compile(
            r"^[ \t]*(?:" + "|".join(re.escape(e) for e in state_only) + r")\s*=")
        country_blocks = {"completion_reward", "country_event", "news_event", "report_event",
                          "option", "focus", "decision"}
        stack: list[str | None] = []
        for i, line in enumerate(text.splitlines(), 1):
            s = line.strip()
            if s.startswith("#"):
                continue
            m = re.match(r"^([A-Za-z0-9_:.][A-Za-z0-9_:.]*)\s*=\s*\{", s)
            pushed = False
            for ch in line:
                if ch == "{":
                    if m is not None and not pushed:
                        stack.append(m.group(1))
                        pushed = True
                    else:
                        stack.append(None)
                elif ch == "}":
                    if stack:
                        stack.pop()
            in_state = any(name in stack for name in state_scopes) or \
                any(k is not None and k.isdigit() for k in stack) or \
                any(k is not None and k.startswith(("var:", "event_target:")) for k in stack)  # var/event-target scopes may hold states
            in_country = any(name in stack for name in country_blocks)
            if in_country and not in_state:
                m = scope_effect_re.match(line)
                if m:
                    eff = m.group(0).strip().rstrip("=").strip()
                    errors.append(self._err(
                        "invalid_scope",
                        f"state-scope effect `{eff}` used directly in a country context",
                        identifier=eff, file=source_file, line=i,
                    ))

    def _check_icons(self, text: str, errors: list[dict], source_file: str | None) -> None:
        self._ensure_icons()
        for m in ICON_LINE.finditer(text):
            val = m.group(1).strip()
            if not val or val in ('""', ""):
                errors.append(self._err("missing_sprite", "icon value is empty",
                                        identifier=val, file=source_file,
                                        line=self._find_line(text, m.group(0))))
            elif val.startswith("GFX_") and val not in self.icon_set:
                errors.append(self._err(
                    "unknown_icon", f"icon not found among vanilla GFX icons: {val}",
                    identifier=val, file=source_file, line=self._find_line(text, val, m.start()),
                ))

    # ------------------------------------------------------------ proposals
    def validate_proposal(self, proposals: dict[str, str],
                          allowed_new_ids: set[str] | None = None) -> dict:
        """Validate a set of proposed file contents as if they were on disk."""
        errors: list[dict] = []
        warnings: list[dict] = []
        allowed = set(allowed_new_ids or set())
        for content in proposals.values():
            allowed.update(m.group(1) for m in ID_SLOT.finditer(content))
            allowed.update(m.group(1) for m in re.finditer(
                r"^[ \t]{0,2}([A-Za-z0-9_]+)\s*=\s*\{", content, re.M))
        all_ids: list[tuple[str, str]] = []  # (id, kind)
        loc_keys: set[str] = set()
        focus_ids: set[str] = set()

        for path, content in proposals.items():
            if "localisation" in path or path.endswith(".yml"):
                loc_keys.update(self._extract_loc_keys(content))
                continue
            errors += self._check_content(path, content, allowed)
            all_ids.extend(self._collect_ids(path, content))

        # disk localisation keys also count as existing
        loc_keys |= self._workspace_loc_keys()
        # cross-file duplicates
        for kind in ("focus", "event", "decision"):
            ids = [i for i, k in all_ids if k == kind]
            for dup in sorted({i for i in ids if ids.count(i) > 1}):
                errors.append(self._err(
                    "duplicate_event_id" if kind == "event" else "duplicate_identifier",
                    f"duplicate {kind} id across proposals: {dup}", identifier=dup,
                ))
        # localisation coverage
        for ident, kind in all_ids:
            needed = [ident] if kind == "focus" else [ident + ".t", ident + ".d"] if kind == "event" else [ident, ident + "_desc"]
            missing = [n for n in needed if n not in loc_keys
                       and n not in self.categories.get("localisation", {})]
            if missing:
                warnings.append(self._err(
                    "missing_localisation",
                    f"{kind} `{ident}` missing localisation keys: {', '.join(missing)}",
                    identifier=ident,
                ))
        # missing_localisation counts as an error for the repair loop
        errors.extend(warnings)
        return {"valid": not errors, "errors": errors, "warnings": warnings,
                "proposal_files": list(proposals)}

    def _check_content(self, path: str, content: str, allowed: set[str]) -> list[dict]:
        from hoi4_agent._runtime.hoi4parser import parse_tree

        errors: list[dict] = []
        ok, msg = check_delimiters(content)
        if not ok:
            errors.append(self._err("brace_mismatch", f"unbalanced delimiters: {msg}", file=path))
        code_res = self.validate_code(content, allowed_new_ids=allowed, source_file=path)
        errors.extend(code_res["errors"])
        try:
            tree = parse_tree(content)
        except Exception as exc:  # noqa: BLE001
            errors.append(self._err("brace_mismatch", f"parse failure: {exc}", file=path))
            return errors
        self._check_required_blocks(path, content, tree, errors)
        self._check_modifiers(path, content, errors)
        return errors

    def _check_required_blocks(self, path, content, tree, errors) -> None:
        from hoi4_agent._runtime.hoi4parser import walk

        for node in (n for n in walk(tree, "focus") if n.get("kind") == "block"):
            if not any(c.get("kind") == "kv" and c.get("key") == "id" for c in node.get("children", [])):
                errors.append(self._err("missing_required_block", "focus block has no id",
                                        file=path, line=content.count("\n", 0, node.get("start", 0)) + 1))
        for key in ("country_event", "news_event", "report_event"):
            # only top-level definitions, not `country_event = { id = X }` triggers
            for node in (n for n in tree if n.get("kind") == "block" and n.get("key") == key):
                if not any(c.get("kind") == "kv" and c.get("key") == "id" for c in node.get("children", [])):
                    errors.append(self._err("missing_required_block", f"{key} has no id",
                                            file=path, line=content.count("\n", 0, node.get("start", 0)) + 1))
                    has_options = any(c.get("kind") == "block" and c.get("key") == "options"
                                      for c in node.get("children", []))
                    has_option = any(c.get("kind") == "block" and c.get("key") == "option"
                                     for c in node.get("children", []))
                    if not has_options and not has_option:
                        errors.append(self._err("missing_required_block", f"{key} `has no options block`",
                                                file=path, line=content.count("\n", 0, node.get("start", 0)) + 1))

    def _check_modifiers(self, path, content, errors) -> None:
        if not self.modifiers:
            return
        # State files use their own grammar (manpower, state_category,
        # buildings_max_level_factor, victory_points, owner, add_core_of ...),
        # which is not the country-modifier language this check validates.
        if path and path.replace("\\", "/").startswith("history/states/"):
            return
        known_mods = set(self.modifiers)
        scripted = set(self.index.categories().get("scripted_effects", {}))
        for m in BLOCK_LINE.finditer(content):
            name = m.group(1)
            if name in known_mods or name in ALLOW_BLOCK_KEYS:
                continue
            if name in self.effects or name in scripted or VERB_PREFIX.match(name):
                continue
            if re.match(r"^[a-z_]+_(factor|multiplier|bonus|penalty|increase|decrease)$", name):
                base = re.sub(r"_(factor|multiplier|bonus|penalty|increase|decrease)$", "", name)
                if base not in known_mods and name not in ALLOW_BLOCK_KEYS:
                    errors.append(self._err("invalid_modifier", f"modifier key not in docs: {name}",
                                            identifier=name, file=path,
                                            line=self._find_line(content, name, m.start())))

    def _collect_ids(self, path: str, content: str) -> list[tuple[str, str]]:
        from hoi4_agent._runtime.hoi4parser import parse_tree, walk

        out: list[tuple[str, str]] = []
        try:
            tree = parse_tree(content)
        except Exception:  # noqa: BLE001
            return out
        for node in (n for n in walk(tree, "focus") if n.get("kind") == "block"):
            for c in node.get("children", []):
                if c.get("kind") == "kv" and c.get("key") == "id":
                    out.append((c["value"], "focus"))
        for key in ("country_event", "news_event", "report_event"):
            for node in (n for n in tree if n.get("kind") == "block" and n.get("key") == key):
                for c in node.get("children", []):
                    if c.get("kind") == "kv" and c.get("key") == "id":
                        out.append((c["value"], "event"))
        for node in tree:
            if node.get("kind") == "block" and node.get("key"):
                if node.get("key") in ("focus", "country_event", "news_event",
                                       "report_event", "state", "idea",
                                       "equipments", "characters",
                                       "division_template", "on_actions",
                                       "on_action", "technology"):
                    continue
                child_keys = {c.get("key") for c in node.get("children", []) if c.get("kind") == "block"}
                if child_keys & {"available", "complete_effect", "visible"}:
                    out.append((node["key"], "decision"))
        return out

    @staticmethod
    def _extract_loc_keys(content: str) -> set[str]:
        return {m.group(1) for m in LOC_KEY.finditer(content)}

    # ------------------------------------------------------------ on-disk
    def validate_focus_tree(self) -> dict:
        self.refresh_workspace()
        return self._validate_system("common/national_focus", "focus")

    def validate_events(self) -> dict:
        self.refresh_workspace()
        return self._validate_system("events", "event")

    def validate_localisation(self) -> dict:
        self.refresh_workspace()
        errors: list[dict] = []
        seen: dict[str, str] = {}
        base = workspace() / "localisation" / "english"
        files = walk_text_files(base) if base.exists() else []
        for f in files:
            for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                m = LOC_KEY.match(line)
                if m:
                    key = m.group(1)
                    if key in seen:
                        errors.append(self._err("duplicate_identifier",
                                                f"duplicate localisation key {key}",
                                                identifier=key,
                                                file=f.relative_to(workspace()).as_posix(),
                                                line=i))
                    else:
                        seen[key] = f"{f.name}:{i}"
        return {"valid": not errors, "errors": errors, "warnings": [], "keys": len(seen)}

    def _validate_system(self, rel_dir: str, kind: str) -> dict:
        errors: list[dict] = []
        warnings: list[dict] = []
        base = workspace() / rel_dir
        if not base.exists():
            return {"valid": True, "errors": [], "warnings": [self._err("missing_required_block",
                    f"no {rel_dir} directory in workspace")], "files": 0}
        files = walk_text_files(base)
        all_ids: list[str] = []
        for f in files:
            rel = f.relative_to(workspace()).as_posix()
            res = self.validate_code(f.read_text(encoding="utf-8", errors="replace"),
                                     source_file=rel)
            errors.extend(res["errors"])
            all_ids.extend(i for i, k in self._collect_ids(rel, f.read_text(encoding="utf-8", errors="replace")) if k == kind)
        dupes = sorted({i for i in all_ids if all_ids.count(i) > 1})
        if dupes:
            etype = "duplicate_event_id" if kind == "event" else "duplicate_identifier"
            errors.append(self._err(etype, f"duplicate {kind} ids across workspace: {', '.join(dupes)}",
                                    identifier=dupes[0]))
        return {"valid": not errors, "errors": errors, "warnings": warnings,
                "files": len(files), "ids": len(set(all_ids))}

    def _workspace_loc_keys(self) -> set[str]:
        keys: set[str] = set()
        base = workspace() / "localisation" / "english"
        if not base.exists():
            return keys
        for f in walk_text_files(base):
            keys |= self._extract_loc_keys(f.read_text(encoding="utf-8", errors="replace"))
        return keys
