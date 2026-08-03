# Owner: ACTIVE
"""Deterministic repair engine + optional model fallback.

Given structured validator errors, the proposed project state, and the
identifier index, repairs ONLY the failing parts. The model is asked only
when no deterministic strategy applies.
"""

from __future__ import annotations

import difflib
import re
import time
from dataclasses import dataclass, field

from . import patcher


def convert_known_syntax(proposals: dict[str, str]) -> dict[str, str]:
    """Deterministic, documented syntax conversions (behaviour-preserving).

    - quotes unquoted `has_dlc = Name` values (current parser requires quotes)
    - adds a `limit = { always = yes }` to `if = { ... }` blocks that lack one
    """
    has_dlc = re.compile(r"(has_dlc\s*=\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*$)", re.M)
    for rel in list(proposals):
        text = proposals[rel]
        text = has_dlc.sub(lambda m: f'{m.group(1)}"{m.group(2)}"{m.group(3)}', text)
        text = re.sub(
            r"(\t+)if\s*=\s*\{\n(?!\t+limit\s*=\s*\{)",
            lambda m: f"{m.group(1)}if = {{\n{m.group(1)}\tlimit = {{ always = yes }}\n",
            text,
        )
        proposals[rel] = text
    return proposals


@dataclass
class RepairAttempt:
    attempt: int
    validator_errors: list[dict]
    tool_calls: list[dict] = field(default_factory=list)
    model_response: str = ""
    diffs: list[str] = field(default_factory=list)
    elapsed_sec: float = 0.0
    success: bool = False


class RepairEngine:
    def __init__(self, ctx, validator, tools, agent=None):
        self.ctx = ctx
        self.validator = validator
        self.tools = tools
        self.agent = agent
        self.index = ctx.index

    # ------------------------------------------------------------ main loop
    def run_repair_loop(self, proposals: dict[str, str], max_attempts: int = 5,
                        log: list[RepairAttempt] | None = None) -> tuple[dict[str, str], dict, list[RepairAttempt]]:
        log = log if log is not None else []
        validation = {"valid": False, "errors": []}
        for attempt in range(1, max_attempts + 1):
            t0 = time.perf_counter()
            validation = self.validator.validate_proposal(proposals)
            record = RepairAttempt(attempt=attempt, validator_errors=validation["errors"])
            record.success = validation["valid"]
            record.elapsed_sec = round(time.perf_counter() - t0, 3)
            if validation["valid"]:
                log.append(record)
                break
            if not self._self_check(proposals, validation["errors"]):
                record.model_response = self._model_repair(proposals, validation["errors"])
            proposals, calls, model_resp = self._repair_once(proposals, validation["errors"])
            record.tool_calls = calls
            if model_resp:
                record.model_response = model_resp
            record.diffs = self._proposal_diffs(proposals)
            record.elapsed_sec = round(time.perf_counter() - t0, 3)
            log.append(record)
        return proposals, validation, log

    def _proposal_diffs(self, proposals: dict[str, str]) -> list[str]:
        diffs = []
        for path, content in proposals.items():
            diffs.append(patcher.make_diff(path, self._read_original(path), content))
        return diffs

    def _read_original(self, path: str) -> str:
        from .filesystem import read_text_keep, workspace

        try:
            return read_text_keep(workspace() / path)
        except OSError:
            return ""

    # ------------------------------------------------------------ self-check
    def _self_check(self, proposals, errors) -> bool:
        if self.agent is None or not getattr(self.agent, "use_model", False):
            return True
        context = self.ctx.memory.context_summary()
        prompt = (
            "Answer YES or NO to each question about your pending HOI4 patch:\n"
            "1. Did I invent any identifiers?\n"
            "2. Did I verify every identifier against the vanilla index?\n"
            "3. Did I preserve localisation?\n"
            "4. Did I modify unrelated code?\n"
            "5. Did I balance every block?\n\n"
            "Validator errors:\n" + "\n".join(f"- [{e['type']}] {e['message']}" for e in errors[:20]) +
            "\n\nVerified identifiers:\n" + context +
            "\n\nAnswer format: 1. YES/NO 2. YES/NO 3. YES/NO 4. YES/NO 5. YES/NO"
        )
        answer = self.agent.generate_with_model(prompt, max_new_tokens=80).upper()
        ok = answer.count("NO") == 0 and "YES" in answer
        self.ctx.memory.notes.append(f"self_check: {answer.strip()[:80]}")
        return ok

    # ------------------------------------------------------------ dispatcher
    def _repair_once(self, proposals, errors) -> tuple[dict, list[dict], str]:
        calls: list[dict] = []
        changed = False
        model_resp = ""
        handlers = {
            "unknown_identifier": self._repair_unknown_identifier,
            "duplicate_identifier": self._repair_duplicate,
            "duplicate_event_id": self._repair_duplicate,
            "missing_localisation": self._repair_localisation,
            "brace_mismatch": self._repair_braces,
            "invalid_scope": self._repair_scope,
            "broken_reference": self._repair_broken_reference,
            "invalid_effect": self._repair_invalid_effect,
            "invalid_trigger": self._repair_invalid_effect,
            "invalid_modifier": self._repair_invalid_modifier,
            "unknown_icon": self._repair_icon,
            "missing_sprite": self._repair_icon,
            "missing_required_block": self._repair_required_block,
        }
        for error in errors:
            handler = handlers.get(error.get("type"))
            if handler is None:
                continue
            result = handler(proposals, error)
            if result is None:
                continue
            proposals, tool_calls = result
            calls.extend(tool_calls)
            changed = True
        if not changed:
            # deterministic repair made no progress: ask the model once
            model_resp = self._model_repair(proposals, errors)
            if model_resp:
                patched = self._apply_model_output(proposals, model_resp, errors)
                if patched:
                    proposals = patched
        return proposals, calls, model_resp

    # ------------------------------------------------------ deterministic fixes
    def _replace_in_file(self, proposals, file, old, new) -> bool:
        if file not in proposals:
            return False
        content = proposals[file]
        if old not in content:
            return False
        proposals[file] = content.replace(old, new, 1)
        return True

    def _best_similar(self, name: str, pool: dict) -> tuple[str, float] | None:
        if not pool:
            return None
        matches = difflib.get_close_matches(name, list(pool), n=1, cutoff=0.55)
        if not matches:
            return None
        best = matches[0]
        ratio = difflib.SequenceMatcher(None, name.lower(), best.lower()).ratio()
        return best, ratio

    def _repair_unknown_identifier(self, proposals, error) -> tuple[dict, list[dict]] | None:
        ident = error.get("identifier")
        file = error.get("file")
        if not ident or not file or file not in proposals:
            return None
        calls = [{"tool": "search_identifier", "args": {"name": ident}}]
        self.tools.search_identifier(ident)
        if self.index.search(ident):
            return None
        similar = self.index.fuzzy(ident, limit=5)
        calls.append({"tool": "find_similar_identifier", "args": {"name": ident}})
        self.tools.find_similar_identifier(ident)
        if not similar:
            return None
        best, ratio = max(
            ((s["identifier"], difflib.SequenceMatcher(None, ident.lower(), s["identifier"].lower()).ratio())
             for s in similar),
            key=lambda kv: kv[1],
        )
        if ratio < 0.75:
            return None
        self._replace_in_file(proposals, file, ident, best)
        self.ctx.memory.verify_identifier(best, f"replaced unknown `{ident}`")
        return proposals, calls

    def _repair_duplicate(self, proposals, error) -> tuple[dict, list[dict]] | None:
        ident = error.get("identifier")
        if not ident:
            return None
        for file, content in proposals.items():
            if content.count(ident) <= 1:
                continue
            new_id = self._next_available(ident, proposals)
            first = content.find(ident)
            second = content.find(ident, first + len(ident))
            if second >= 0:
                proposals[file] = content[:second] + new_id + content[second + len(ident):]
                self.ctx.memory.verify_identifier(new_id, f"renamed duplicate of `{ident}`")
                return proposals, [{"tool": "validate_code", "args": {}}]
        return None

    def _next_available(self, ident: str, proposals) -> str:
        used: set[str] = set()
        for content in proposals.values():
            used.update(re.findall(rf"\b{re.escape(ident)}(?:_\d+)?\b", content))
        n = 1
        while True:
            cand = ident if n == 1 else f"{ident}_{n}"
            if cand not in used and not self.index.contains(cand):
                return cand
            n += 1

    def _repair_localisation(self, proposals, error) -> tuple[dict, list[dict]] | None:
        ident = error.get("identifier")
        if not ident:
            return None
        m = re.search(r"missing localisation keys: (.+)$", error.get("message", ""))
        needed_keys = [k.strip() for k in m.group(1).split(",")] if m else []
        loc_path = next((p for p in proposals if "localisation" in p and p.endswith(".yml")), None)
        if loc_path is None:
            prefix = re.match(r"^([A-Z]{2,3})_", ident)
            loc_path = f"localisation/english/{(prefix.group(1).lower() if prefix else 'mod')}_l_english.yml"
            from .filesystem import workspace

            # Start from the existing workspace file so repair never wipes
            # user/vanilla localisation keys.
            existing_loc = workspace() / loc_path
            proposals[loc_path] = (
                existing_loc.read_text(encoding="utf-8-sig",
                                       errors="surrogateescape")
                if existing_loc.exists() else "l_english:\n"
            )
        lines = proposals[loc_path].rstrip("\n") + "\n"
        existing = self.validator._extract_loc_keys(proposals[loc_path])
        kinds = needed_keys or ([ident] if ("." in ident) else [ident, ident + "_desc"])
        for key in kinds:
            if key not in existing:
                lines += f' {key}:0 "Agent-generated text for {key}"\n'
                existing.add(key)
        proposals[loc_path] = lines
        self.ctx.memory.notes.append(f"added localisation for {ident}")
        return proposals, [{"tool": "validate_localisation", "args": {}}]

    def _repair_braces(self, proposals, error) -> tuple[dict, list[dict]] | None:
        file = error.get("file")
        if not file or file not in proposals:
            return None
        content = proposals[file]
        depth = self._brace_depth(content)
        if depth > 0:
            proposals[file] = content.rstrip() + "\n" + "}" * depth + "\n"
            return proposals, [{"tool": "validate_code", "args": {}}]
        if depth < 0:
            fixed = content.rstrip()
            for _ in range(-depth):
                if fixed.endswith("}"):
                    fixed = fixed[:-1]
            proposals[file] = fixed + "\n"
            return proposals, [{"tool": "validate_code", "args": {}}]
        return None

    @staticmethod
    def _brace_depth(text: str) -> int:
        cleaned = re.sub(r"#[^\n]*", "", text)
        cleaned = re.sub(r'"[^"\n]*"', '""', cleaned)
        return cleaned.count("{") - cleaned.count("}")

    def _repair_scope(self, proposals, error) -> tuple[dict, list[dict]] | None:
        eff = error.get("identifier")
        file = error.get("file")
        line = error.get("line")
        if not eff or not file or file not in proposals:
            return None
        calls = [{"tool": "search_documentation", "args": {"query": eff}}]
        self.tools.search_documentation(eff)
        lines = proposals[file].splitlines()
        if not line or line > len(lines):
            return None
        idx = line - 1
        stripped = lines[idx].strip()
        if not stripped.startswith(eff):
            return None
        indent = re.match(r"^[ \t]*", lines[idx]).group(0)
        lines[idx] = f"{indent}random_owned_controlled_state = {{\n{indent}\t{stripped}\n{indent}}}"
        proposals[file] = "\n".join(lines) + "\n"
        return proposals, calls

    def _repair_broken_reference(self, proposals, error) -> tuple[dict, list[dict]] | None:
        ident = error.get("identifier")
        file = error.get("file")
        if not ident or not file or file not in proposals:
            return None
        calls = [{"tool": "search_identifier", "args": {"name": ident}},
                 {"tool": "find_similar_identifier", "args": {"name": ident}}]
        self.tools.search_identifier(ident)
        self.tools.find_similar_identifier(ident)
        focuses = self.index.categories().get("focuses", {})
        best = self._best_similar(ident, focuses)
        if not best or best[1] < 0.75:
            return None
        self._replace_in_file(proposals, file, ident, best[0])
        self.ctx.memory.verify_identifier(best[0], "replaced broken focus reference")
        return proposals, calls

    def _repair_invalid_effect(self, proposals, error) -> tuple[dict, list[dict]] | None:
        name = error.get("identifier")
        file = error.get("file")
        if not name or not file or file not in proposals:
            return None
        if error.get("type") == "invalid_effect":
            pool = {**self.validator.effects, **self.index.categories().get("scripted_effects", {})}
        else:
            pool = {**self.validator.triggers, **self.index.categories().get("scripted_triggers", {})}
        best = self._best_similar(name, pool)
        if not best or best[1] < 0.8:
            return None
        self._replace_in_file(proposals, file, name, best[0])
        self.ctx.memory.verify_identifier(best[0], "corrected misspelled keyword")
        return proposals, [{"tool": "search_documentation", "args": {"query": name}}]

    def _repair_invalid_modifier(self, proposals, error) -> tuple[dict, list[dict]] | None:
        name = error.get("identifier")
        file = error.get("file")
        if not name or not file or file not in proposals:
            return None
        best = self._best_similar(name, self.validator.modifiers)
        if not best or best[1] < 0.8:
            return None
        self._replace_in_file(proposals, file, name, best[0])
        return proposals, [{"tool": "search_documentation", "args": {"query": name}}]

    def _repair_icon(self, proposals, error) -> tuple[dict, list[dict]] | None:
        file = error.get("file")
        if not file or file not in proposals:
            return None
        self.validator._ensure_icons()
        icons = sorted(self.validator.icon_set)
        if not icons:
            return None
        fallback = next((i for i in icons if "generic" in i.lower()), icons[0])
        content = proposals[file]
        for m in re.finditer(r"(\s*icon\s*=\s*)\S+", content):
            proposals[file] = content[: m.start(1)] + m.group(1) + fallback + content[m.end():]
            self.ctx.memory.notes.append(f"replaced icon with verified `{fallback}`")
            return proposals, [{"tool": "validate_code", "args": {}}]
        return None

    def _repair_required_block(self, proposals, error) -> tuple[dict, list[dict]] | None:
        file = error.get("file")
        line = error.get("line")
        message = error.get("message", "")
        if not file or file not in proposals:
            return None
        if "no id" in message:
            return None  # cannot know the intended id deterministically
        if "options" in message:
            content = proposals[file]
            m = re.search(r"id\s*=\s*([A-Za-z0-9_.]+)", content)
            event_id = m.group(1) if m else "agent_event"
            # HOI4 events use direct `option = { ... }` blocks inside the event
            # (an `options = { }` wrapper is not valid game syntax).
            block = (
                f"\toption = {{\n"
                f"\t\tname = {event_id}.a\n"
                "\t}\n"
            )
            lines = content.splitlines()
            if line and 0 < line <= len(lines):
                # find the block's closing brace starting from the event block line
                depth = 0
                insert_at = len(lines)
                for i in range(line - 1, len(lines)):
                    depth += lines[i].count("{") - lines[i].count("}")
                    if depth <= 0 and i > line - 1:
                        insert_at = i
                        break
                indent = re.match(r"^[ \t]*", lines[line - 1]).group(0) + "\t"
                block = "\n".join(indent + ln for ln in block.strip().splitlines()) + "\n"
                lines.insert(insert_at, block.rstrip("\n"))
                proposals[file] = "\n".join(lines) + "\n"
            else:
                proposals[file] = content.rstrip() + "\n" + block
            self._repair_localisation(proposals, {
                "identifier": f"{event_id}.a", "type": "missing_localisation"})
            return proposals, [{"tool": "validate_code", "args": {}}]
        return None

    # ------------------------------------------------------------- model path
    def _model_repair(self, proposals, errors) -> str:
        if self.agent is None or not getattr(self.agent, "use_model", False):
            return ""
        target = errors[0].get("file") if errors else next(iter(proposals))
        context = self.ctx.memory.context_summary()
        prompt = (
            "Repair ONLY the reported problems in this HOI4 file. Do not rewrite "
            "unrelated code. Never invent identifiers.\n\n"
            "Validator errors:\n" +
            "\n".join(f"- [{e['type']}] {e['message']}" + (f" (line {e.get('line')})" if e.get("line") else "")
                      for e in errors[:20]) +
            "\n\nVerified identifiers only (from the vanilla index):\n" + context +
            "\n\nCurrent file content:\n```\n" + proposals.get(target, "")[:4000] + "\n```\n\n"
            "Return the complete corrected file content in a code fence. If you cannot "
            "repair it deterministically, respond with UNRESOLVED."
        )
        return self.agent.generate_with_model(prompt, max_new_tokens=1200)

    def _apply_model_output(self, proposals, model_resp, errors) -> dict | None:
        fence = re.findall(r"```[a-z]*\n(.*?)```", model_resp, re.S)
        content = fence[-1] if fence else model_resp
        if not content.strip() or "UNRESOLVED" in content.upper():
            return None
        target = errors[0].get("file") if errors else next(iter(proposals))
        if target in proposals:
            proposals[target] = content
            return proposals
        return None
