# Owner: ACTIVE
"""Strict-mode execution budgets (instruction fidelity).

Before any patch is staged, the request is parsed into an execution budget:
the maximum number of new objects per kind, the maximum number of files that
may change, and whether new files are allowed at all. The proposal is then
checked against that budget; anything over budget is reported so the caller
can shrink or refuse it. Generators in this codebase are minimal by
construction, so a budget violation is a bug, not an expectation.
"""

from __future__ import annotations

import re

from hoi4_agent._runtime.hoi4parser import parse_tree, walk

_COUNT = re.compile(
    r"\b(one|a|an|the|two|three|\d+)\s*-?\s*"
    r"(focus(?:es)?|event(?:s)?|decision(?:s)?|idea(?:s)?|spirit(?:s)?|"
    r"technology(?:ies)?|division template(?:s)?|character(?:s)?|"
    r"advisor(?:s)?|modifier(?:s)?|option(?:s)?)\b", re.I)

_PLURALS = {"focuses": "focus", "events": "event", "decisions": "decision",
            "ideas": "idea", "spirits": "spirit", "technologies": "technology",
            "templates": "division", "characters": "character",
            "advisors": "advisor", "modifiers": "modifier", "options": "option"}


def _word_count(low: str, singular: str) -> int:
    plural = singular + "s"
    m = re.search(rf"\b(\d+)\s*-?\s*{plural}\b", low)
    if m:
        return int(m.group(1))
    if re.search(rf"\b(?:two|three)\s*-?\s*{singular}\b", low):
        return 2 if "two" in low else 3
    if re.search(rf"\b(?:two|three)\s*-?\s*{plural}\b", low):
        return 2 if "two" in low else 3
    if re.search(rf"\b(?:one|a|an|the)\s+{singular}\b", low) or \
            re.search(rf"\b(?:one|a|an|the)\s+{plural}\b", low):
        return 1
    return 0


def infer_budget(request: str) -> dict:
    """Execution budget derived from the literal request."""
    low = request.lower()
    budget: dict = {"objects": {}, "max_files": None, "allow_new_files": True,
                    "modify_only": False}
    for singular in ("focus", "event", "decision", "idea", "spirit",
                     "technology", "character", "advisor", "modifier",
                     "division template", "option"):
        n = _word_count(low, singular)
        if n:
            budget["objects"][singular] = n
    if not budget["objects"]:
        # Unquantified single-object phrasing ("add a focus for Italy called X")
        for singular in ("focus", "event", "decision", "spirit", "idea",
                         "technology", "character", "division template"):
            if re.search(rf"\b{singular}\b", low):
                budget["objects"][singular] = 1
                break
    if any(w in low for w in ("modify", "change", "update", "edit", "fix",
                              "repair", "convert")):
        budget["modify_only"] = True
        budget["allow_new_files"] = "new" in low
    if re.search(r"\b(?:focus tree|branch|focuses|decisions|events|ideas)\b", low):
        budget["max_files"] = None  # project pipeline owns its own scope
    return budget


def count_objects(proposals: dict[str, str]) -> dict[str, int]:
    """Generated objects per kind, parsed structurally (top-level blocks)."""
    counts: dict[str, int] = {}
    for content in proposals.values():
        try:
            tree = parse_tree(content)
        except Exception:  # noqa: BLE001
            continue
        for node in tree:
            if node.get("kind") != "block":
                continue
            key = node.get("key", "")
            if key in ("country_event", "news_event", "state_event"):
                counts["event"] = counts.get("event", 0) + 1
            elif key in ("scripted_effect", "scripted_trigger"):
                counts[key] = counts.get(key, 0) + 1
            elif key == "characters":
                counts["character"] = counts.get("character", 0) + 1
            elif key == "division_template":
                counts["division"] = counts.get("division", 0) + 1
            elif key == "on_actions":
                counts["on_action"] = counts.get("on_action", 0) + 1
            elif key == "equipments":
                counts["equipment"] = len([
                    c for c in node.get("children", [])
                    if c.get("kind") == "block" and c.get("key")])
        # focus blocks are nested inside focus_tree
        counts["focus"] = counts.get("focus", 0) + len([
            n for n in walk(tree, "focus") if n.get("kind") == "block"])
        for node in tree:
            if node.get("kind") == "block" and node.get("key"):
                if node.get("key") == "ai_will_do":
                    counts["ai_will_do"] = counts.get("ai_will_do", 0) + 1
                child_keys = {c.get("key") for c in node.get("children", [])
                              if c.get("kind") == "block"}
                if "modifier" in child_keys and node.get("key") not in (
                        "modifier", "ai_will_do", "available"):
                    counts["idea"] = counts.get("idea", 0) + 1
                if child_keys & {"available", "complete_effect"} \
                        and node.get("key") not in ("focus", "country_event",
                                                    "news_event", "state_event"):
                    counts["decision"] = counts.get("decision", 0) + 1
    return counts


def check_budget(budget: dict, proposals: dict[str, str]) -> dict:
    """Compare generated objects against the budget. Returns
    {"ok": bool, "requested": dict, "generated": dict, "violations": [...]}."""
    generated = count_objects(proposals)
    violations: list[str] = []
    for kind, limit in budget.get("objects", {}).items():
        got = generated.get(kind, 0)
        if got > limit:
            violations.append(
                f"{kind}: requested {limit}, generated {got}")
    return {
        "ok": not violations,
        "requested": budget.get("objects", {}),
        "generated": generated,
        "violations": violations,
    }
