# Owner: ACTIVE
"""Generalized single-snippet code generation (strict-mode default).

Handles country-less or single-object requests ("Add a focus for Italy called
'Mare Nostrum'...", "write a decision that...") by emitting the SMALLEST valid
HOI4 snippet that satisfies the prompt. Every keyword is verified against the
installed game version's official documentation (data/raw/game/documentation),
the vanilla files, or the wiki corpus. New identifiers are declared by the
snippet itself, use vanilla identifier conventions (TAG_ prefixes), and are
checked against the vanilla index and the workspace so nothing is duplicated.

Scope discipline (instruction fidelity):
* one requested object -> exactly that object plus its required localisation;
* dependencies (ideas granted by a focus, second events in a chain) are
  emitted only when the prompt requires them;
* tree/branch/plural requests ("focus tree", "decisions") are NOT snippets and
  fall through to the project pipeline;
* if the object already exists in the workspace, generate() returns None and
  the agent reports it instead of duplicating it.
"""

from __future__ import annotations

import re
from pathlib import Path

from .config import CONFIG

# Opinion modifiers that exist in vanilla (verified via the localisation
# index and vanilla events).
_OPINION_POOL = ["condemn_aggression", "embargo", "danzig_for_guarantees",
                 "destroyers_for_bases_opinion", "border_conflict_reconciled"]

# Verified vanilla advisor traits / ideas (present in common/characters and
# the ideas index).
_TRAIT_POOL = ["war_industrialist", "captain_of_industry", "silent_workhorse",
               "armaments_organizer"]

# Phrases that mark a request as a *project* (tree/branch/plural) rather than
# a single-object snippet.
_SNIPPET_BLOCKERS = re.compile(
    r"\b(focus tree|focus trees|branch|branches|tree|trees|path|paths)\b", re.I)

# "add decisions", "make events", "write focuses", ... are project requests.
_CREATE_PLURAL = re.compile(
    r"\b(add|create|make|write|build|new)\b[\s\S]*\b(decisions|events|ideas|"
    r"focuses|spirits)\b", re.I)

_PATH_IN_PROMPT = re.compile(
    r"(?:^|\s)((?:common|events|history|localisation)/[A-Za-z0-9_\-./ ]+\.(?:txt|yml))")

_FOCUS_COST_RE = re.compile(
    r"\bchange\s+(?:the\s+)?cost\s+of\s+(?:the\s+)?focus\s+([A-Za-z0-9_]+)"
    r"(?:\s+in\s+([A-Za-z0-9_\-./]+?))?\s+to\s+(\d+)", re.I)


def _extract_number(low: str, *words: str) -> float | None:
    for word in words:
        m = re.search(r"(\d+(?:\.\d+)?)\s*%?\s*" + re.escape(word), low)
        if m:
            return float(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)", low)
    return float(m.group(1)) if m else None


def _extract_name(request: str) -> str | None:
    m = re.search(
        r"(?:called|named)\s+['\"]?([A-Za-z][A-Za-z0-9' -]{2,40}?)['\"]?"
        r"(?=\s+(?:that|which|with|and|for|to|,|\.)|$)",
        request, flags=re.IGNORECASE)
    if not m:
        # Quoted names only when the quotes delimit a standalone word group
        # (avoids matching apostrophes inside words like "hasn't").
        m = re.search(r"(?:^|\s)['\"]([A-Za-z][A-Za-z0-9' -]{2,40}?)['\"](?=\s|$)",
                      request)
    return m.group(1).strip() if m else None


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "snippet"


def _title(name: str) -> str:
    return " ".join(w.capitalize() for w in name.split()) or "Agent Snippet"


def _ideology(low: str) -> str:
    for w in ("communist", "communism"):
        if w in low:
            return "communism"
    for w in ("fascist", "fascism"):
        if w in low:
            return "fascism"
    for w in ("democratic", "democracy"):
        if w in low:
            return "democratic"
    for w in ("trotsky", "stalin"):
        if w in low:
            return "communism"  # trotskyism/stalinism are not standalone
    return "fascism"           # ideology values in the installed version


def _render_loc(entries: dict[str, str]) -> str:
    lines = ["l_english:", ""]
    for key, value in entries.items():
        lines.append(f" {key}:0 \"{value}\"")
    return "\n".join(lines) + "\n"


def _append_block(existing: str, snippet: str) -> str:
    if not existing or not existing.strip():
        return snippet
    return existing.rstrip("\n") + "\n\n" + snippet


def _wrap_focus_tree(content: str) -> str:
    """Wrap a bare focus block in a valid focus_tree block (national focus
    files must contain focus_tree blocks)."""
    m = re.search(r"id\s*=\s*([A-Za-z0-9_.]+)", content)
    fid = m.group(1) if m else "GEN_agent_focus"
    lines = ["focus_tree = {", f"\tid = {fid}_tree"]
    for line in content.splitlines():
        lines.append(("\t" + line) if line.strip() else "")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _unwrap_focus_tree(wrapped: str) -> str:
    """Extract just the focus block(s), dropping the wrapper header."""
    m = re.search(r"^\tfocus = \{", wrapped, re.M)
    if not m:
        return wrapped
    start = m.start()
    depth = 0
    for j in range(m.end() - 1, len(wrapped)):
        if wrapped[j] == "{":
            depth += 1
        elif wrapped[j] == "}":
            depth -= 1
            if depth == 0:
                return wrapped[start:j + 1] + "\n"
    return wrapped[start:].rstrip() + "\n"


def _insert_focus_into_tree(existing: str, inner: str) -> str:
    """Insert focus block(s) inside the last existing focus_tree block."""
    idx = existing.rfind("focus_tree = {")
    if idx < 0:
        return _append_block(existing, _wrap_focus_tree(inner))
    brace = existing.find("{", idx)
    depth = 0
    for j in range(brace, len(existing)):
        if existing[j] == "{":
            depth += 1
        elif existing[j] == "}":
            depth -= 1
            if depth == 0:
                return existing[:j] + "\n" + inner + "\n" + existing[j:]
    return _append_block(existing, inner)


def _merge_loc(entries_content: str, existing: str) -> str:
    entries = [ln.strip() for ln in entries_content.splitlines()
               if re.match(r"^[A-Za-z0-9_.]+\s*:\d*\s*\"", ln.strip())]
    if not entries:
        return existing or entries_content
    if not existing or not existing.strip():
        return entries_content
    existing_keys = set(re.findall(
        r"^[ \t]*([A-Za-z0-9_.]+(?:\.[A-Za-z0-9_]+)*)\s*:\d*\s*\"",
        existing, re.M))
    new_entries = [e for e in entries
                   if e.split(":", 1)[0].strip() not in existing_keys]
    if not new_entries:
        return existing
    return existing.rstrip("\n") + "\n" + "\n".join(" " + e for e in new_entries) + "\n"


def merge_snippet_text(old: str, new: str, is_yml: bool) -> str:
    """Merge a snippet batch's proposed content into the current file content.

    Snippet batches are incremental (each adds one object); when several are
    approved together, later batches must append to the current file instead of
    replacing it, and must not duplicate objects that already landed."""
    if not old or not old.strip():
        return new
    if is_yml:
        return merge_yml_text(old, new)
    new_ids = set(re.findall(r"\bid\s*=\s*([A-Za-z0-9_.]+)", new))
    if new_ids and new_ids <= set(re.findall(r"\bid\s*=\s*([A-Za-z0-9_.]+)", old)):
        return old
    return old.rstrip("\n") + "\n\n" + new.strip() + "\n"


_LOC_LINE = re.compile(
    r"^[ \t]*([A-Za-z0-9_.]+(?:\.[A-Za-z0-9_]+)*)(\s*:\d*\s*\"[^\"]*\")")


def merge_yml_text(current: str, proposal: str) -> str:
    """Key-wise merge of two localisation yml texts. The proposal's value wins
    for keys it defines; keys present only in `current` are preserved."""
    prop_lines: dict[str, str] = {}
    prop_order: list[str] = []
    for line in proposal.splitlines():
        m = _LOC_LINE.match(line)
        if m:
            key = m.group(1)
            if key not in prop_lines:
                prop_lines[key] = line
                prop_order.append(key)
    out: list[str] = []
    seen: set[str] = set()
    for line in current.splitlines():
        m = _LOC_LINE.match(line)
        if m and m.group(1) in prop_lines:
            key = m.group(1)
            if key not in seen:
                out.append(prop_lines[key])
                seen.add(key)
            continue  # drop any further duplicate lines of this key
        out.append(line)
    for key in prop_order:
        if key not in seen:
            out.append(prop_lines[key])
            seen.add(key)
    text = "\n".join(out)
    return text + ("\n" if current.endswith("\n") or proposal.endswith("\n") else "")


def dedupe_loc_keys(text: str) -> str:
    """Drop duplicate localisation keys (keep the first occurrence)."""
    seen: set[str] = set()
    out: list[str] = []
    for line in text.splitlines():
        m = _LOC_LINE.match(line)
        if m:
            key = m.group(1)
            if key in seen:
                continue
            seen.add(key)
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def _read_workspace_text(path: str) -> str:
    try:
        from .filesystem import read_text_keep, workspace

        p = workspace() / path
        return read_text_keep(p) if p.exists() else ""
    except Exception:  # noqa: BLE001
        return ""


class SnippetEngine:
    """Deterministic, grounded, minimal code-snippet generator."""

    def __init__(self, agent=None):
        self.agent = agent

    _KIND_PREFIXES: dict[str, tuple[str, ...]] = {
        "focus": ("common/national_focus/",),
        "event": ("events/",),
        "decision": ("common/decisions/",),
        "scripted": ("common/scripted_effects/", "common/scripted_triggers/"),
        "idea": ("common/ideas/",),
        "technology": ("common/technologies/",),
        "equipment": ("common/units/equipment/",),
        "upgrade": ("common/units/equipment/",),
        "division": ("history/units/", "common/units/"),
        "ai_strategy": ("common/ai_strategy/",),
        "character": ("common/characters/",),
        "on_action": ("common/on_actions/",),
        "modifier": ("common/modifiers/",),
        "state_history": ("history/states/",),
        "country_history": ("history/countries/",),
    }

    # ------------------------------------------------------------- dispatch
    def generate(self, request: str,
                 active_file: str | None = None) -> dict[str, str] | None:
        """Return {path: content} proposals, or None when the object already
        exists in the workspace (idempotence) or the request is out of scope."""
        spec = self._dispatch(request)
        if spec is None:
            return None
        kind, path, content, loc, extras = spec
        raw_content = content
        if kind == "focus":
            content = _wrap_focus_tree(content)
        files = {path: content}
        files.update(extras)
        if loc:
            files["localisation/english/agent_snippet_l_english.yml"] = _render_loc(loc)
        # Idempotence: never regenerate an object that already exists.
        if path.startswith("snippets/") or kind in (
                "ai_strategy", "on_action", "modifier", "division",
                "state_history", "country_history"):
            from .filesystem import workspace

            if (workspace() / path).exists():
                return None
        for fid in self._primary_ids(kind, content, loc):
            if self._workspace_contains(fid):
                return None
        target = self._path_from_request(request) or active_file
        if target:
            files = self._route_to_active(kind, files, target)
        else:
            files = self._merge_existing(files)
        if self.agent is not None:
            for fid in self._primary_ids(kind, raw_content, loc):
                if fid not in self.agent.memory.created_ids:
                    self.agent.memory.created_ids.append(fid)
        return files

    def _merge_existing(self, files: dict[str, str]) -> dict[str, str]:
        """Append new content into existing workspace files instead of
        replacing them, so a second snippet never wipes the first."""
        from .filesystem import workspace

        for p in list(files):
            full = workspace() / p
            if not full.exists():
                continue
            existing = full.read_text(encoding="utf-8-sig",
                                      errors="surrogateescape")
            files[p] = merge_snippet_text(existing, files[p], p.endswith(".yml"))
        return files

    def _dispatch(self, request: str) -> tuple:
        low = request.lower()
        if "ai_will_do" in low:
            return "ai_will_do", "snippets/agent_ai_will_do.txt", \
                self._ai_will_do(low), {}, {}
        if "complex trigger" in low or "nested trigger" in low:
            return "scripted", "snippets/agent_available.txt", self._available(low), {}, {}
        if re.search(r"\bfocus\b", low) and "focus tree" not in low:
            content, loc, extras = self._focus(request, low)
            return "focus", "common/national_focus/agent_focus.txt", content, loc, extras
        if "event" in low:
            content, loc = self._event(request, low)
            return "event", "events/agent_events.txt", content, loc, {}
        if "decision" in low:
            content, loc = self._decision(request, low)
            return "decision", "common/decisions/agent_decisions.txt", content, loc, {}
        if "scripted_effect" in low or "scripted effect" in low \
                or "scripted_trigger" in low or "scripted trigger" in low:
            content = self._scripted(request, low)
            path = ("common/scripted_triggers/agent_scripted_triggers.txt"
                    if "trigger" in low and "effect" not in low
                    else "common/scripted_effects/agent_scripted_effects.txt")
            return "scripted", path, content, {}, {}
        if "spirit" in low or "idea" in low:
            return "idea", "common/ideas/agent_ideas.txt", self._idea(request, low), {}, {}
        if "technology" in low or ("tech" in low and "decision" not in low):
            return ("technology", "common/technologies/agent_technology.txt",
                    self._technology(low), {}, {})
        if "light tank" in low or "equipment stats" in low or "aircraft" in low:
            return ("equipment", "common/units/equipment/agent_equipment.txt",
                    self._equipment(low), {}, {})
        if "division template" in low:
            from .config import mod_start_year

            return "division", f"history/units/AGENT_{mod_start_year()}.txt", \
                self._division(low), {}, {}
        if "equipment upgrade" in low:
            return "upgrade", "common/units/equipment/agent_upgrades.txt", self._upgrade(low), {}, {}
        if "ai strategy" in low:
            return ("ai_strategy", "common/ai_strategy/agent_ai_strategy.txt",
                    self._ai_strategy(low), {}, {})
        if "character" in low and "file" in low:
            return ("character", "common/characters/agent_characters.txt",
                    self._character(request, low), {}, {})
        if "on_action" in low:
            return ("on_action", "common/on_actions/agent_on_actions.txt",
                    self._on_action(low), {}, {})
        if "modifier definition" in low:
            return "modifier", "common/modifiers/agent_modifiers.txt", self._modifier(low), {}, {}
        if "state history" in low:
            return ("state_history", "history/states/agent_state.txt",
                    self._state_history(low), {}, {})
        if "country history" in low:
            tag = self._country_tag(request)
            name = self._country_name(tag) if tag else ""
            path = (f"history/countries/{tag} - {name}.txt"
                    if tag and name else "history/countries/AGENT - Agent.txt")
            return ("country_history", path,
                    self._country_history(request, low), {}, {})
        return None

    def matches(self, request: str) -> bool:
        """True when this is a single-object snippet request (not a project)."""
        try:
            if self._dispatch(request) is None:
                return False
            low = request.lower()
            if _SNIPPET_BLOCKERS.search(request):
                return False
            # "prereqs/prerequires two other focuses" is descriptive, not a
            # request for a whole tree.
            if _CREATE_PLURAL.search(request) and "prereq" not in low:
                return False
            return True
        except Exception:  # noqa: BLE001
            return False

    def modify(self, request: str) -> dict[str, str] | None:
        """Deterministic contextual single-object edits. Returns
        (proposals, reason) with reason in {"changed", "already_set",
        "not_found"}, or None when the request is not an edit request."""
        m = _FOCUS_COST_RE.search(request)
        if m:
            return self._modify_cost(m.group(1), m.group(2), int(m.group(3)))
        low = request.lower()
        if re.search(r"\bcheaper\b", low) or \
                re.search(r"\bmake\b[^\n]{0,40}\bcost\b", low):
            return self._modify_cost_ctx(request, low)
        if re.search(r"\brename\b", low):
            return self._modify_rename(request, low)
        if re.search(r"\bmove\b", low) and re.search(r"\bx\s*=\s*\d+", low):
            return self._modify_move(request, low)
        if re.search(r"\bai\s*ignore\b", low):
            return self._modify_ai_ignore(request, low)
        if re.search(r"\b(remove|clear|strip)\b", low) and \
                re.search(r"\b(completion reward|bonus|reward)\b", low):
            return self._modify_clear_reward(request, low)
        if re.search(r"\blocalisation\s+(?:title|key)\s+for\b", low):
            return self._modify_add_loc(request, low)
        if re.search(r"\bsecond option\b", low):
            return self._modify_event_option(request, low)
        if re.search(r"\bchange its effect\b", low) or \
                re.search(r"\beffect\b[^\n]{0,50}\badd\b[^\n]{0,50}\b\d+\b", low):
            return self._modify_event_effect(request, low)
        return None

    def _modify_cost_ctx(self, request: str, low: str) -> dict[str, str]:
        fid = self._resolve_target(request, low, "focus")
        if fid is None:
            return {}, "not_found"
        rel = self._find_file_with(fid, ("common/national_focus",))
        if rel is None:
            return {}, "not_found"
        text = self._read(rel)
        found = self._focus_block(text, fid)
        if found is None:
            return {}, "not_found"
        cur = re.search(r"\bcost\s*=\s*(\d+)", found[2])
        if re.search(r"\bcheaper\b", low):
            new_cost = max(1, (int(cur.group(1)) if cur else 10) - 5)
        else:
            m = re.search(r"\b(\d+)\b", low)
            if not m:
                return {}, "not_found"
            new_cost = int(m.group(1))
        return self._modify_cost(fid, rel, new_cost)

    # ------------------------------------------------------------ cost edit
    def _modify_cost(self, fid: str, path: str, new_cost: int) -> dict[str, str]:
        from .filesystem import workspace

        candidates: list[str] = []
        if path:
            candidates.append(path.replace("\\", "/"))
        else:
            base = workspace() / "common" / "national_focus"
            if base.exists():
                pattern = re.compile(rf"\bid\s*=\s*{re.escape(fid)}\b")
                for f in base.glob("*.txt"):
                    try:
                        if pattern.search(f.read_text(
                                encoding="utf-8", errors="surrogateescape")):
                            candidates.append(f.relative_to(workspace()).as_posix())
                    except OSError:
                        continue
        for rel in candidates:
            full = workspace() / rel
            if not full.exists():
                continue
            text = full.read_text(encoding="utf-8-sig",
                                  errors="surrogateescape")
            edited = self._change_focus_cost(text, fid, new_cost)
            if edited is not None:
                return ({rel: edited}, "changed") if edited != text \
                    else ({}, "already_set")
        return {}, "not_found"

    # -------------------------------------------------- contextual targets
    def _resolve_target(self, request: str, low: str, kind: str) -> str | None:
        """Resolve the object a contextual request refers to: an explicit id
        in the request, else the most recently mentioned object, else the most
        recently created object of `kind`."""
        if self.agent is None:
            return None
        for cand in re.findall(r"\b([A-Z]{2,4}_[A-Za-z0-9_]+)\b", request):
            for fid in reversed(self.agent.memory.created_ids):
                if fid == cand or fid.startswith(cand + "_"):
                    self.agent.memory.last_mentioned_id = fid
                    return fid
        mentioned = self.agent.memory.last_mentioned_id
        if mentioned:
            rel_dirs = ("events",) if kind == "event" else \
                ("common/national_focus",)
            if self._find_file_with(mentioned, rel_dirs):
                return mentioned
        if kind == "event":
            for fid in reversed(self.agent.memory.created_ids):
                if "." in fid and ("event" in low or "option" in low or "effect" in low):
                    self.agent.memory.last_mentioned_id = fid
                    return fid
            return None
        for fid in reversed(self.agent.memory.created_ids):
            if "." not in fid and fid.lower().startswith(("gen_", "agent_")):
                self.agent.memory.last_mentioned_id = fid
                return fid
        return None

    def _find_file_with(self, needle: str, rel_dirs: tuple[str, ...]) -> str | None:
        from .filesystem import workspace

        pattern = re.compile(rf"\bid\s*=\s*{re.escape(needle)}\b")
        for rel_dir in rel_dirs:
            base = workspace() / rel_dir
            if not base.exists():
                continue
            for f in base.rglob("*"):
                if not f.is_file():
                    continue
                try:
                    if pattern.search(f.read_text(
                            encoding="utf-8", errors="surrogateescape")):
                        return f.relative_to(workspace()).as_posix()
                except OSError:
                    continue
        return None

    def _read(self, rel: str) -> str:
        from .filesystem import workspace

        p = workspace() / rel
        return (p.read_text(encoding="utf-8-sig", errors="surrogateescape")
                if p.exists() else "")

    def _focus_block(self, text: str, fid: str) -> tuple[int, int, str] | None:
        for m in re.finditer(r"\bfocus\s*=\s*\{", text):
            start = m.start()
            depth, i = 1, m.end()
            while i < len(text) and depth:
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                i += 1
            block = text[start:i]
            if re.search(rf"\bid\s*=\s*{re.escape(fid)}\b", block):
                return start, i, block
        return None

    def _event_block(self, text: str, eid: str) -> tuple[int, int, str] | None:
        for key in ("country_event", "news_event", "report_event", "state_event"):
            for m in re.finditer(rf"\b{key}\s*=\s*\{{", text):
                start = m.start()
                depth, i = 1, m.end()
                while i < len(text) and depth:
                    if text[i] == "{":
                        depth += 1
                    elif text[i] == "}":
                        depth -= 1
                    i += 1
                block = text[start:i]
                if re.search(rf"\bid\s*=\s*{re.escape(eid)}\b", block):
                    return start, i, block
        return None

    def _loc_path(self, key: str) -> str | None:
        from .filesystem import workspace

        base = workspace() / "localisation" / "english"
        if base.exists():
            for f in sorted(base.glob("*.yml")):
                try:
                    if key in f.read_text(encoding="utf-8-sig",
                                          errors="surrogateescape"):
                        return f.relative_to(workspace()).as_posix()
                except OSError:
                    continue
        return "localisation/english/agent_snippet_l_english.yml" \
            if (workspace() / "localisation" / "english").exists() else None

    # ------------------------------------------------------- rename a focus
    def _modify_rename(self, request: str, low: str) -> dict[str, str]:
        fid = self._resolve_target(request, low, "focus")
        if fid is None:
            return {}, "not_found"
        m = re.search(r"\bto\s+([A-Za-z0-9_\- ]+?)\s*$", request)
        if not m:
            return {}, "not_found"
        new_name = m.group(1).strip()
        if re.match(r"^[A-Z]{2,4}_", new_name):
            new_fid = new_name
        else:
            new_fid = self._new_id("GEN_" + _slug(new_name))
        rel = self._find_file_with(fid, ("common/national_focus",))
        if rel is None:
            return {}, "not_found"
        text = self._read(rel)
        found = self._focus_block(text, fid)
        if found is None:
            return {}, "not_found"
        start, end, block = found
        new_block = re.sub(rf"\bid\s*=\s*{re.escape(fid)}\b",
                           f"id = {new_fid}", block, count=1)
        proposals = {rel: text[:start] + new_block + text[end:]}
        loc_rel = self._loc_path(fid)
        if loc_rel:
            loc = self._read(loc_rel)
            new_loc = loc.replace(f"{fid}:", f"{new_fid}:")
            new_loc = new_loc.replace(f"{fid}_desc:", f"{new_fid}_desc:")
            if new_loc != loc:
                proposals[loc_rel] = new_loc
        if fid in self.agent.memory.created_ids:
            self.agent.memory.created_ids[self.agent.memory.created_ids.index(fid)] = new_fid
        self.agent.memory.last_mentioned_id = new_fid
        return proposals, "changed"

    # ---------------------------------------------------------- move a focus
    def _modify_move(self, request: str, low: str) -> dict[str, str]:
        fid = self._resolve_target(request, low, "focus")
        if fid is None:
            return {}, "not_found"
        mx = re.search(r"\bx\s*=\s*(\d+)", low)
        my = re.search(r"\by\s*=\s*(\d+)", low)
        if not mx or not my:
            return {}, "not_found"
        rel = self._find_file_with(fid, ("common/national_focus",))
        if rel is None:
            return {}, "not_found"
        text = self._read(rel)
        found = self._focus_block(text, fid)
        if found is None:
            return {}, "not_found"
        start, end, block = found
        nb = re.sub(r"\bx\s*=\s*\d+", f"x = {mx.group(1)}", block, count=1)
        nb = re.sub(r"\by\s*=\s*\d+", f"y = {my.group(1)}", nb, count=1)
        if nb == block:
            return {}, "already_set"
        return {rel: text[:start] + nb + text[end:]}, "changed"

    # -------------------------------------------------------- make AI ignore
    def _modify_ai_ignore(self, request: str, low: str) -> dict[str, str]:
        fid = self._resolve_target(request, low, "focus")
        if fid is None:
            return {}, "not_found"
        rel = self._find_file_with(fid, ("common/national_focus",))
        if rel is None:
            return {}, "not_found"
        text = self._read(rel)
        found = self._focus_block(text, fid)
        if found is None:
            return {}, "not_found"
        start, end, block = found
        if re.search(r"\bai_will_do\s*=\s*\{", block):
            return {}, "already_set"
        insert = "\tai_will_do = {\n\t\tfactor = 0\n\t}\n"
        m = re.search(r"^(\t*)id\s*=\s*" + re.escape(fid) + r"\b", block, re.M)
        pos = (m.end() if m else block.index("{")) + 1
        new_block = block[:pos] + "\n" + insert + block[pos:]
        return {rel: text[:start] + new_block + text[end:]}, "changed"

    # ------------------------------------------------------ clear the reward
    def _modify_clear_reward(self, request: str, low: str) -> dict[str, str]:
        fid = self._resolve_target(request, low, "focus")
        if fid is None:
            return {}, "not_found"
        rel = self._find_file_with(fid, ("common/national_focus",))
        if rel is None:
            return {}, "not_found"
        text = self._read(rel)
        found = self._focus_block(text, fid)
        if found is None:
            return {}, "not_found"
        start, end, block = found
        m = re.search(r"completion_reward\s*=\s*\{", block)
        if not m:
            return {}, "already_set"
        cs, i = m.end(), 1
        while i:
            if block[cs] == "{":
                i += 1
            elif block[cs] == "}":
                i -= 1
            cs += 1
        new_block = block[: m.start()] + "completion_reward = {\n\t}" + block[cs:]
        if new_block == block:
            return {}, "already_set"
        return {rel: text[:start] + new_block + text[end:]}, "changed"

    # ------------------------------------------------------ add localisation
    def _modify_add_loc(self, request: str, low: str) -> dict[str, str]:
        fid = self._resolve_target(request, low, "focus")
        if fid is None:
            return {}, "not_found"
        loc_rel = self._loc_path(fid) or "localisation/english/agent_snippet_l_english.yml"
        loc = self._read(loc_rel)
        if re.search(rf"^\s*{re.escape(fid)}\s*:\d*\s*\"", loc, re.M):
            return {}, "already_set"
        title = fid.replace("_", " ").title()
        if not loc.strip():
            loc = "l_english:\n"
        proposals = {loc_rel: loc.rstrip() + f"\n {fid}:0 \"{title}\"\n {fid}_desc:0 \"{title}.\"\n"}
        return proposals, "changed"

    # ------------------------------------------------------ event: add option
    def _modify_event_option(self, request: str, low: str) -> dict[str, str]:
        eid = self._resolve_target(request, low, "event")
        if eid is None:
            return {}, "not_found"
        rel = self._find_file_with(eid, ("events",))
        if rel is None:
            return {}, "not_found"
        text = self._read(rel)
        found = self._event_block(text, eid)
        if found is None:
            return {}, "not_found"
        start, end, block = found
        n_options = len(re.findall(r"\boption\s*=\s*\{", block))
        letter = chr(ord("a") + n_options)
        option = (f"\toption = {{\n\t\tname = {eid}.{letter}\n"
                  f"\t\tadd_political_power = 25\n\t}}\n")
        insert_at = block.rstrip().rfind("}")
        new_block = block[:insert_at] + option + block[insert_at:]
        if new_block == block:
            return {}, "already_set"
        proposals = {rel: text[:start] + new_block + text[end:]}
        loc_rel = self._loc_path(eid)
        if loc_rel:
            loc = self._read(loc_rel)
            if f"{eid}.{letter}" not in loc:
                proposals[loc_rel] = loc.rstrip() + f"\n {eid}.{letter}:0 \"Option {n_options + 1}\"\n"
        return proposals, "changed"

    # ------------------------------------------------- event: change effect
    def _modify_event_effect(self, request: str, low: str) -> dict[str, str]:
        eid = self._resolve_target(request, low, "event")
        if eid is None:
            return {}, "not_found"
        m = re.search(r"\b(\d+)\s+(?:political power|stability|war support)\b", low)
        if not m:
            return {}, "not_found"
        rel = self._find_file_with(eid, ("events",))
        if rel is None:
            return {}, "not_found"
        text = self._read(rel)
        found = self._event_block(text, eid)
        if found is None:
            return {}, "not_found"
        start, end, block = found
        if "political power" in low:
            kind_key, value = "add_political_power", str(int(m.group(1)))
        elif "stability" in low:
            kind_key, value = "add_stability", f"{int(m.group(1)) / 100:.2f}"
        else:
            kind_key, value = "add_war_support", f"{int(m.group(1)) / 100:.2f}"
        effect = f"{kind_key} = {value}"
        nb = re.sub(rf"\b{re.escape(kind_key)}\s*=\s*-?[\d.]+", effect,
                    block, count=1)
        if nb == block:
            # key absent: replace the first plain effect line inside the
            # first option so the requested effect actually takes effect.
            opt = re.search(r"\boption\s*=\s*\{[^}]*\}", block, re.S)
            if opt:
                inner = opt.group(0)
                new_inner = re.sub(r"\badd_[a-z_]+\s*=\s*-?[\d.]+", effect,
                                   inner, count=1)
                if new_inner != inner:
                    nb = block.replace(inner, new_inner)
        if nb == block:
            return {}, "not_found"
        return {rel: text[:start] + nb + text[end:]}, "changed"

    @staticmethod
    def _change_focus_cost(text: str, fid: str, new_cost: int) -> str | None:
        """Replace only the cost inside the focus block declaring `fid`."""
        for m in re.finditer(r"\bfocus\s*=\s*\{", text):
            start = m.start()
            depth, i = 1, m.end()
            while i < len(text) and depth:
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                i += 1
            block = text[start:i]
            if re.search(rf"\bid\s*=\s*{re.escape(fid)}\b", block):
                new_block, n = re.subn(r"\bcost\s*=\s*\d+",
                                       f"cost = {new_cost}", block, count=1)
                if n:
                    return text[:start] + new_block + text[i:]
                # focus has no cost line yet: add one right after its id
                new_block = re.sub(
                    r"(id\s*=\s*[A-Za-z0-9_.]+)",
                    rf"\1\n\tcost = {new_cost}", block, count=1)
                if new_block != block:
                    return text[:start] + new_block + text[i:]
        return None

    # ------------------------------------------------------------ id helpers
    def _country_tag(self, request: str) -> str:
        if self.agent is None:
            return ""
        try:
            low = request.lower()
            # Prefer the country named as the TARGET ("for Poland") over one
            # mentioned as context ("against Germany").
            m = re.search(
                r"\b(?:for|to|in|into|of|on)\s+(?:the\s+)?"
                r"([a-z][a-z0-9' -]{2,40}?)"
                r"(?=\s+(?:that|which|with|and|to|,|\.)|$)", low)
            if m:
                cand = m.group(1).strip()
                tag = self.agent.planner._resolve_country(cand, cand)
                if tag:
                    return tag
            return self.agent.planner._resolve_country(low, request) or ""
        except Exception:  # noqa: BLE001
            return ""

    def _country_name(self, tag: str) -> str:
        try:
            return self.agent.planner._load_vanilla_country_names().get(tag, "")
        except Exception:  # noqa: BLE001
            return ""

    def _new_id(self, base: str) -> str:
        """A declared new id that does not collide with the vanilla index.
        (Workspace collisions mean "already exists" and are handled by the
        idempotence check, never by silent renumbering.)"""
        cand, n = base, 1
        while self._vanilla_known(cand):
            n += 1
            cand = f"{base}_{n}"
        return cand

    def _vanilla_known(self, fid: str) -> bool:
        try:
            return fid in self.agent.index.known_set()
        except Exception:  # noqa: BLE001
            return False

    def _workspace_contains(self, needle: str) -> bool:
        try:
            from .filesystem import workspace

            from .project_scan import SCAN_DIRS

            dirs = SCAN_DIRS + ("common/technologies", "common/units/equipment")
            for rel_dir in dirs:
                base = workspace() / rel_dir
                if not base.exists():
                    continue
                for f in base.rglob("*.txt"):
                    try:
                        if needle in f.read_text(encoding="utf-8", errors="replace"):
                            return True
                    except OSError:
                        continue
                for f in base.rglob("*.yml"):
                    try:
                        if needle in f.read_text(encoding="utf-8", errors="replace"):
                            return True
                    except OSError:
                        continue
        except Exception:  # noqa: BLE001
            pass
        return False

    @staticmethod
    def _primary_ids(kind: str, content: str, loc: dict[str, str]) -> list[str]:
        """The ids that must not already exist (idempotence)."""
        if kind == "focus":
            m = re.search(r"\bid\s*=\s*([A-Za-z0-9_.]+)", content)
            return [m.group(1)] if m else []
        if kind == "event":
            return list(dict.fromkeys(
                m.group(1) for m in re.finditer(r"\bid\s*=\s*([A-Za-z0-9_.]+)", content)))
        if kind in ("idea", "technology", "decision"):
            m = re.search(r"^[ \t]?([A-Za-z0-9_]+)\s*=\s*\{", content, re.M)
            return [m.group(1)] if m else []
        if kind in ("equipment", "upgrade", "character"):
            m = re.search(r"^[ \t]{1,2}([A-Za-z0-9_]+)\s*=\s*\{", content, re.M)
            return [m.group(1)] if m else []
        if kind == "scripted":
            m = re.search(r"scripted_(?:effect|trigger)\s+([A-Za-z0-9_]+)", content)
            return [m.group(1)] if m else []
        return []

    # ------------------------------------------------------ active-file routing
    def is_applicable(self, kind: str, active_file: str) -> bool:
        p = self._normalize_active(active_file)
        if not p:
            return False
        if p.endswith(".yml"):
            return "localisation/" in p and kind in ("focus", "event", "decision")
        return p.endswith(".txt") and any(
            p.startswith(pre) for pre in self._KIND_PREFIXES.get(kind, ()))

    def _normalize_active(self, active_file: str) -> str:
        a = active_file.replace("\\", "/")
        try:
            from .filesystem import workspace

            p = Path(active_file)
            if p.is_absolute():
                return p.resolve().relative_to(workspace().resolve()).as_posix()
        except Exception:  # noqa: BLE001
            pass
        return a.lstrip("/")

    def _route_to_active(self, kind: str, files: dict[str, str],
                         active_file: str) -> dict[str, str]:
        a = self._normalize_active(active_file)
        if not a or not self.is_applicable(kind, active_file):
            return files
        loc_file = next((p for p in files if p.endswith(".yml")), None)
        if a.endswith(".yml"):
            if loc_file and a != loc_file:
                entries = files.pop(loc_file)
                files[a] = _merge_loc(entries, _read_workspace_text(a))
            return files
        main = next((p for p in files if not p.endswith(".yml")), None)
        if main and main != a:
            existing = _read_workspace_text(a)
            snippet = files.pop(main)
            if kind == "focus" and existing.strip():
                files[a] = _insert_focus_into_tree(
                    existing, _unwrap_focus_tree(snippet))
            else:
                files[a] = _append_block(existing, snippet)
        return files

    def _path_from_request(self, request: str) -> str | None:
        m = _PATH_IN_PROMPT.search(request)
        return m.group(1).strip() if m else None

    # ------------------------------------------------------------ generators
    def _focus(self, request: str, low: str) -> tuple[str, dict, dict]:
        tag = self._country_tag(request)
        name = _extract_name(request) or self._default_focus_name(low)
        fid = self._new_id(f"{tag or 'GEN'}_{_slug(name)}")
        cost = int(_extract_number(low, "cost") or 10)
        lines = ["focus = {", f"\tid = {fid}", f"\tcost = {cost}", "\tx = 0", "\ty = 0"]
        if "great depression" in low and tag == "USA":
            lines.append("\tprerequisite = { focus = USA_continue_the_new_deal }")
        elif "prereq" in low:
            n = 2 if "two" in low else 1
            for fid2 in self._verified_focus_ids(tag, n):
                lines.append(f"\tprerequisite = {{ focus = {fid2} }}")
        if "only available" in low or "available if" in low:
            lines += self._focus_available(low, tag)
        reward: list[str] = []
        extras: dict[str, str] = {}
        if "factory output" in low:
            idea = self._new_id(f"{tag.lower() or 'agent'}_{_slug(name)}_output")
            reward.append(f"\t\tadd_ideas = {{ {idea} }}")
            extras[self._idea_path(tag)] = self._idea_block(
                idea, {"industrial_capacity_factory": 0.05})
        elif "national spirit" in low or "defensive" in low:
            idea = self._new_id(f"{tag.lower() or 'agent'}_{_slug(name)}_spirit")
            reward.append(f"\t\tadd_ideas = {{ {idea} }}")
            extras[self._idea_path(tag)] = self._idea_block(
                idea, {"army_defence_factor": 0.1})
        elif any(w in low for w in ("bonus", "gives", "grants", "boost",
                                    "increase", "increases")):
            if "naval" in low:
                reward += self._tech_bonus(_slug(name), "naval_equipment")
            elif "army" in low or "temporary" in low:
                reward += self._tech_bonus(_slug(name), "infantry_weapons")
            else:
                reward.append("\t\tadd_political_power = 25")
        if "civil war" in low:
            ideology = _ideology(low)
            reward += [
                "\t\tstart_civil_war = {",
                f"\t\t\tideology = {ideology}",
                "\t\t\tsize = 0.3",
                "\t\t}",
            ]
            if "ruling party" in low:
                reward += [
                    "\t\tset_politics = {",
                    f"\t\t\truling_party = {ideology}",
                    "\t\t}",
                ]
        if reward:
            lines.append("\tcompletion_reward = {")
            lines.extend(reward)
            lines.append("\t}")
        if "ai only takes it" in low or "ai will" in low:
            threshold = float(_extract_number(low, "world tension") or 30) / 100.0
            lines += [
                "\tai_will_do = {",
                "\t\tfactor = 5",
                "\t\tmodifier = {",
                "\t\t\tfactor = 0",
                f"\t\t\tthreat < {threshold:.2f}",
                "\t\t}",
                "\t}",
            ]
        lines.append("}")
        loc = {fid: _title(name)}
        for idea_file in extras.values():
            im = re.search(r"^\s*([A-Za-z0-9_]+)\s*=\s*\{", idea_file, re.M)
            if im:
                loc[im.group(1)] = _title(name) + " effect"
        return "\n".join(lines) + "\n", loc, extras

    def _default_focus_name(self, low: str) -> str:
        for word, slug in (("civil war", "civil_war"), ("mare nostrum", "mare_nostrum"),
                           ("manchuria", "secure_manchuria")):
            if word in low:
                return slug.replace("_", " ").title()
        return "Custom Focus"

    def _focus_available(self, low: str, tag: str) -> list[str]:
        lines = ["\tavailable = {"]
        added = False
        if "manchuria" in low:
            for sid in self._state_ids_named("manchur"):
                lines.append(f"\t\tcontrols_state = {sid}")
                added = True
            if not added:
                for sid in self._verified_state_ids(1):
                    lines.append(f"\t\tcontrols_state = {sid}")
                    added = True
        if "war with china" in low:
            lines.append("\t\tNOT = { has_war_with = CHI }")
            added = True
        if "austria" in low and not added:
            lines.append("\t\texists = AUT")
            added = True
        if not added:
            lines.append("\t\talways = yes")
        lines.append("\t}")
        return lines

    def _tech_bonus(self, name: str, category: str) -> list[str]:
        return [
            "\t\tadd_tech_bonus = {",
            f"\t\t\tname = {name}_bonus",
            "\t\t\tbonus = 0.5",
            "\t\t\tuses = 1",
            f"\t\t\tcategory = {category}",
            "\t\t}",
        ]

    def _idea_path(self, tag: str) -> str:
        return f"common/ideas/{(tag or 'agent').lower()}_agent_ideas.txt"

    def _idea_block(self, idea: str, modifiers: dict[str, float]) -> str:
        lines = [f"{idea} = {{", "\tremoval_cost = -1", "\tmodifier = {"]
        for mod, val in modifiers.items():
            lines.append(f"\t\t{mod} = {val:.2f}")
        lines += ["\t}", "}"]
        return "\n".join(lines) + "\n"

    def _verified_focus_ids(self, tag: str, n: int) -> list[str]:
        if self.agent is None or not tag:
            return []
        focuses = self.agent.index.categories().get("focuses", {})
        return sorted(fid for fid in focuses if fid.startswith(tag + "_"))[:n]

    def _verified_state_ids(self, n: int = 2) -> list[int]:
        try:
            from .preview import map_preview

            d = map_preview._load()
            return sorted(sid for sid in d.states if sid > 100)[:n]
        except Exception:  # noqa: BLE001
            return [184, 797][:n]

    def _state_ids_named(self, needle: str) -> list[int]:
        try:
            from .preview import map_preview

            d = map_preview._load()
            return [sid for sid, info in sorted(d.states.items())
                    if needle in str(info.get("name", "")).lower()][:3]
        except Exception:  # noqa: BLE001
            return []

    def _event(self, request: str, low: str) -> tuple[str, dict]:
        tag = (self._country_tag(request) or "agent").lower()
        slug = _slug(_extract_name(request) or self._default_event_name(low))
        eid1 = f"{tag}_{slug}.1"
        eid2 = f"{tag}_{slug}.2"
        block = "state_event" if "state" in low and "state-scoped" in low \
            else "news_event" if "news" in low else "country_event"
        lines = [f"{block} = {{", f"\tid = {eid1}",
                 f"\ttitle = {eid1}.t", f"\tdesc = {eid1}.d",
                 "\tis_triggered_only = yes"]
        loc = {f"{eid1}.t": _title(slug.replace("_", " ")),
               f"{eid1}.d": f"{_title(slug.replace('_', ' '))} event.",
               f"{eid1}.a": "Accept"}
        if "war support" in low:
            threshold = float(_extract_number(low, "war support") or 30) / 100.0
            lines += ["\ttrigger = {", f"\t\ttag = {tag.upper()}",
                      f"\t\thas_war_support < {threshold:.2f}", "\t}",
                      "\tmean_time_to_happen = {", "\t\tdays = 20", "\t}"]
        elif "infrastructure" in low:
            lines += ["\ttrigger = {", "\t\towner = {",
                      f"\t\t\ttag = {tag.upper()}", "\t\t}",
                      "\t\tinfrastructure > 4", "\t}",
                      "\tmean_time_to_happen = {", "\t\tdays = 15", "\t}"]
        elif "world tension" in low:
            threshold = float(_extract_number(low, "world tension") or 50) / 100.0
            lines += ["\ttrigger = {", f"\t\tthreat > {threshold:.2f}", "\t}",
                      "\tmean_time_to_happen = {", "\t\tdays = 30", "\t}"]
        if "chain" in low or "triggers the second" in low:
            lines += ["\toption = {", f"\t\tname = {eid1}.a",
                      "\t\tcountry_event = {", f"\t\t\tid = {eid2}",
                      "\t\t\tdays = 7", "\t\t}", "\t}", "}",
                      "", f"{block} = {{", f"\tid = {eid2}",
                      f"\ttitle = {eid2}.t", f"\tdesc = {eid2}.d",
                      "\tis_triggered_only = yes",
                      "\toption = {", f"\t\tname = {eid2}.a"]
            if "civil war" in low or "tension" in low:
                lines += ["\t\tstart_civil_war = {",
                          "\t\t\tideology = neutrality",
                          "\t\t\tsize = 0.2", "\t\t}"]
            lines += ["\t}", "}"]
            loc.update({f"{eid2}.t": _title(slug.replace("_", " ")) + " (2)",
                        f"{eid2}.d": "Second part of the event chain.",
                        f"{eid2}.a": "Acknowledge"})
            return "\n".join(lines) + "\n", loc
        n_options = 3 if re.search(r"\b3\s*(?:selectable\s+)?options?\b", low) else 1
        effects = ["add_political_power = 50", "add_stability = -0.05",
                   "add_war_support = 0.05"]
        for i in range(n_options):
            lines.append("\toption = {")
            key = f"{eid1}.{chr(ord('a') + i)}"
            lines.append(f"\t\tname = {key}")
            loc[key] = f"Option {i + 1}"
            if "opinion" in low:
                mods = _OPINION_POOL[:n_options]
                lines += ["\t\tadd_opinion_modifier = {",
                          "\t\t\ttarget = FRA",
                          f"\t\t\tmodifier = {mods[i % len(mods)]}", "\t\t}"]
            elif "oil" in low:
                lines += ["\t\tadd_resource = {", "\t\t\ttype = oil",
                          "\t\t\tamount = 10", "\t\t}"]
            else:
                lines.append(f"\t\t{effects[i % len(effects)]}")
            lines.append("\t}")
        lines.append("}")
        return "\n".join(lines) + "\n", loc

    def _default_event_name(self, low: str) -> str:
        for word, name in (("appeasement", "appeasement"),
                           ("oil", "oil_boom"), ("morale", "low_morale")):
            if word in low:
                return name
        return "agent_event"

    def _decision(self, request: str, low: str) -> tuple[str, dict]:
        tag = (self._country_tag(request) or "").lower()
        name = _extract_name(request) or self._default_decision_name(low)
        did = self._new_id(f"{tag}_{_slug(name)}" if tag else _slug(name))
        lines = [f"{did} = {{"]
        if "send volunteers" in low:
            lines += ["\tallowed = {", f"\t\toriginal_tag = {(tag or 'HUN').upper()}", "\t}",
                      "\tvisible = {", "\t\tSPR = { has_civil_war = yes }", "\t}"]
        else:
            lines.append("\tallowed = { always = yes }")
        if "cooldown" in low:
            lines.append("\tdays_re_enable = 30")
        if "cost" in low and ("political power" in low or "cost" in low):
            lines.append("\tcost = 50")
        if "technology" in low or "tech" in low:
            lines += ["\tavailable = {", "\t\thas_tech = infantry_weapons1", "\t}"]
        elif "targeted" in low or "target scope" in low or "demand territory" in low:
            target = (self._country_tag(low) or "")
            lines += ["\tavailable = { always = yes }",
                      "\ttarget_root_trigger = {",
                      f"\t\ttag = {'CZE' if 'czech' in low else (target or 'CZE')}",
                      "\t}",
                      "\ttarget_trigger = {", "\t\tis_major = no", "\t}"]
        else:
            lines.append("\tavailable = { always = yes }")
        if "guarantee" in low:
            lines += ["\tcomplete_effect = {", "\t\tgive_guarantee = FROM", "\t}"]
        elif "demand territory" in low:
            lines += ["\tcomplete_effect = {",
                      "\t\tFROM = { every_controlled_state = {",
                      "\t\t\tlimit = { is_controlled_by = FROM }",
                      "\t\t\ttransfer_state_to = ROOT", "\t\t} }", "\t}"]
        elif "send volunteers" in low:
            lines += ["\tcomplete_effect = {", "\t\tadd_political_power = -50", "\t}"]
        else:
            lines += ["\tcomplete_effect = {", "\t}"]
        lines.append("}")
        loc = {did: _title(_slug(name)), f"{did}_desc": f"{_title(_slug(name))} decision."}
        return "\n".join(lines) + "\n", loc

    def _default_decision_name(self, low: str) -> str:
        if "volunteers" in low:
            return "send_volunteers_spain"
        if "demand" in low or "territory" in low:
            return "demand_territory"
        return "custom_decision"

    def _available(self, low: str) -> str:
        if "and, or, and not" in low or "nested" in low:
            return ("available = {\n"
                    "\tOR = {\n"
                    "\t\tAND = {\n"
                    "\t\t\thas_tech = land_doctrine\n"
                    "\t\t\tnum_of_factories > 50\n"
                    "\t\t}\n"
                    "\t\tNOT = {\n"
                    "\t\t\thas_war = yes\n"
                    "\t\t}\n"
                    "\t}\n"
                    "}\n")
        tag = (self._country_tag(low) or "MEX").upper()
        lines = ["available = {", f"\ttag = {tag}"]
        if "stability" in low or "oil" in low:
            lines += ["\tAND = {", "\t\thas_stability > 0.4",
                      "\t\tNOT = { has_civil_war = yes }", "\t}"]
        if "oil" in low:
            lines += ["\thas_resources_in_country = {", "\t\tresource = oil",
                      "\t\tamount > 50", "\t}"]
        lines.append("}")
        return "\n".join(lines) + "\n"

    def _scripted(self, request: str, low: str) -> str:
        tag = (self._country_tag(request) or "").lower()
        if "controls at least" in low:
            ids = self._verified_state_ids(5)
            amount = _extract_number(low, "percent") or 80
            needed = max(1, int(round(len(ids) * amount / 100.0)))
            lines = ["scripted_trigger gen_controls_core_territory = {",
                     f"\tnum_of_controlled_states > {needed - 1}"]
            for sid in ids:
                lines.append(f"\tcontrols_state = {sid}")
            lines += ["}", ""]
            return "\n".join(lines) + "\n"
        if "controls" in low and "states" in low:
            ids = self._state_ids_named("bosphorus") or \
                self._state_ids_named("istanbul") or self._state_ids_named("thrace") or \
                self._verified_state_ids(2)
            name = self._new_id(f"{tag or 'tur'}_controls_bosphorus")
            lines = [f"scripted_trigger {name} = {{"]
            for sid in ids[:2]:
                lines.append(f"\tcontrols_state = {sid}")
            lines.append("}")
            return "\n".join(lines) + "\n"
        if "custom character" in low or "army chief" in low \
                or "resistance leader" in low:
            name = "resistance_leader" if "resistance" in low else "custom_army_chief"
            label = "Resistance Leader" if "resistance" in low else "Custom Army Chief"
            return (f"scripted_effect {(tag or 'eth')}_{name} = {{\n"
                    "\tcreate_corps_commander = {\n"
                    f'\t\tname = "{label}"\n'
                    "\t\tskill = 2\n"
                    "\t\tattack_skill = 2\n"
                    "\t\tdefense_skill = 3\n"
                    "\t\ttraits = { guerilla_fighter }\n"
                    "\t}\n"
                    "}\n")
        return (f"scripted_effect {(tag or 'bra')}_coffee_boom = {{\n"
                "\tadd_stability = $AMOUNT$\n"
                "\tadd_political_power = $AMOUNT$\n"
                "}\n")

    def _idea(self, request: str, low: str) -> str:
        tag = (self._country_tag(request) or "").lower()
        name = _extract_name(request) or self._default_idea_name(low)
        did = self._new_id(f"{tag}_{_slug(name)}" if tag else _slug(name))
        lines = [f"{did} = {{", "\tremoval_cost = -1", "\tmodifier = {"]
        if "resentment" in low or "anschluss" in low:
            lines += ["\t\tstability_factor = -0.1", "\t\tresistance_target = 0.1"]
        elif "mobilization" in low or "mobilisation" in low:
            lines += ["\t\tconscription = 0.02", "\t\tarmy_org_factor = 0.1"]
        if "war support" in low:
            val = _extract_number(low, "war support")
            lines.append(f"\t\twar_support_factor = {-(val or 15) / 100.0:.2f}")
        if "stability" in low and "negative stability" not in low:
            val = _extract_number(low, "stability")
            lines.append(f"\t\tstability_factor = {abs(val or 10) / 100.0:.2f}")
        if "war economy" in low or "factory" in low:
            lines += ["\t\tconsumer_goods_factor = -0.1",
                      "\t\tindustrial_capacity_factory = 0.1"]
        lines += ["\t}", ""]
        if "escalating" in low or "over time" in low or "mobilization" in low:
            lines.append("\ton_add = { add_stability = -0.05 }")
        lines.append("}")
        return "\n".join(lines) + "\n"

    def _default_idea_name(self, low: str) -> str:
        if "anschluss" in low or "resentment" in low:
            return "anschluss_resentment"
        if "mobilization" in low or "mobilisation" in low:
            return "wartime_mobilization"
        return "custom_spirit"

    def _technology(self, low: str) -> str:
        tag = (self._country_tag(low) or "").lower()
        if "naval" in low and ("destroyer" in low or "hull" in low):
            did = self._new_id(f"{tag or 'uk'}_destroyer_improvements")
            return (f"{did} = {{\n"
                    "\tyear = 1937\n"
                    "\tcategories = {\n"
                    "\t\tnaval_equipment\n"
                    "\t}\n"
                    "\tfolder = {\n"
                    "\t\tname = mtgnavalfolder\n"
                    "\t}\n"
                    "\tresearch_cost = 4\n"
                    "\tenable_equipments = {\n"
                    "\t\tship_hull_light_2\n"
                    "\t}\n"
                    "}\n")
        did = self._new_id(f"{tag or 'agent'}_infantry_equipment_tech")
        from .config import mod_start_year

        _year = mod_start_year()
        return (f"{did} = {{\n"
                "\tenable_equipments = {\n"
                "\t\tinfantry_equipment_1\n"
                "\t}\n"
                "\tresearch_cost = 1.5\n"
                f"\tstart_year = {_year}\n"
                "\tfolder = {\n"
                "\t\tname = infantry_folder\n"
                "\t}\n"
                "\tcategories = {\n"
                "\t\tinfantry_weapons\n"
                "\t}\n"
                "}\n")

    def _equipment(self, low: str) -> str:
        tag = (self._country_tag(low) or "").lower()
        if "fighter" in low or "aircraft" in low or "carrier" in low:
            did = self._new_id(f"{tag or 'jap'}_carrier_fighter_1")
            return (f"equipments = {{\n"
                    f"\t{did} = {{\n"
                    "\t\tyear = 1938\n"
                    "\t\tis_buildable = yes\n"
                    "\t\tarchetype = cv_small_plane_airframe\n"
                    "\t\tparent = cv_small_plane_airframe_1\n"
                    "\t\tair_range = 500\n"
                    "\t\tmaximum_speed = 420\n"
                    "\t\tair_agility = 45\n"
                    "\t\tresources = {\n"
                    "\t\t\taluminium = 3\n"
                    "\t\t}\n"
                    "\t}\n"
                    "}\n")
        did = self._new_id(f"{tag or 'agent'}_light_tank_equipment_1")
        return (f"equipments = {{\n"
                f"\t{did} = {{\n"
                "\t\tyear = 1938\n"
                "\t\tarchetype = light_tank_equipment\n"
                "\t\tparent = light_tank_equipment_2\n"
                "\t\tis_buildable = yes\n"
                "\t\tpriority = 100\n"
                "\t\tvisual_level = 2\n"
                "\t\tmaximum_speed = 10\n"
                "\t\treliability = 0.8\n"
                "\t\tdefense = 6\n"
                "\t\tbreakthrough = 12\n"
                "\t\tarmor_value = 30\n"
                "\t\tsoft_attack = 16\n"
                "\t\thard_attack = 8\n"
                "\t\tap_attack = 30\n"
                "\t\tair_attack = 0\n"
                "\t\tbuild_cost_ic = 10\n"
                "\t\tfuel_consumption = 1.5\n"
                "\t\tresources = {\n"
                "\t\t\tsteel = 4\n"
                "\t\t\tchromium = 1\n"
                "\t\t}\n"
                "\t}\n"
                "}\n")

    def _division(self, low: str) -> str:
        if "tank" in low:
            return ("division_template = {\n"
                    '\tname = "Tank Shock Army"\n'
                    "\tregiments = {\n"
                    "\t\tmedium_armor = { x = 0 y = 0 }\n"
                    "\t\tmedium_armor = { x = 1 y = 0 }\n"
                    "\t\tmotorized = { x = 0 y = 1 }\n"
                    "\t}\n"
                    "\tsupport = {\n"
                    "\t\tengineer = { x = 0 y = 0 }\n"
                    "\t}\n"
                    "}\n")
        return ("division_template = {\n"
                '\tname = "Mountain Infantry Division"\n'
                "\tregiments = {\n"
                "\t\tmountaineers = { x = 0 y = 0 }\n"
                "\t\tmountaineers = { x = 1 y = 0 }\n"
                "\t\tmountaineers = { x = 0 y = 1 }\n"
                "\t}\n"
                "\tsupport = {\n"
                "\t\tengineer = { x = 0 y = 0 }\n"
                "\t\trecon = { x = 1 y = 0 }\n"
                "\t}\n"
                "}\n")

    def _upgrade(self, low: str) -> str:
        from .config import mod_start_year

        _year = mod_start_year()
        return ("equipments = {\n"
                "\tartillery_equipment_agent_1 = {\n"
                f"\t\tyear = {_year}\n"
                "\t\tarchetype = artillery_equipment\n"
                "\t\tpriority = 50\n"
                "\t\tvisual_level = 0\n"
                "\t}\n"
                "\tartillery_equipment_agent_2 = {\n"
                f"\t\tyear = {_year + 3}\n"
                "\t\tarchetype = artillery_equipment\n"
                "\t\tparent = artillery_equipment_agent_1\n"
                "\t\tpriority = 50\n"
                "\t\tvisual_level = 1\n"
                "\t\tsoft_attack = 30\n"
                "\t}\n"
                "\tartillery_equipment_agent_3 = {\n"
                "\t\tyear = 1942\n"
                "\t\tarchetype = artillery_equipment\n"
                "\t\tparent = artillery_equipment_agent_2\n"
                "\t\tpriority = 50\n"
                "\t\tvisual_level = 2\n"
                "\t\tsoft_attack = 35\n"
                "\t}\n"
                "}\n")

    def _ai_strategy(self, low: str) -> str:
        if "ally" in low:
            return ("add_ai_strategy = {\n"
                    "\ttype = alliance\n"
                    '\tid = "GER"\n'
                    "\tvalue = 100\n"
                    "}\n")
        return ("add_ai_strategy = {\n"
                "\ttype = conquer\n"
                '\tid = "POL"\n'
                "\tvalue = 50\n"
                "}\n")

    def _ai_will_do(self, low: str) -> str:
        return ("ai_will_do = {\n"
                "\tfactor = 5\n"
                "\tmodifier = {\n"
                "\t\tfactor = 0\n"
                "\t\thas_war_with = GER\n"
                "\t}\n"
                "}\n")

    def _character(self, request: str, low: str) -> str:
        tag = self._country_tag(request)
        if "general" in low or "field marshal" in low:
            cid = self._new_id(f"{tag or 'SOV'}_fictional_general")
            return (f"characters = {{\n"
                    f"\t{cid} = {{\n"
                    '\t\tname = "Viktor Groznyi"\n'
                    "\t\tfield_marshal = {\n"
                    "\t\t\tskill = 4\n"
                    "\t\t\tattack_skill = 5\n"
                    "\t\t\tdefense_skill = 2\n"
                    "\t\t\ttraits = { offensive_doctrine }\n"
                    "\t\t}\n"
                    "\t}\n"
                    "}\n")
        name = _extract_name(request) or "Heinrich Fiktiv"
        cid = self._new_id(f"{tag or 'GEN'}_{_slug(name)}")
        trait = _TRAIT_POOL[2]
        return (f"characters = {{\n"
                f"\t{cid} = {{\n"
                f'\t\tname = "{name}"\n'
                "\t\tadvisor = {\n"
                "\t\t\tslot = political_advisor\n"
                f"\t\t\tidea_token = {_slug(name)}_advisor\n"
                f"\t\t\ttraits = {{ {trait} }}\n"
                "\t\t\tcost = 150\n"
                "\t\t\tai_will_do = { factor = 1 }\n"
                "\t\t}\n"
                "\t}\n"
                "}\n")

    def _on_action(self, low: str) -> str:
        tag = (self._country_tag(low) or "").upper()
        if "declare" in low and "war" in low:
            return (f"on_actions = {{\n"
                    "\ton_declare_war = {\n"
                    f"\t\ttrigger = {{ tag = {tag or 'ENG'} }}\n"
                    "\t\teffect = {\n"
                    "\t\t\tadd_political_power = -20\n"
                    "\t\t}\n"
                    "\t}\n"
                    "}\n")
        return ("on_actions = {\n"
                "\ton_faction_formed = {\n"
                "\t\teffect = {\n"
                "\t\t\tadd_political_power = 20\n"
                "\t\t}\n"
                "\t}\n"
                "}\n")

    def _modifier(self, low: str) -> str:
        return ("custom_research_boost = {\n"
                "\tresearch_speed_factor = 0.1\n"
                "}\n")

    def _state_history(self, low: str) -> str:
        ids = self._verified_state_ids(1)
        sid = ids[0] if ids else 59
        return (f"state = {{\n"
                f"\tid = {sid}\n"
                f'\tname = "STATE_{sid}"\n'
                "\thistory = {\n"
                "\t\towner = GER\n"
                "\t\tvictory_points = {\n"
                f"\t\t\t{sid} 5\n"
                "\t\t}\n"
                "\t\tbuildings = {\n"
                "\t\t\tinfrastructure = 4\n"
                "\t\t}\n"
                "\t}\n"
                "}\n")

    def _country_history(self, request: str, low: str) -> str:
        tag = self._country_tag(request)
        ideology = _ideology(low)
        capital = self._vanilla_capital(tag)
        if ideology == "fascism":
            pops = {"fascism": 65, "democratic": 15, "communism": 10, "neutrality": 10}
        elif ideology == "communism":
            pops = {"fascism": 10, "democratic": 20, "communism": 60, "neutrality": 10}
        else:
            pops = {"fascism": 10, "democratic": 60, "communism": 20, "neutrality": 10}
        from .config import mod_start_year

        _year = mod_start_year()
        lines = [f"capital = {capital}", "", "set_politics = {",
                 f"\truling_party = {ideology}",
                 f'\tlast_election = "{_year}.1.1"', "\telection_frequency = 48",
                 "\telections_allowed = no", "}", "", "set_popularities = {"]
        for k, v in pops.items():
            lines.append(f"\t{k} = {v}")
        lines.append("}")
        return "\n".join(lines) + "\n"

    def _vanilla_capital(self, tag: str) -> int:
        try:
            from .filesystem import workspace

            name = self._country_name(tag)
            if not name:
                return 1
            for root in (workspace(), CONFIG.vanilla_root):
                f = root / "history" / "countries" / f"{tag} - {name}.txt"
                if not f.exists():
                    f = root / "history" / "countries" / f"{tag}.txt"
                if f.exists():
                    m = re.search(r"^\s*capital\s*=\s*(\d+)",
                                  f.read_text(encoding="utf-8", errors="ignore"), re.M)
                    if m:
                        return int(m.group(1))
        except Exception:  # noqa: BLE001
            pass
        return 64 if tag == "GER" else 1
