# Owner: ACTIVE
"""Rule-based planner: intent detection and deterministic tool plans.
The planner never guesses identifiers; it only decides what to inspect."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import CONFIG

COUNTRY_TAGS = {
    "german": "GER", "germany": "GER", "british": "ENG", "english": "ENG",
    "american": "USA", "usa": "USA", "french": "FRA", "france": "FRA",
    "italian": "ITA", "italy": "ITA", "polish": "POL", "poland": "POL",
    "soviet": "SOV", "soviets": "SOV", "russian": "SOV", "russia": "SOV",
    "japanese": "JAP", "japan": "JAP",
    "chinese": "CHI", "china": "CHI", "spanish": "SPA", "spain": "SPA",
    "canadian": "CAN", "canada": "CAN", "australian": "AST", "australia": "AST",
    "south african": "SAF", "south africa": "SAF", "new zealand": "NZL",
    "brazilian": "BRA", "brazil": "BRA", "argentine": "ARG", "argentina": "ARG",
    "mexican": "MEX", "mexico": "MEX", "portuguese": "POR", "portugal": "POR",
    "dutch": "HOL", "netherlands": "HOL", "belgian": "BEL", "belgium": "BEL",
    "norwegian": "NOR", "norway": "NOR", "swedish": "SWE", "sweden": "SWE",
    "finnish": "FIN", "finland": "FIN", "danish": "DEN", "denmark": "DEN",
    "greek": "GRE", "greece": "GRE", "turkish": "TUR", "turkey": "TUR",
    "hungarian": "HUN", "hungary": "HUN", "romanian": "ROM", "romania": "ROM",
    "bulgarian": "BUL", "bulgaria": "BUL", "yugoslav": "YUG", "yugoslavia": "YUG",
    "czech": "CZE", "czechoslovak": "CZE", "czechoslovakia": "CZE",
    "paraguayan": "PAR", "paraguay": "PAR", "nepalese": "NEP", "nepal": "NEP",
    "ethiopian": "ETH", "ethiopia": "ETH", "iraqi": "IRQ", "iraq": "IRQ",
    "persian": "PER", "persia": "PER", "afghan": "AFG", "afghanistan": "AFG",
    "siam": "SIA", "thai": "SIA", "thailand": "SIA", "philippine": "PHI",
    "philippines": "PHI", "indonesian": "INS", "indonesia": "INS",
    "uk": "ENG", "britain": "ENG", "america": "USA", "united states": "USA",
}

COUNTRY_FILES = {
    "GER": "germany.txt", "ENG": "england.txt", "USA": "usa.txt",
    "FRA": "france.txt", "ITA": "italy.txt", "POL": "poland.txt",
    "SOV": "soviet.txt", "JAP": "japan.txt", "CHI": "china.txt", "SPA": "spain.txt",
    "CAN": "canada.txt", "AST": "australia.txt", "SAF": "south africa.txt",
    "NZL": "new zealand.txt", "BRA": "brazil.txt", "ARG": "argentina.txt",
    "MEX": "mexico.txt", "POR": "portugal.txt", "HOL": "netherlands.txt",
    "BEL": "belgium.txt", "NOR": "norway.txt", "SWE": "sweden.txt",
    "FIN": "finland.txt", "DEN": "denmark.txt", "GRE": "greece.txt",
    "TUR": "turkey.txt", "HUN": "hungary.txt", "ROM": "romania.txt",
    "BUL": "bulgaria.txt", "YUG": "yugoslavia.txt", "CZE": "czechoslovakia.txt",
    "PAR": "paraguay.txt", "NEP": "nepal.txt", "ETH": "ethiopia.txt",
    "IRQ": "iraq.txt", "PER": "persia.txt", "AFG": "afghanistan.txt",
    "SIA": "siam.txt", "PHI": "philippines.txt", "INS": "indonesia.txt",
}

STATUS_BY_TOOL = {
    "list_directory": "Inspecting project structure...",
    "read_file": "Reading focus tree...",
    "search_identifier": "Searching identifiers...",
    "find_similar_identifier": "Looking for similar identifiers...",
    "find_vanilla_examples": "Reading vanilla examples...",
    "search_documentation": "Consulting official documentation...",
    "search_wiki": "Consulting the wiki...",
    "search_files": "Searching files...",
    "validate_code": "Validating proposed code...",
    "validate_focus_tree": "Validating focus tree...",
    "validate_events": "Validating events...",
    "validate_localisation": "Validating localisation...",
    "show_diff": "Showing diff...",
    "apply_patch": "Applying changes...",
    "__generate_patch__": "Generating patch...",
}

FOCUS_EVENT_RE = re.compile(
    r"\b(?:add|attach|insert|put|give)\b[\s\S]{0,60}?\bevent\b"
    r"[\s\S]{0,60}?\b(?:to|in|on|for)\b[\s\S]{0,80}?\bfocus\b"
)

FOCUS_EFFECTS_RE = re.compile(
    r"(?:"
    r"\b(?:add|give|set|put)\b[\s\S]{0,50}?"
    r"(?:(?:effect|reward)s?|political power|pp\b|stability|war support|"
    r"manpower|army experience|navy experience|air experience)"
    r"[\s\S]{0,50}?\b(?:to|on|in|for)\b[\s\S]{0,60}?\b(?:focus|tree)\b"
    r"|"
    r"\b(?:change|modify|edit|update)\b[\s\S]{0,60}?\bfocus\b"
    r"[\s\S]{0,80}?\b(?:add|give|set)\b[\s\S]{0,60}?"
    r"\b(?:effect|reward|manpower|political power|stability|war support|"
    r"army experience|navy experience|air experience)\b"
    r")"
)

ORDINAL_WORDS = {
    "second": "2", "third": "3", "fourth": "4", "fifth": "5", "sixth": "6",
    "seventh": "7", "eighth": "8", "ninth": "9", "tenth": "10",
}

# Country-scoped features that can be requested for several countries at once
# and need no follow-up question (new_country / focus_effects are excluded).
MULTI_COUNTRY_FEATURES = frozenset({
    "focus_branch", "civil_war", "modify_focus_tree", "decisions_events",
    "idea_chain", "ideas", "focus_with_ideas", "decisions", "events",
    "remove_content", "advisors", "releasable",
})

MODIFIER_WORDS = {
    "stability": "stability_factor",
    "war support": "war_support_factor",
    "consumer goods": "consumer_goods_factor",
    "factory output": "industrial_capacity_factory",
    "factory production": "industrial_capacity_factory",
    "research speed": "research_speed_factor",
    "political power gain": "political_power_gain",
    "political power": "political_power_gain",
    "justify war goal": "justify_war_goal_time",
}

EFFECT_WORDS = {
    "political power": "add_political_power",
    "stability": "add_stability",
    "war support": "add_war_support",
    "army experience": "army_experience",
    "navy experience": "navy_experience",
    "air experience": "air_experience",
    "manpower": "add_manpower",
}


@dataclass
class Plan:
    intent: str
    target_files: list[str] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)
    statuses: list[str] = field(default_factory=list)
    parsed: dict = field(default_factory=dict)


@dataclass
class ProjectTask:
    id: str
    objective: str
    dependencies: list[str] = field(default_factory=list)
    estimated_files: list[str] = field(default_factory=list)
    validator: str = "proposal"
    status: str = "pending"  # pending | running | completed | failed
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "objective": self.objective,
            "dependencies": self.dependencies,
            "estimated_files": self.estimated_files,
            "validator": self.validator,
            "status": self.status,
            "detail": self.detail,
        }


@dataclass
class ProjectPlan:
    name: str
    request: str
    country_tag: str
    feature: str
    tasks: list[ProjectTask] = field(default_factory=list)
    new_country_name: str = ""
    new_country_tag: str = ""
    politics: str = ""
    selected_states: list[int] = field(default_factory=list)
    focus_position: str = ""
    effect_spec: list[dict] = field(default_factory=list)
    remove_spec: dict = field(default_factory=dict)
    division_name: str = ""
    spawn: bool | None = None
    oob_count: int = 0
    unit_key: str = ""

    def task(self, task_id: str) -> ProjectTask | None:
        return next((t for t in self.tasks if t.id == task_id), None)

    def topological_order(self) -> list[ProjectTask]:
        order: list[ProjectTask] = []
        visited: set[str] = set()
        by_id = {t.id: t for t in self.tasks}

        def visit(tid: str) -> None:
            if tid in visited:
                return
            visited.add(tid)
            task = by_id[tid]
            for dep in task.dependencies:
                visit(dep)
            order.append(task)

        for t in self.tasks:
            visit(t.id)
        return order

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "request": self.request,
            "country_tag": self.country_tag,
            "feature": self.feature,
            "tasks": [t.to_dict() for t in self.tasks],
            "new_country_name": self.new_country_name,
            "new_country_tag": self.new_country_tag,
            "politics": self.politics,
            "selected_states": self.selected_states,
            "focus_position": self.focus_position,
            "effect_spec": self.effect_spec,
            "remove_spec": self.remove_spec,
            "division_name": self.division_name,
            "spawn": self.spawn,
            "oob_count": self.oob_count,
            "unit_key": self.unit_key,
        }


FEATURE_TASK_SETS: dict[str, list[str]] = {
    "focus_branch": ["focuses", "localisation", "validate", "apply"],
    "focus_with_ideas": ["ideas", "focuses", "localisation", "validate", "apply"],
    "civil_war": ["ideas", "focuses", "events", "decisions", "ai_strategy",
                  "references", "localisation", "validate", "apply"],
    "idea_chain": ["ideas", "localisation", "validate", "apply"],
    "ideas": ["ideas", "localisation", "validate", "apply"],
    "decisions": ["decisions", "localisation", "validate", "apply"],
    "events": ["events", "localisation", "validate", "apply"],
    "decisions_events": ["decisions", "events", "localisation", "validate", "apply"],
    "releasable": ["ideas", "focuses", "references", "localisation", "validate", "apply"],
    "advisors": ["ideas", "characters", "localisation", "validate", "apply"],
    "modify_focus_tree": ["focuses", "localisation", "validate", "apply"],
    "new_country": ["country_tag", "ideas", "country_files", "oob", "focuses", "events",
                    "decisions", "ai_strategy", "localisation", "validate", "apply"],
    "transfer_states": ["state_transfer", "validate", "apply"],
    "focus_event": ["focus_event", "localisation", "validate", "apply"],
    "focus_effects": ["focus_effects", "localisation", "validate", "apply"],
    "remove_content": ["remove_content", "localisation", "validate", "apply"],
    "oob": ["oob", "validate", "apply"],
}

TASK_OBJECTIVES = {
    "ideas": "Create national spirits / idea chain",
    "focuses": "Create focus tree / branch",
    "events": "Create events",
    "decisions": "Create decisions",
    "characters": "Create advisor characters",
    "ai_strategy": "Create AI strategy",
    "references": "Update file references (history wiring)",
    "country_tag": "Register the new country tag",
    "country_files": "Create country definition, history, and state",
    "oob": "Generate starting army (OOB)",
    "state_transfer": "Transfer selected states to the target country",
    "focus_event": "Add an event to a focus",
    "focus_effects": "Add completion-reward effects to focuses",
    "remove_content": "Remove or clear effects on a content type",
    "localisation": "Create localisation for all new identifiers",
    "validate": "Validate the whole project",
    "apply": "Apply patches after approval",
}

TASK_DEPENDENCIES: dict[str, list[str]] = {
    "country_tag": [],
    "country_files": ["country_tag", "ideas"],
    "oob": ["country_files"],
    "state_transfer": [],
    "focus_event": [],
    "focus_effects": [],
    "remove_content": [],
    "ideas": [],
    "focuses": ["ideas", "country_files"],
    "events": ["ideas", "country_files"],
    "decisions": ["ideas", "country_files"],
    "characters": ["ideas", "country_files"],
    "ai_strategy": ["focuses"],
    "references": ["focuses", "ideas"],
    "localisation": ["focuses", "events", "decisions", "ideas", "characters", "country_files"],
    "validate": ["ideas", "focuses", "events", "decisions", "characters",
                 "ai_strategy", "references", "localisation", "country_files",
                 "focus_event", "focus_effects", "remove_content"],
    "apply": ["validate"],
}

TASK_FILES: dict[str, list[str]] = {
    "ideas": ["common/ideas/"],
    "focuses": ["common/national_focus/"],
    "events": ["events/"],
    "decisions": ["common/decisions/"],
    "characters": ["common/characters/"],
    "ai_strategy": ["common/ai_strategy/"],
    "references": ["history/countries/"],
    "country_tag": ["common/country_tags/"],
    "country_files": ["common/countries/", "history/countries/", "history/states/"],
    "oob": ["history/units/"],
    "state_transfer": ["history/states/"],
    "focus_event": ["common/national_focus/", "events/"],
    "focus_effects": ["common/national_focus/"],
    "remove_content": ["common/national_focus/", "common/decisions/",
                       "events/", "common/ideas/"],
    "localisation": ["localisation/english/"],
}


class Planner:
    _vanilla_names: dict[str, str] | None = None

    def _load_vanilla_country_names(self) -> dict[str, str]:
        if Planner._vanilla_names is not None:
            return Planner._vanilla_names
        names: dict[str, str] = {}
        tags_dir = CONFIG.index_dir.parent.parent / "raw" / "game" / "common" / "country_tags"
        if tags_dir.exists():
            for f in tags_dir.glob("*.txt"):
                for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
                    m = re.match(r'^([A-Z0-9]{1,3})\s*=\s*"?countries/(.+)\.txt"?$', line.strip())
                    if m:
                        names[m.group(1)] = m.group(2)
        Planner._vanilla_names = names
        return names

    def _workspace_countries(self) -> tuple[dict[str, str], dict[str, str]]:
        """(name_lower -> tag, tag -> name) for countries defined in the workspace mod."""
        from .project_scan import ProjectScan

        scan = ProjectScan().build()
        names: dict[str, str] = {}
        tags: dict[str, str] = {}
        for rel, info in scan.get("files", {}).items():
            if rel.startswith("common/country_tags") and info["kind"] == "unknown":
                text = (CONFIG.workspace_root / rel).read_text(encoding="utf-8", errors="ignore")
                for line in text.splitlines():
                    m = re.match(r'^([A-Z0-9]{1,3})\s*=\s*"?countries/(.+)\.txt"?$', line.strip())
                    if m:
                        names[m.group(2).lower()] = m.group(1)
                        tags[m.group(1)] = m.group(2)
            elif rel.startswith("history/countries"):
                m = re.match(r"^([A-Z0-9]{1,3})\s*-\s*(.+)\.txt$", Path(rel).name)
                if m:
                    names[m.group(2).lower()] = m.group(1)
                    tags.setdefault(m.group(1), m.group(2))
        return names, tags

    def _detect_new_country(self, request: str) -> tuple[str, str] | None:
        low = request.lower()
        country_intent = (
            "new country" in low or "new nation" in low or
            (("create" in low or "add" in low or "make" in low or "turn" in low or "form" in low) and
             (re.search(r"\bcountry\b", low) or re.search(r"\bnation\b", low)))
        )
        if not country_intent:
            return None
        # "create a country history file / country tag / country file ..."
        # (including typos like "country hisoory file") is a snippet request,
        # not a brand-new country.
        if re.search(r"\bcountry\b[^.\n]{0,60}\b(?:history|file|tag)\b", low):
            return None
        name = self._extract_country_name(request, low)
        if not name:
            return None
        # A known vanilla/workspace country is a modify request, not a new country.
        known = set(COUNTRY_TAGS) | {v.lower() for v in self._load_vanilla_country_names().values()}
        ws_names, _ = self._workspace_countries()
        known |= set(ws_names)
        if name.lower() in known:
            return None
        tag = re.sub(r"[^A-Za-z0-9]", "", name)[:3].upper()
        if len(tag) < 2:
            tag = (tag + "XX")[:3]
        # Avoid colliding with vanilla/workspace tags (e.g. "Bluenada2" must
        # not reuse BLU from the existing "Bluenada" country).
        existing_tags = set(COUNTRY_TAGS.values()) | set(self._load_vanilla_country_names())
        ws_tags = self._workspace_countries()[1]
        existing_tags |= set(ws_tags)
        base = re.sub(r"[^A-Za-z0-9]", "", name).upper()
        n = 1
        while tag in existing_tags and n < 100:
            cand = (base[:2] + str(n))[:3] if n < 10 else (base[:1] + str(n))[:3]
            if cand not in existing_tags:
                tag = cand
                break
            n += 1
        return name, tag

    def _detect_transfer(self, request: str) -> tuple[str, list[int]] | None:
        """Detect "transfer/give states X, Y to TAG/Country" requests.

        Returns (target_tag, state_ids) or None.
        """
        low = request.lower()
        if "transfer" not in low and "give" not in low:
            return None
        states = _extract_states(request)
        if not states:
            m = re.search(r"\b([0-9][0-9,\s]*?)\s+(?:to|for)\b", low)
            if m:
                states = [int(n) for n in re.findall(r"\d+", m.group(1))][:50]
        if not states:
            return None
        tm = re.search(
            r"\b(?:to|for)\s+(?:the\s+)?(?:country|tag)?\s*"
            r"([A-Za-z][A-Za-z0-9 ]*?)\s*$",
            request,
        )
        if not tm:
            return None
        target = tm.group(1).strip()
        if not target:
            return None
        tag = self._resolve_country(low, request)
        if not tag and re.fullmatch(r"[A-Za-z0-9]{1,3}", target):
            tag = target.upper()
        return (tag, states) if tag else None

    def _extract_country_name(self, request: str, low: str) -> str:
        """Pull a country name out of new-country phrasings.

        Handles: "a country called X", "new country X", "make X a country",
        "turn X into a nation", names with digits (e.g. Bluenada2), and
        capitalized-name fallbacks.
        """
        # Strip a trailing "using/with/for/in states 5, 10, 25" clause so the
        # end-anchored "called X" pattern still sees the name.
        request = re.sub(
            r"\s+(?:using|for|with|in)\s+states?\s*[=:]?\s*[0-9][0-9,\s]*$",
            "", request, flags=re.IGNORECASE,
        )
        low = request.lower()
        name_chars = r"[A-Za-z0-9][A-Za-z0-9' -]*?"
        patterns = [
            # ... called/named X (end of request)
            rf"(?:called|named)\s+\"?({name_chars})\"?\s*[.,]?$",
            # "X" (quoted name)
            rf"\"({name_chars})\"",
            # new country X / new nation X
            rf"(?:new country|new nation)\s+(?:called|named\s+)?\"?({name_chars})\"?\s*$",
            # make/create/turn/form X a country / X into a nation
            rf"\b(?:make|create|add|turn|form)\s+({name_chars})\s+(?:into\s+)?(?:a|an|the)\s+(?:new\s+|independent\s+)?(?:country|nation)\b",
            # make a country X (no "called")
            rf"\b(?:make|create|add)\s+(?:a|an|the)\s+(?:new\s+|independent\s+)?(?:country|nation)\s+({name_chars})\s*$",
        ]
        for pat in patterns:
            m = re.search(pat, request)
            if m:
                return re.sub(r"\s+", " ", m.group(1)).strip().title()
        stop = {"the", "a", "an", "of", "and", "country", "nation", "new", "called",
                "named", "create", "add", "make", "turn", "form", "into", "communist",
                "fascist", "democratic", "monarchist", "neutral"}
        caps = [w for w in re.findall(r"[A-Z][a-z]+", request) if w.lower() not in stop]
        if caps:
            return caps[-1]
        return ""

    def _detect_politics(self, request: str) -> str:
        low = request.lower()
        fragments: list[str] = []
        for m in re.finditer(r"(\d+(?:\.\d+)?\s*%\s*[a-z-]+)", low):
            fragments.append(m.group(1))
        if fragments:
            return ", ".join(fragments)
        for word in ("communist", "fascist", "democratic", "monarchist", "neutrality",
                     "non-aligned", "neutral", "democracy", "fascism", "communism"):
            if word in low:
                return word
        return ""

    def plan(self, request: str) -> Plan:
        from .intents import Intent, classify

        low = request.lower()
        intent = classify(request)
        if intent in (Intent.REPAIR, Intent.MERGE, Intent.REFACTOR, Intent.EXPLAIN):
            return Plan(intent=intent.value.lower(), target_files=[],
                        steps=[{"tool": "list_directory", "args": {"path": "."}}],
                        statuses=["Inspecting project..."],
                        parsed={})
        country = self._resolve_country(low, request)
        if country is None:
            return Plan(intent="unknown_country", target_files=[], steps=[],
                        statuses=[], parsed={})
        amount = _extract_amount(low)
        effect = _extract_effect(low)
        target = _target_file(intent := _intent(low), country)

        plan = Plan(intent=intent, target_files=[target] if target else [],
                    parsed={"country_tag": country, "amount": amount, "effect": effect})

        if intent == "add_focus":
            plan.steps = [
                {"tool": "list_directory", "args": {"path": "common/national_focus"}},
                {"tool": "read_file", "args": {"path": target}},
                {"tool": "search_identifier", "args": {"name": country}},
                {"tool": "find_vanilla_examples", "args": {"query": effect}},
                {"tool": "search_documentation", "args": {"query": effect}},
                {"tool": "__generate_patch__", "args": {}},
                {"tool": "validate_code", "args": {}},
                {"tool": "validate_focus_tree", "args": {}},
                {"tool": "validate_localisation", "args": {}},
                {"tool": "show_diff", "args": {}},
                {"tool": "apply_patch", "args": {}},
            ]
            plan.statuses = [
                "Searching identifiers...",
                "Reading vanilla examples...",
                f"Reading {country} focus tree...",
                "Generating patch...",
                "Validating...",
                "Showing diff...",
                "Ready to apply.",
            ]
        elif intent == "validate":
            plan.steps = [
                {"tool": "validate_focus_tree", "args": {}},
                {"tool": "validate_events", "args": {}},
                {"tool": "validate_localisation", "args": {}},
            ]
            plan.statuses = ["Validating focus tree...", "Validating events...", "Validating localisation..."]
        elif intent in ("explain", "inspect"):
            ident = _extract_identifier(request)
            plan.steps = [
                {"tool": "search_identifier", "args": {"name": ident}},
                {"tool": "find_similar_identifier", "args": {"name": ident}},
            ]
            plan.statuses = ["Searching identifiers...", "Looking for similar identifiers..."]
            plan.parsed["identifier"] = ident
        else:  # generic add (event/decision/other) or fallback
            plan.steps = [
                {"tool": "list_directory", "args": {"path": "."}},
                {"tool": "search_identifier", "args": {"name": country}},
                {"tool": "find_vanilla_examples", "args": {"query": effect}},
            ]
            plan.statuses = ["Inspecting workspace...", "Searching identifiers...", "Reading vanilla examples..."]
        return plan

    def plan_project(self, request: str, name: str | None = None,
                     force_country: str | None = None) -> ProjectPlan:
        from .intents import Intent, classify

        low = request.lower()
        intent = classify(request)
        if intent in (Intent.MERGE, Intent.REFACTOR, Intent.EXPLAIN, Intent.REPAIR,
                      Intent.VALIDATE, Intent.SEARCH):
            feature = {"MERGE": "merge", "REFACTOR": "refactor", "EXPLAIN": "explain",
                       "REPAIR": "repair", "VALIDATE": "validate", "SEARCH": "search"}[intent.value]
            plan = ProjectPlan(name=name or feature.title(), request=request,
                               country_tag="", feature=feature)
            plan.tasks = [ProjectTask(id="single", objective=feature,
                                      dependencies=[], estimated_files=[],
                                      validator="none")]
            return plan
        transfer = self._detect_transfer(request)
        if transfer:
            ctag, states = transfer
            plan = ProjectPlan(name=name or f"Transfer States to {ctag}",
                               request=request, country_tag=ctag,
                               feature="transfer_states", selected_states=states)
            task_ids = FEATURE_TASK_SETS["transfer_states"]
            plan.tasks = [ProjectTask(
                id=tid, objective=TASK_OBJECTIVES.get(tid, tid),
                dependencies=[d for d in TASK_DEPENDENCIES.get(tid, []) if d in task_ids],
                estimated_files=TASK_FILES.get(tid, []),
                validator="proposal" if tid not in ("validate", "apply") else
                          ("project" if tid == "validate" else "none"),
            ) for tid in task_ids]
            return plan
        new_country = self._detect_new_country(request)
        if new_country:
            cname, ctag = new_country
            plan = ProjectPlan(name=name or f"New Country {cname}", request=request,
                               country_tag=ctag, feature="new_country",
                               new_country_name=cname, new_country_tag=ctag,
                               politics=self._detect_politics(request),
                               selected_states=_extract_states(request))
            task_ids = FEATURE_TASK_SETS["new_country"]
            plan.tasks = [ProjectTask(
                id=tid, objective=TASK_OBJECTIVES.get(tid, tid),
                dependencies=[d for d in TASK_DEPENDENCIES.get(tid, []) if d in task_ids],
                estimated_files=TASK_FILES.get(tid, []),
                validator="proposal" if tid not in ("validate", "apply") else
                          ("project" if tid == "validate" else "none"),
            ) for tid in task_ids]
            return plan
        country = force_country or self._resolve_country(low, request)
        if country is None:
            country = self._country_from_focus_id(request)
        remove_spec = _parse_remove_spec(request)
        if remove_spec:
            mode_label = ("Effects" if remove_spec["mode"] == "clear_effects" else "All")
            plan = ProjectPlan(name=name or f"Remove {mode_label} from {remove_spec['target']}",
                               request=request, country_tag=country or "",
                               feature="remove_content", remove_spec=remove_spec)
            task_ids = FEATURE_TASK_SETS["remove_content"]
            plan.tasks = [ProjectTask(
                id=tid, objective=TASK_OBJECTIVES.get(tid, tid),
                dependencies=[d for d in TASK_DEPENDENCIES.get(tid, []) if d in task_ids],
                estimated_files=TASK_FILES.get(tid, []),
                validator="proposal" if tid not in ("validate", "apply") else
                          ("project" if tid == "validate" else "none"),
            ) for tid in task_ids]
            return plan
        if _detect_oob(low):
            plan = ProjectPlan(name=name or f"OOB {country or 'Army'}",
                               request=request, country_tag=country or "",
                               feature="oob",
                               division_name=_oob_name(request),
                               spawn=_oob_spawn(low),
                               oob_count=_oob_count(low),
                               unit_key=_oob_unit_key(low))
            task_ids = FEATURE_TASK_SETS["oob"]
            plan.tasks = [ProjectTask(
                id=tid, objective=TASK_OBJECTIVES.get(tid, tid),
                dependencies=[d for d in TASK_DEPENDENCIES.get(tid, []) if d in task_ids],
                estimated_files=TASK_FILES.get(tid, []),
                validator="proposal" if tid not in ("validate", "apply") else
                          ("project" if tid == "validate" else "none"),
            ) for tid in task_ids]
            return plan
        if country is None:
            plan = ProjectPlan(name=name or "Unknown Country", request=request,
                               country_tag="", feature="unknown_country")
            plan.tasks = [ProjectTask(id="single", objective="unknown country",
                                      dependencies=[], estimated_files=[], validator="none")]
            return plan
        if FOCUS_EVENT_RE.search(low):
            plan = ProjectPlan(name=name or f"Add Event to {country} Focus",
                               request=request, country_tag=country,
                               feature="focus_event",
                               focus_position=_extract_focus_position(request))
            task_ids = FEATURE_TASK_SETS["focus_event"]
            plan.tasks = [ProjectTask(
                id=tid, objective=TASK_OBJECTIVES.get(tid, tid),
                dependencies=[d for d in TASK_DEPENDENCIES.get(tid, []) if d in task_ids],
                estimated_files=TASK_FILES.get(tid, []),
                validator="proposal" if tid not in ("validate", "apply") else
                          ("project" if tid == "validate" else "none"),
            ) for tid in task_ids]
            return plan
        if FOCUS_EFFECTS_RE.search(low):
            plan = ProjectPlan(name=name or f"Add Effects to {country} Focus Tree",
                               request=request, country_tag=country,
                               feature="focus_effects",
                               effect_spec=_parse_effect_spec(request))
            task_ids = FEATURE_TASK_SETS["focus_effects"]
            plan.tasks = [ProjectTask(
                id=tid, objective=TASK_OBJECTIVES.get(tid, tid),
                dependencies=[d for d in TASK_DEPENDENCIES.get(tid, []) if d in task_ids],
                estimated_files=TASK_FILES.get(tid, []),
                validator="proposal" if tid not in ("validate", "apply") else
                          ("project" if tid == "validate" else "none"),
            ) for tid in task_ids]
            return plan
        feature = _detect_feature(low)
        task_ids = list(FEATURE_TASK_SETS.get(feature, FEATURE_TASK_SETS["focus_branch"]))
        if feature in ("focus_branch", "focus_with_ideas"):
            # A plain focus tree is just the skeleton: blank rewards, no ideas,
            # no events/decisions/ai. Extra content is added only when asked.
            # focus_with_ideas includes ideas by definition.
            extras: list[str] = []
            if re.search(r"\bevents?\b", low):
                extras.append("events")
            if re.search(r"\bdecisions?\b", low):
                extras.append("decisions")
            if re.search(r"\bideas?\b", low):
                extras.append("ideas")
            if re.search(r"\bai\b", low):
                extras.append("ai_strategy")
            pos = task_ids.index("focuses") + 1
            for tid in extras:
                if tid not in task_ids:
                    task_ids.insert(pos, tid)
                    pos += 1
            ideology = next((w for w in ("communist", "fascist", "democratic", "monarchist") if w in low), "")
            base_name = f"{feature.replace('_', ' ').title()} {country}"
            plan = ProjectPlan(
                name=name or (f"{base_name} ({ideology})" if ideology else base_name),
                request=request,
                country_tag=country,
                feature=feature,
                focus_position=_extract_focus_position(request),
            )
            for tid in task_ids:
                plan.tasks.append(ProjectTask(
                    id=tid,
                    objective=TASK_OBJECTIVES.get(tid, tid),
                    dependencies=[d for d in TASK_DEPENDENCIES.get(tid, []) if d in task_ids],
                    estimated_files=TASK_FILES.get(tid, []),
                    validator="proposal" if tid not in ("validate", "apply") else
                              ("project" if tid == "validate" else "none"),
                ))
            return plan
        ideology = next((w for w in ("communist", "fascist", "democratic", "monarchist") if w in low), "")
        base_name = f"{feature.replace('_', ' ').title()} {country}"
        plan = ProjectPlan(
            name=name or (f"{base_name} ({ideology})" if ideology else base_name),
            request=request,
            country_tag=country,
            feature=feature,
        )
        for tid in task_ids:
            plan.tasks.append(ProjectTask(
                id=tid,
                objective=TASK_OBJECTIVES.get(tid, tid),
                dependencies=[d for d in TASK_DEPENDENCIES.get(tid, []) if d in task_ids],
                estimated_files=TASK_FILES.get(tid, []),
                validator="proposal" if tid not in ("validate", "apply") else
                          ("project" if tid == "validate" else "none"),
            ))
        return plan

    def _resolve_country(self, low: str, request: str) -> str | None:
        """Country tag from known words, mod workspace countries, vanilla names,
        or None if no country can be identified. Never guesses a fallback tag."""
        tag = next((t for w, t in COUNTRY_TAGS.items() if w in low), None)
        if tag:
            return tag
        ws_names, _ = self._workspace_countries()
        for name, t in ws_names.items():
            if name in low:
                return t
        vanilla_names = self._load_vanilla_country_names()
        for t, name in vanilla_names.items():
            if name.lower() in low:
                return t
        # Explicit uppercase tag in the request ("for BEL"). Skip words that
        # merely look like tags (e.g. "OOB", "SPA" in text).
        known = (set(COUNTRY_TAGS.values())
                 | set(vanilla_names)
                 | set(self._workspace_countries()[1]))
        for m in re.finditer(r"\b([A-Z]{3})\b", request):
            if m.group(1) in known:
                return m.group(1)
        stopwords = {
            "add", "create", "make", "new", "focus", "branch", "tree", "event",
            "decision", "path", "country", "nation", "communist", "fascist",
            "democratic", "monarchist", "focuses", "events", "decisions", "ideas",
            "advisors", "project", "please", "hello", "can", "you", "for", "with",
            "that", "gives", "give", "the", "a", "an", "to", "of", "and", "in",
        }
        caps = [w for w in re.findall(r"\b[A-Z][a-z]+\b", request)
                if w.lower() not in stopwords]
        # No known country matched and no obvious capitalized name: report unknown
        # instead of silently editing Germany.
        return None

    def _country_from_focus_id(self, request: str) -> str | None:
        """Country tag from a focus-id prefix, e.g. ARG_army_recruitment_bolster -> ARG."""
        m = re.search(r"\b([A-Z]{2,4})_[A-Za-z0-9_]+\b", request)
        if not m:
            return None
        tag = m.group(1)
        known = (set(COUNTRY_TAGS.values())
                 | set(self._load_vanilla_country_names())
                 | set(self._workspace_countries()[1]))
        return tag if tag in known else None

    def _extract_countries(self, request: str) -> list[str]:
        """All country tags mentioned in a request, in mention order, deduped."""
        low = request.lower()
        # (start, -name_len, end, tag) — longer names win overlapping matches.
        matches: list[tuple[int, int, int, str]] = []
        for word, tag in COUNTRY_TAGS.items():
            for m in re.finditer(r"\b" + re.escape(word) + r"\b", low):
                matches.append((m.start(), -len(word), m.end(), tag))
        ws_names, _ = self._workspace_countries()
        for name, tag in ws_names.items():
            for m in re.finditer(r"\b" + re.escape(name) + r"\b", low):
                matches.append((m.start(), -len(name), m.end(), tag))
        for tag, name in self._load_vanilla_country_names().items():
            for m in re.finditer(r"\b" + re.escape(name.lower()) + r"\b", low):
                matches.append((m.start(), -len(name), m.end(), tag))
        found: list[str] = []
        covered_end = -1
        for _start, _neg_len, end, tag in sorted(matches):
            if _start < covered_end:
                continue  # inside an already-accepted, more specific match
            if tag not in found:
                found.append(tag)
            covered_end = max(covered_end, end)
        return found


def _detect_feature(low: str) -> str:
    focus_tree_request = ("focus" in low or "branch" in low or "tree" in low)
    if "civil war" in low or "civilwar" in low:
        if focus_tree_request:
            # A focus-tree request with a civil-war branch is just a focus
            # branch with a civil-war reward — never the full package of
            # ideas/events/decisions/ai_strategy.
            return "focus_branch"
        return "civil_war"
    if "releasable" in low or ("create" in low and "nation" in low):
        return "releasable"
    if "advisor" in low or "character" in low or "minister" in low:
        return "advisors"
    if "modify" in low or "extend" in low or "expand" in low:
        return "modify_focus_tree"
    if ("focus" in low or "branch" in low or "tree" in low) \
            and ("idea" in low or "spirit" in low):
        return "focus_with_ideas"
    if "idea" in low or "spirit" in low:
        return "ideas"
    if "focus" in low or "branch" in low or "tree" in low:
        return "focus_branch"
    if "decision" in low and "event" in low:
        return "decisions_events"
    if "decision" in low:
        return "decisions"
    if "event" in low:
        return "events"
    if _detect_oob(low):
        return "oob"
    return "focus_branch"


def _detect_oob(low: str) -> bool:
    """Order-of-battle / army requests."""
    if "army experience" in low or "manpower" in low or "army xp" in low:
        return False
    if re.search(r"\b(oob|order of battle|starting army)\b", low):
        return True
    if re.search(r"\barmy\b", low) and re.search(r"\b(for|of|called)\b", low):
        return True
    if re.search(r"\b\d+\s+divisions?\b", low):
        return True
    if re.search(r"\b(division|divisions)\b", low) and \
            re.search(r"\b(add|create|make|new|oob|spawn|build)\b", low):
        return True
    return False


def _oob_name(request: str) -> str:
    """Division instance name from the request, else '' (prompt for it)."""
    m = re.search(r"\b(?:named|called)\s+[\"']?([^\"',.]+)", request, re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r"(?:name|call)(?:ed)?\s+(?:it|the\s+divisions?)\s+[\"']?([^\"',.]+)",
                  request, re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r"[\"']([^\"']{2,60})[\"']", request)
    return m.group(1).strip() if m else ""


def _oob_spawn(low: str) -> bool | None:
    """Spawn intent: True / False when explicit, None to prompt."""
    if re.search(r"\bdon'?t\s+spawn\b|\bno\s+spawn\b|\bwithout\s+spawn\b", low):
        return False
    if re.search(r"\bspawn\b", low):
        return True
    return None


def _oob_count(low: str) -> int:
    m = re.search(r"\b(\d+)\s+divisions?\b", low)
    if m:
        return max(1, int(m.group(1)))
    if re.search(r"\b(?:a|one)\s+division\b", low):
        return 1
    return 3


def _oob_unit_key(low: str) -> str:
    """Unit-type keywords from the request (e.g. 'medium tank')."""
    m = re.search(r"\b(medium|light|heavy|super[ -]?heavy)\s+(?:tank|armor|armour)\b", low)
    if m:
        return m.group(0).strip()
    for word in ("motorized", "mechanized", "mechanised", "cavalry", "infantry",
                 "marine", "mountain", "paratrooper", "tank", "artillery"):
        if word in low:
            return word
    return ""


def _intent(low: str) -> str:
    if re.search(r"\b(add|create|make|new)\b.*\bfocus\b", low) or re.search(r"\bfocus\b.*\b(add|create|make|new)\b", low):
        return "add_focus"
    if "validate" in low or "check" in low or "broken" in low or "error" in low:
        return "validate"
    if any(w in low for w in ("where is", "explain", "what does", "inspect", "find")):
        return "explain"
    if any(w in low for w in ("add", "create", "make", "new")):
        return "add_generic"
    return "explain"


def _extract_amount(low: str) -> float:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(%|pp|political power|stability|war support|manpower|experience)?", low)
    return float(m.group(1)) if m else 0.0


def _extract_effect(low: str) -> str:
    for word, effect in EFFECT_WORDS.items():
        if word in low:
            return effect
    return "add_political_power"


def _extract_identifier(request: str) -> str:
    m = re.search(r"`([A-Za-z0-9_.]+)`", request)
    if m:
        return m.group(1)
    m = re.search(r"\b([A-Z]{2,4}_[A-Za-z0-9_]+)\b", request)
    return m.group(1) if m else request.strip()[:60]


def _extract_states(request: str) -> list[int]:
    """State ids from phrasings like "using states 5, 10, 25"."""
    low = request.lower()
    m = re.search(r"(?:using|for|with|in)\s+states?\s*[=:]?\s*([0-9][0-9,\s]*)$", low)
    if not m:
        m = re.search(r"\bstates?\s+([0-9][0-9,\s]*)\b", low)
    if not m:
        return []
    return [int(n) for n in re.findall(r"\d+", m.group(1))][:50]


def _extract_focus_position(request: str) -> str:
    """Which focus an event should attach to: 'first' | 'last' | index | id."""
    low = request.lower()
    if "first" in low or "1st" in low:
        return "first"
    if "last" in low:
        return "last"
    for word, num in ORDINAL_WORDS.items():
        if re.search(rf"\b{word}\s+focus\b", low):
            return num
    m = re.search(r"\b(?:focus\s+)?(\d+)(?:st|nd|rd|th)?\b", low)
    if m:
        return m.group(1)
    m = re.search(r"\b([A-Z]{2,4}_[A-Za-z0-9_]+)\b", request)
    if m:
        return m.group(1)
    return ""


def _parse_effect_spec(request: str) -> list[dict]:
    """Parse '50 political power to each focus' / 'stability to focus 1' into
    [{"position": "first"|"last"|"N"|"all", "effect": ..., "amount": ...}]."""
    low = request.lower()
    spec: list[dict] = []
    for clause in re.split(r"\s*,\s*|\band\b", low):
        found = [(w, e) for w, e in EFFECT_WORDS.items() if w in clause]
        if not found:
            continue
        if re.search(r"\b(each|every|all)\s+focus", clause):
            positions = ["all"]
        else:
            positions = []
            if re.search(r"\b(?:first|1st)\s+focus\b", clause):
                positions.append("first")
            if re.search(r"\blast\s+focus\b", clause):
                positions.append("last")
            for word, num in ORDINAL_WORDS.items():
                if re.search(rf"\b{word}\s+focus\b", clause) and num not in positions:
                    positions.append(num)
            for m in re.finditer(r"\bfocus\s+(\d+)\b|\b(\d+)(?:st|nd|rd|th)\s+focus\b", clause):
                n = int(m.group(1) or m.group(2))
                if str(n) not in positions:
                    positions.append(str(n))
            fid = re.search(r"\b([A-Za-z]{2,4}_[A-Za-z0-9_]+)\b", clause)
            if fid:
                # Recover the original casing from the request so the id matches
                # the tree block exactly (ARG_... vs arg_...).
                orig = re.search(re.escape(fid.group(1)), request, re.IGNORECASE)
                ident = orig.group(0) if orig else fid.group(1).upper()
                positions = [ident]
            if not positions:
                positions = ["all"]
        amount: float | None = None
        pct = False
        m = re.search(
            r"(\d+(?:\.\d+)?)\s*(?:%|political power|pp\b|stability|war support|"
            r"manpower|army experience|navy experience|air experience)",
            clause,
        )
        if m:
            amount = float(m.group(1))
            pct = "%" in m.group(0)
        else:
            # "stability +10 to focus 1" / "50 to focus 2"
            m = re.search(r"\+?(\d+(?:\.\d+)?)\s*(?:to\s+)?(?:the\s+)?focus", clause)
            if m:
                amount = float(m.group(1))
        for _word, effect in found:
            for pos in positions:
                entry: dict = {"position": pos, "effect": effect, "amount": amount}
                if pct and effect in ("add_stability", "add_war_support") and amount is not None:
                    entry["amount"] = round(amount / 100.0, 4)
                elif not pct and effect in ("add_stability", "add_war_support") \
                        and amount is not None and amount >= 1:
                    # Bare numbers on stability/war support mean percentage points.
                    entry["amount"] = round(amount / 100.0, 4)
                spec.append(entry)
    return spec


def _parse_remove_spec(request: str) -> dict | None:
    """Parse 'remove effects from the decisions' / 'remove events' into
    {"target": focuses|decisions|events|ideas, "mode": clear_effects|remove_all}."""
    low = request.lower()
    if not re.search(r"\b(?:remove|clear|strip|delete|drop)\b", low):
        return None
    targets = [
        ("focuses", r"\bfocus(?:es| tree| trees)?\b"),
        ("decisions", r"\bdecision(?:s)?\b"),
        ("events", r"\bevent(?:s)?\b"),
        ("ideas", r"\b(?:national spirit(?:s)?|spirit(?:s)?|idea(?:s)?)\b"),
    ]
    best: tuple[int, str] | None = None
    for target, pattern in targets:
        m = re.search(pattern, low)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), target)
    if best is None:
        return None
    mode = "clear_effects" if re.search(r"\beffect(?:s)?\b|\breward(?:s)?\b", low) else "remove_all"
    return {"target": best[1], "mode": mode}


def _target_file(intent: str, country: str) -> str:
    if intent in ("add_focus",):
        return f"common/national_focus/{COUNTRY_FILES.get(country, country.lower() + '.txt')}"
    return ""
