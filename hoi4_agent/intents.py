# Owner: ACTIVE
"""Intent classifier for the V2 existing-mod editor.

Eight intents, each mapping to an execution pipeline:
CREATE, MODIFY, REPAIR, MERGE, REFACTOR, EXPLAIN, VALIDATE, SEARCH.
"""

from __future__ import annotations

import re
from enum import Enum


class Intent(str, Enum):
    CREATE = "CREATE"
    MODIFY = "MODIFY"
    REPAIR = "REPAIR"
    MERGE = "MERGE"
    REFACTOR = "REFACTOR"
    EXPLAIN = "EXPLAIN"
    VALIDATE = "VALIDATE"
    SEARCH = "SEARCH"


RULES: list[tuple[Intent, tuple[str, ...]]] = [
    (Intent.MERGE, ("merge", "combine", "conflicting mods", "two mods", "merge these")),
    (Intent.REFACTOR, ("refactor", "deduplicate", "dedupe", "duplicated", "duplicate code",
                       "repeated effect", "repeated trigger", "duplicate blocks")),
    (Intent.EXPLAIN, ("explain", "why is", "why does", "root cause", "what does this error",
                      "why is this failing", "validator error")),
    (Intent.REPAIR, ("fix", "repair", "broken", "error.log", "crash", "missing localisation",
                     "missing icon", "invalid", "convert", "old syntax",
                     "failing", "repair this", "unbalanced", "not firing", "doesn't work")),
    (Intent.VALIDATE, ("validate", "run validators", "check the project", "validators")),
    (Intent.SEARCH, ("find", "search", "where is", "locate", "which file")),
    (Intent.MODIFY, ("modify", "extend", "expand", "change", "update", "edit", "add to the")),
    (Intent.CREATE, ("add", "create", "make", "new", "build", "transfer", "spawn",
                     "remove", "clear", "strip", "delete", "write")),
]

# Contextual follow-up edits ("make it cheaper", "rename it", "remove its
# bonus", "move it", "make the AI ignore it", "add a second option to it")
# are MODIFY requests even though they contain CREATE-ish verbs.
CONTEXTUAL_EDIT = re.compile(
    r"\b(?:rename|move)\b|"
    r"\b(?:make|change)\b[^\n]{0,60}\b(?:cheaper|cost|more expensive)\b|"
    r"\bremove\b[^\n]{0,60}\b(?:bonus|reward|completion reward)\b|"
    r"\bai\s*ignore\b|"
    r"\bsecond option\b|"
    r"\bchange its effect\b|"
    r"\blocalisation\s+(?:title|key)\s+for\b|"
    r"\bit\s+(?:cheaper|cost|to\s+\d+)\b",
    re.I)

# "upgrade" is only a REPAIR intent when it refers to upgrading existing
# content ("upgrade old syntax", "upgrade my mod"). "Add a technology
# unlocking an infantry equipment upgrade" or "write a 3-step equipment
# upgrade chain" are creation requests.
_UPGRADE_REPAIR_CTX = ("old", "outdated", "mod", "syntax", "convert",
                       "broken", "repair", "fix")


def classify(request: str) -> Intent:
    """Deterministic keyword classifier. Priority order encodes the fixes
    learned from the V1 benchmark (explain before validate, repair before create)."""
    low = request.lower()
    if CONTEXTUAL_EDIT.search(low):
        return Intent.MODIFY
    for intent, keywords in RULES:
        if intent is Intent.REPAIR and "upgrade" in low:
            if not any(w in low for w in _UPGRADE_REPAIR_CTX):
                continue  # creation-style upgrade, not a repair
        for k in keywords:
            if k == "search" and not re.search(r"\bsearch\b", low):
                continue  # don't match "search" inside "research"
            if k in low:
                return intent
    return Intent.EXPLAIN
