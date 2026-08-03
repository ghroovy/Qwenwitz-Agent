# Owner: ACTIVE
"""Hierarchical multi-file project execution for the HOI4 coding agent.

One request becomes a dependency-ordered task graph. Every task generates
grounded content, runs the existing repair loop, updates shared project
memory, and persists state so projects can be resumed.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import CONFIG
from .planner import COUNTRY_FILES, Planner, ProjectPlan
from hoi4_agent._runtime.common import DATA_RAW  # noqa: E402

PROJECTS_DIR = CONFIG.memory_dir.parent / "projects"
BUILT_COUNTRIES_FILE = CONFIG.memory_dir / "built_countries.json"


def load_built_countries() -> list[dict]:
    """Countries this agent has created before (persisted between sessions)."""
    if not BUILT_COUNTRIES_FILE.exists():
        return []
    try:
        return json.loads(BUILT_COUNTRIES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def remember_built_country(name: str, tag: str, states: list[int] | None = None) -> None:
    entries = [e for e in load_built_countries() if e.get("tag") != tag]
    entries.append({
        "name": name,
        "tag": tag,
        "states": list(states or []),
        "created_at": time.time(),
    })
    BUILT_COUNTRIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    BUILT_COUNTRIES_FILE.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")

VERIFIED_EFFECTS = [
    "add_political_power", "add_stability", "add_war_support",
    "army_experience", "navy_experience", "air_experience",
    "add_manpower",
]
PREFERRED_MODIFIERS = [
    "political_power_gain", "stability", "war_support", "consumer_goods_factor",
    "research_speed_factor", "army_speed_factor", "navy_speed_factor",
    "air_speed_factor", "manpower", "justify_war_goal_time",
]


def _ideology_for_civil_war(low: str) -> str:
    """Ideology value used by start_civil_war, grounded in the request."""
    if any(w in low for w in ("communist", "communism")):
        return "communism"
    if any(w in low for w in ("fascist", "fascism")):
        return "fascism"
    if any(w in low for w in ("democratic", "democracy")):
        return "democratic"
    return "neutrality"


@dataclass
class ProjectMemory:
    verified_identifiers: dict[str, str] = field(default_factory=dict)
    new_ids: dict[str, list[str]] = field(default_factory=dict)
    loc_keys: list[str] = field(default_factory=list)
    focus_ids: list[str] = field(default_factory=list)
    event_ids: list[str] = field(default_factory=list)
    decision_ids: list[str] = field(default_factory=list)
    idea_ids: list[str] = field(default_factory=list)
    advisor_ids: list[str] = field(default_factory=list)
    owned_provinces: list[int] = field(default_factory=list)
    created_files: list[str] = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)
    relationships: list[dict] = field(default_factory=list)

    def next_id(self, category: str, base: str) -> str:
        existing = set(self.new_ids.get(category, []))
        n = 1
        while True:
            cand = base if n == 1 else f"{base}_{n}"
            if cand not in existing:
                existing.add(cand)
                self.new_ids.setdefault(category, []).append(cand)
                return cand
            n += 1

    def add_loc(self, *keys: str) -> None:
        for k in keys:
            if k not in self.loc_keys:
                self.loc_keys.append(k)

    def relate(self, kind: str, from_id: str, to_kind: str, to_id: str) -> None:
        self.relationships.append({"kind": kind, "from": from_id, "to_kind": to_kind, "to": to_id})

    def to_dict(self) -> dict:
        return {
            "verified_identifiers": self.verified_identifiers,
            "new_ids": self.new_ids,
            "loc_keys": self.loc_keys,
            "focus_ids": self.focus_ids,
            "event_ids": self.event_ids,
            "decision_ids": self.decision_ids,
            "idea_ids": self.idea_ids,
            "advisor_ids": self.advisor_ids,
            "owned_provinces": self.owned_provinces,
            "created_files": self.created_files,
            "modified_files": self.modified_files,
            "relationships": self.relationships,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ProjectMemory":
        m = cls()
        for k, v in data.items():
            setattr(m, k, v)
        return m


@dataclass
class Project:
    name: str
    slug: str
    request: str
    plan: ProjectPlan
    memory: ProjectMemory = field(default_factory=ProjectMemory)
    proposals: dict[str, str] = field(default_factory=dict)
    task_status: dict[str, str] = field(default_factory=dict)
    task_detail: dict[str, str] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    status: str = "pending"  # pending | running | completed | failed | aborted
    applied: bool = False
    stats: dict = field(default_factory=dict)

    def save(self) -> Path:
        PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
        path = PROJECTS_DIR / f"{self.slug}.json"
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "slug": self.slug,
            "request": self.request,
            "plan": self.plan.to_dict(),
            "memory": self.memory.to_dict(),
            "proposals": self.proposals,
            "task_status": self.task_status,
            "task_detail": self.task_detail,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status,
            "applied": self.applied,
            "stats": self.stats,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Project":
        plan_data = data["plan"]
        plan = ProjectPlan(
            name=plan_data["name"],
            request=plan_data["request"],
            country_tag=plan_data["country_tag"],
            feature=plan_data["feature"],
            new_country_name=plan_data.get("new_country_name", ""),
            new_country_tag=plan_data.get("new_country_tag", ""),
            politics=plan_data.get("politics", ""),
            selected_states=list(plan_data.get("selected_states", []) or []),
            focus_position=plan_data.get("focus_position", ""),
            effect_spec=list(plan_data.get("effect_spec", []) or []),
            remove_spec=dict(plan_data.get("remove_spec", {}) or {}),
            division_name=plan_data.get("division_name", ""),
            spawn=plan_data.get("spawn"),
            oob_count=int(plan_data.get("oob_count", 0) or 0),
            unit_key=plan_data.get("unit_key", ""),
        )
        for t in plan_data["tasks"]:
            from .planner import ProjectTask

            plan.tasks.append(ProjectTask(
                id=t["id"], objective=t["objective"], dependencies=t["dependencies"],
                estimated_files=t["estimated_files"], validator=t["validator"],
                status=t.get("status", "pending"), detail=t.get("detail", ""),
            ))
        p = cls(
            name=data["name"], slug=data["slug"], request=data["request"], plan=plan,
            memory=ProjectMemory.from_dict(data["memory"]),
            proposals=data.get("proposals", {}),
            task_status=data.get("task_status", {}),
            task_detail=data.get("task_detail", {}),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            status=data.get("status", "pending"),
            applied=data.get("applied", False),
            stats=data.get("stats", {}),
        )
        for t in plan.tasks:
            t.status = data.get("task_status", {}).get(t.id, "pending")
        return p


def load_project(slug: str) -> Project | None:
    path = PROJECTS_DIR / f"{slug}.json"
    if not path.exists():
        return None
    return Project.from_dict(json.loads(path.read_text(encoding="utf-8")))


class ProjectExecutor:
    def __init__(self, agent):
        self.agent = agent
        self.planner = Planner()
        self.validator = agent.validator
        self.index = agent.index
        self.tools = agent.tools

    # ------------------------------------------------------------- creation
    def create_project(self, request: str, name: str | None = None,
                       force_country: str | None = None) -> Project:
        plan = self.planner.plan_project(request, name=name,
                                         force_country=force_country)
        slug = re.sub(r"[^a-z0-9]+", "-", plan.name.lower()).strip("-") or "project"
        project = Project(name=plan.name, slug=slug, request=request, plan=plan)
        project.save()
        return project

    def run_multi(self, request: str, auto_approve: bool = False) -> dict:
        """Run one country-scoped feature for every country in the request."""
        from .planner import MULTI_COUNTRY_FEATURES

        countries = self.planner._extract_countries(request)
        probe = self.planner.plan_project(request)
        if len(countries) < 2 or probe.feature not in MULTI_COUNTRY_FEATURES:
            project = self.create_project(request)
            return self.run(project, auto_approve=auto_approve)
        base = probe.feature.replace("_", " ").title()
        proposals: dict[str, str] = {}
        results: dict[str, str] = {}
        failed: list[str] = []
        for tag in countries:
            project = self.create_project(request, name=f"{base} {tag}",
                                          force_country=tag)
            result = self.run(project, auto_approve=auto_approve)
            results[tag] = result["status"]
            proposals.update(project.proposals)
            if result["status"] == "failed":
                failed.append(tag)
        if self.agent is not None:
            self.agent._prepare_pending(
                proposals, label=f"Multi: {base}",
                project_slug=",".join(countries))
            pending_files = list(self.agent.pending.get("proposals", {}))
        else:
            pending_files = sorted(proposals)
        if failed:
            summary = (f"failed for {', '.join(failed)}; "
                       "review pending files from the other countries")
            status = "failed"
        elif pending_files:
            summary = ("Feature ready — " + ", ".join(pending_files[:4]) +
                       (f" and {len(pending_files) - 4} more"
                        if len(pending_files) > 4 else "") +
                       ". Review the diffs and click Accept to apply.")
            status = "pending"
        else:
            summary = "Applied for all requested countries."
            status = "completed"
        return {
            "summary": summary,
            "status": status,
            "task_status": {"countries": results},
            "pending_files": pending_files,
            "project_slug": "",
            "multi_country": list(countries),
        }

    # ------------------------------------------------------------- execution
    def run(self, project: Project, auto_approve: bool = False, max_task_attempts: int = 3) -> dict:
        project.status = "running"
        if not project.applied:
            project.task_status["apply"] = "pending"
        order = project.plan.topological_order()
        t_start = time.perf_counter()
        project.stats = {
            "repair_iterations": 0,
            "validator_failures": 0,
            "tool_calls": 0,
            "tasks_attempted": 0,
            "validator_error_types": [],
        }
        for task in order:
            if task.id in ("validate", "apply"):
                continue  # handled after all content tasks
            if project.task_status.get(task.id) == "completed":
                continue
            project.task_status[task.id] = "running"
            project.updated_at = time.time()
            project.save()
            ok, detail = self._execute_task(project, task, max_task_attempts)
            project.task_status[task.id] = "completed" if ok else "failed"
            project.task_detail[task.id] = detail
            project.updated_at = time.time()
            project.save()
        failed_tasks = [t.id for t in project.plan.tasks
                        if project.task_status.get(t.id) == "failed"]
        if failed_tasks:
            project.status = "failed"
            project.save()
            details = "; ".join(
                project.task_detail.get(t, "") for t in failed_tasks
                if project.task_detail.get(t))
            msg = f"failed task(s): {', '.join(failed_tasks)}; nothing applied"
            if details:
                msg += f" — {details}"
            return self._result(project, t_start, msg)
        # final dependency check + apply
        issues = self.check_project_dependencies(project)
        project.task_detail["validate"] = ("dependencies ok" if not issues
                                           else "; ".join(issues[:5]))
        if issues:
            project.status = "failed"
            project.save()
            return self._result(project, t_start, f"blocked by {len(issues)} dependency issue(s): "
                                                  + "; ".join(issues[:5]))
        project.task_status["validate"] = "completed"
        apply_task = project.plan.task("apply")
        if apply_task is None or (project.task_status.get("apply") == "completed" and project.applied):
            project.status = "completed" if project.applied else "pending"
            project.save()
            return self._result(project, t_start, "project completed")
        project.task_status["apply"] = "running"
        project.save()
        diff = self._all_diffs(project)
        approved = auto_approve or CONFIG.auto_approve or self.agent._ask_approval(diff)
        if approved:
            from . import patcher
            from .filesystem import read_text_keep, workspace

            for path, content in project.proposals.items():
                old = read_text_keep(workspace() / path) \
                    if (workspace() / path).exists() else ""
                if content == old:
                    continue
                d = patcher.make_diff(path, old, content)
                summary = patcher.apply_diff(d, workspace())
                project.memory.modified_files.extend(a["path"] for a in summary.get("applied", []))
            project.applied = True
            project.status = "completed"
            if project.plan.feature == "new_country":
                remember_built_country(
                    project.plan.new_country_name, project.plan.country_tag,
                    project.plan.selected_states,
                )
        else:
            project.status = "pending"
        project.task_status["apply"] = "completed" if project.applied else "failed"
        project.updated_at = time.time()
        project.save()
        return self._result(project, t_start, "applied" if project.applied else "approval declined")

    def _result(self, project: Project, t_start: float, message: str) -> dict:
        elapsed = round(time.perf_counter() - t_start, 2)
        project.stats["runtime_sec"] = elapsed
        return {
            "project": project.slug,
            "status": project.status,
            "applied": project.applied,
            "message": message,
            "stats": project.stats,
            "task_status": dict(project.task_status),
            "human_intervention": (
                0 if project.applied else
                (1 if project.status == "pending" else len([t for t in project.task_status.values() if t == "failed"]))
            ),
        }

    # ---------------------------------------------------------------- tasks
    def _execute_task(self, project: Project, task, max_task_attempts: int) -> tuple[bool, str]:
        last_error = ""
        for attempt in range(1, max_task_attempts + 1):
            project.stats["tasks_attempted"] += 1
            try:
                gen = self._task_generator(project, task.id)
                new_proposals = gen()
            except Exception as exc:  # noqa: BLE001 - record and retry
                last_error = f"generator error: {type(exc).__name__}: {exc}"
                continue
            if new_proposals is None:
                task.detail = "generator produced nothing"
                break
            before = dict(project.proposals)
            self._merge_proposals(project, new_proposals, task_id=task.id)
            valid, repair_log = self._validate_and_repair(project)
            project.stats["repair_iterations"] += len(repair_log)
            project.stats["validator_failures"] += sum(1 for r in repair_log if not r.success)
            for r in repair_log:
                project.stats["validator_error_types"].extend(
                    e.get("type", "unknown") for e in r.validator_errors)
            if valid:
                project.task_detail[task.id] = f"valid on task attempt {attempt}"
                project.save()
                return True, f"completed in {attempt} task attempt(s)"
            if repair_log:
                last_error = "; ".join(
                    f"[{e.get('type')}] {e.get('message', '')}"
                    for e in repair_log[-1].validator_errors[:5])
            project.proposals = before  # revert this task's changes
        detail = f"failed after {max_task_attempts} task attempt(s)"
        if last_error:
            detail += f" — {last_error}"
        return False, detail

    def _validate_and_repair(self, project: Project) -> tuple[bool, list]:
        from .repair import RepairEngine

        engine = RepairEngine(self.agent.ctx, self.validator, self.tools, agent=self.agent)
        proposals, validation, log = engine.run_repair_loop(project.proposals, max_attempts=5)
        project.proposals = proposals
        return validation["valid"], log

    def _merge_proposals(self, project: Project, new_proposals: dict[str, str], task_id: str = "") -> None:
        for path, content in new_proposals.items():
            existing = project.proposals.get(path)
            replace = task_id == "localisation" and "localisation" in path
            if existing is not None and path not in project.memory.created_files and not replace:
                project.proposals[path] = existing.rstrip("\n") + "\n\n" + content.strip() + "\n"
            else:
                project.proposals[path] = content
                if path not in project.memory.created_files:
                    project.memory.created_files.append(path)

    def _all_diffs(self, project: Project) -> str:
        from . import patcher
        from .filesystem import read_text_keep, workspace

        diffs = []
        for path, content in project.proposals.items():
            old = read_text_keep(workspace() / path) \
                if (workspace() / path).exists() else ""
            if content != old:
                diffs.append(patcher.make_diff(path, old, content))
        return "\n".join(d for d in diffs if d.strip())

    # ------------------------------------------------------ dependency checks
    def check_project_dependencies(self, project: Project) -> list[str]:
        """Cross-file reference checks. No dangling references allowed."""
        issues: list[str] = []
        mem = project.memory
        if project.plan.feature in ("focus_event", "focus_effects", "remove_content"):
            # Surgical edit: only verify the event references introduced by
            # this operation. Pre-existing dangling refs in the edited tree
            # (e.g. idea ids from an earlier run) are out of scope here.
            vanilla_events = set(self.index.categories().get("events", {}))
            for path, content in project.proposals.items():
                for m in EVENT_REF.finditer(content):
                    if m.group(1) not in vanilla_events and m.group(1) not in mem.event_ids:
                        issues.append(f"`{path}` references missing event {m.group(1)}")
            return issues
        vanilla_focuses = set(self.index.categories().get("focuses", {}))
        vanilla_events = set(self.index.categories().get("events", {}))
        vanilla_ideas = set(self.index.categories().get("ideas", {}))
        vanilla_loc = set(self.index.categories().get("localisation", {}))
        self.validator._ensure_icons()
        icons = self.validator.icon_set
        focus_ids = set(mem.focus_ids)
        event_ids = set(mem.event_ids)
        idea_ids = set(mem.idea_ids)
        loc_keys = set(mem.loc_keys) | vanilla_loc

        for path, content in project.proposals.items():
            for tok in PREREQ_FOCUS.findall(content):
                if tok not in vanilla_focuses and tok not in focus_ids:
                    issues.append(f"focus `{path}` references missing focus {tok}")
            for m in EVENT_REF.finditer(content):
                if m.group(1) not in vanilla_events and m.group(1) not in event_ids:
                    issues.append(f"focus `{path}` references missing event {m.group(1)}")
            for m in IDEA_LIST.finditer(content):
                for tok in m.group(1).split():
                    if tok in {"idea", "days", "months", "value", "name", "="} or tok.isdigit():
                        continue
                    if tok not in vanilla_ideas and tok not in idea_ids:
                        issues.append(f"`{path}` references missing idea {tok}")
            for m in ADVISOR_IDEA.finditer(content):
                if m.group(1) not in vanilla_ideas and m.group(1) not in idea_ids:
                    issues.append(f"character `{path}` references missing idea {m.group(1)}")
            for m in ICON_REF.finditer(content):
                val = m.group(1)
                if val.startswith("GFX_") and val not in icons:
                    issues.append(f"`{path}` references unknown icon {val}")
        for ident in focus_ids | event_ids | set(mem.decision_ids) | idea_ids | set(mem.advisor_ids):
            needed = [ident] if ident in focus_ids else (
                [ident + ".t", ident + ".d"] if ident in event_ids else [ident, ident + "_desc"])
            for n in needed:
                if n not in loc_keys:
                    issues.append(f"missing localisation for {ident} (key {n})")
        return issues

    # ----------------------------------------------------------- generators
    def _task_generator(self, project: Project, task_id: str):
        feature = project.plan.feature
        tag = project.plan.country_tag
        slug = re.sub(r"[^a-z0-9_]+", "_", project.plan.name.lower()).strip("_")
        if task_id == "ideas":
            return lambda: self._gen_ideas(project, tag, slug, feature)
        if task_id == "focuses":
            return lambda: self._gen_focuses(project, tag, slug, feature)
        if task_id == "events":
            return lambda: self._gen_events(project, tag, slug)
        if task_id == "decisions":
            return lambda: self._gen_decisions(project, tag, slug)
        if task_id == "characters":
            return lambda: self._gen_characters(project, tag, slug)
        if task_id == "ai_strategy":
            return lambda: self._gen_ai_strategy(project, tag, slug)
        if task_id == "references":
            return lambda: self._gen_references(project, tag)
        if task_id == "country_tag":
            return lambda: self._gen_country_tag(project, tag)
        if task_id == "country_files":
            return lambda: self._gen_country_files(project, tag)
        if task_id == "oob":
            return lambda: self._gen_oob(project, tag)
        if task_id == "state_transfer":
            return lambda: self._gen_state_transfer(project, tag)
        if task_id == "focus_event":
            return lambda: self._gen_focus_event(project, tag, slug)
        if task_id == "focus_effects":
            return lambda: self._gen_focus_effects(project, tag, slug)
        if task_id == "remove_content":
            return lambda: self._gen_remove_content(project, tag, slug)
        if task_id == "localisation":
            return lambda: self._gen_localisation(project, tag)
        return lambda: None

    def _gen_country_tag(self, project, tag) -> dict:
        name = project.plan.new_country_name
        file = f"common/country_tags/{tag}.txt"
        return {file: f'{tag} = "countries/{name}.txt"\n'}

    def _gen_country_files(self, project, tag) -> dict:
        name = project.plan.new_country_name
        proposals: dict[str, str] = {}
        proposals[f"common/countries/{name}.txt"] = self._country_style(tag)
        first_idea = project.memory.idea_ids[0] if project.memory.idea_ids else ""
        from .politics import parse_politics

        selected = list(project.plan.selected_states or [])
        if selected:
            provinces, capital = self._selected_state_territory(selected)
            project.memory.owned_provinces = provinces
            proposals.update(self._state_override_files(selected, tag, name))
            capital_line = f"capital = {capital}\n"
        else:
            # No territory selected: build the country without a synthetic
            # state. Land is assigned later through map state transfer.
            project.memory.owned_provinces = []
            capital_line = "capital = 1 # placeholder — transfer states on the map to set real territory\n"
        politics = parse_politics(project.plan.politics)
        parties = " ".join(f"{k} = {v:.2f}" for k, v in politics["parties"].items())
        from hoi4_agent.config import mod_start_year

        _year = mod_start_year()
        history = (
            capital_line
            + "set_politics = {\n"
            f"\truling_party = {politics['ruling_party']}\n"
            f'\tlast_election = "{_year}.1.1"\n'
            "\telection_frequency = 48\n"
            "\telections_allowed = yes\n"
            f"\tparties = {{ {parties} }}\n"
            "}\n"
            + (f"add_ideas = {{ {first_idea} }}\n" if first_idea else "")
            + "set_technology = {\n"
            "\tinfantry_weapons = 1\n"
            "\ttech_engineers = 1\n"
            "\ttech_support = 1\n"
            "}\n"
            + (f'set_oob = "{tag}_{_year}"\n' if project.memory.owned_provinces else "")
            + "focus_tree = {\n"
            "\tcountry = {\n"
            "\t\tfactor = 0\n"
            "\t\tmodifier = {\n"
            "\t\t\tadd = 10\n"
            f"\t\t\ttag = {tag}\n"
            "\t\t}\n"
            "\t}\n"
            "}\n"
        )
        proposals[f"history/countries/{tag} - {name}.txt"] = history
        return proposals

    def _selected_state_territory(self, state_ids: list[int]) -> tuple[list[int], int]:
        """Provinces + capital for a set of real state ids (vanilla + workspace)."""
        from .preview.map_preview import _load

        d = _load()
        provinces: list[int] = []
        for sid in state_ids:
            info = d.states.get(int(sid))
            if info:
                provinces.extend(info["provinces"])
        provinces = sorted(set(provinces))
        if not provinces:
            raise ValueError(f"none of the selected states exist in the map: {state_ids}")
        return provinces, int(state_ids[0])

    def _state_override_files(self, state_ids: list[int], tag: str, name: str) -> dict[str, str]:
        """history/states/{sid}-{Tag}.txt overrides that transfer ownership.

        Copies the full vanilla (or workspace) state block and replaces the
        owner, so the new country receives the selected states as cores.
        """
        from .filesystem import workspace
        from .preview import raw_game_dir

        proposals: dict[str, str] = {}
        safe_name = re.sub(r"[^A-Za-z]", "", name)
        for sid in state_ids:
            source = None
            ws_dir = workspace() / "history" / "states"
            if ws_dir.exists():
                for f in ws_dir.glob("*.txt"):
                    if f.name.startswith(f"{sid}-") or f.name.startswith(f"{sid} "):
                        source = f
                        break
            if source is None:
                vanilla_dir = raw_game_dir() / "history" / "states"
                if vanilla_dir.exists():
                    for f in vanilla_dir.glob("*.txt"):
                        if f.name.startswith(f"{sid}-") or f.name.startswith(f"{sid} "):
                            source = f
                            break
            if source is None:
                continue
            text = source.read_text(encoding="utf-8", errors="ignore")
            block = self._state_block(text, int(sid))
            if not block:
                continue
            # Replace owner and ensure the new country holds a core.
            block = re.sub(r"(\bowner\s*=\s*)[A-Z0-9]{1,3}", rf"\g<1>{tag}", block)
            if re.search(rf"\badd_core_of\s*=\s*{tag}\b", block) is None:
                block = re.sub(r"(\n\s*)(owner\s*=\s*[A-Z0-9]{1,3})",
                               rf"\g<1>\g<2>\n\g<1>add_core_of = {tag}", block, count=1)
            proposals[f"history/states/{sid}-{safe_name}.txt"] = block + "\n"
        return proposals

    @staticmethod
    def _state_block(text: str, state_id: int) -> str:
        """Extract the full `state = { ... }` block for a state id."""
        pattern = re.compile(r"state\s*=\s*\{")
        for m in pattern.finditer(text):
            start = m.end()
            depth, i = 1, start
            while i < len(text) and depth > 0:
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                i += 1
            block = text[m.start():i]
            if re.search(rf"\bid\s*=\s*{state_id}\b", block):
                return block
        return ""

    def _gen_oob(self, project, tag) -> dict:
        """Full order-of-battle generator, grounded in the vanilla
        history/units structure: division_template blocks (regiments/support
        grid) plus an optional `units = { division = {...} }` spawn block.

        Unit names come from the mod workspace first (common/units sub-units
        and division templates), vanilla only as a fallback. Spawn locations
        are the country's victory-point provinces, sorted by VP value."""
        from .filesystem import read_text_keep, workspace
        from hoi4_agent.config import mod_start_year

        year = mod_start_year()
        unit_key = (project.plan.unit_key or "").strip()
        matched = self._match_sub_unit(unit_key)
        template = self._match_template(matched, unit_key, tag)
        n = max(1, int(project.plan.oob_count or 3))
        div_name = (project.plan.division_name or "").strip() or "Infantry Division"
        template_name = template["name"] if template else \
            self._oob_template_name(unit_key, matched)
        file = f"history/units/{tag}_{year}.txt"

        parts: list[str] = []
        if template is None:
            regs, supp = self._template_grid(matched)
            parts.append(self._render_template(template_name, regs, supp))
        spawn = project.plan.spawn
        owned = list(project.memory.owned_provinces or [])
        if spawn is None and owned:
            # New-country path: the country's new states are its territory;
            # a starting army is expected even without an explicit "spawn".
            spawn = True
        if spawn:
            vps = self._oob_vps(tag)
            locs = vps or owned
            if locs:
                parts.append(self._render_units(n, div_name, template_name, locs))
            else:
                project.task_detail["oob"] = (
                    f"spawn requested but no locations found for {tag}; "
                    "units block omitted")
        content = "\n\n".join(parts) + "\n"
        if not content.strip():
            return {}
        existing = workspace() / file
        if existing.exists():
            content = read_text_keep(existing).rstrip("\n") + "\n\n" + content
        return {file: content}

    # ------------------------------------------------------------- OOB
    def _sub_unit_vocab(self) -> list[str]:
        """Battalion (sub-unit) keys from the mod's common/units, falling back
        to vanilla common/units only when the mod has no such folder."""
        from .filesystem import workspace

        roots = [workspace() / "common" / "units"]
        if not roots[0].exists():
            vanilla = CONFIG.vanilla_root / "common" / "units"
            if vanilla.exists():
                roots = [vanilla]
        vocab: list[str] = []
        for root in roots:
            if not root.exists():
                continue
            for f in sorted(root.glob("*.txt")):
                try:
                    text = f.read_text(encoding="utf-8", errors="surrogateescape")
                except OSError:
                    continue
                m = re.search(r"sub_units\s*=\s*\{", text)
                if not m:
                    continue
                vocab.extend(km.group(1) for km in re.finditer(
                    r"^\t([a-z0-9_]+)\s*=\s*\{", text[m.start():], re.M))
        return sorted(set(vocab))

    def _match_sub_unit(self, unit_key: str) -> str:
        vocab = self._sub_unit_vocab()
        if not vocab:
            return "infantry"
        if not unit_key:
            return "infantry" if "infantry" in vocab else vocab[0]
        tokens = [t for t in re.findall(r"[a-z0-9]+", unit_key.lower())
                  if t not in ("a", "an", "the", "of", "for")]
        best, best_score = vocab[0], 0
        for key in vocab:
            score = sum(1 for t in tokens if t in key)
            if score > best_score:
                best, best_score = key, score
        return best

    def _template_blocks(self, roots: list) -> list[dict]:
        """division_template blocks: {name, text, subunits}."""
        out: list[dict] = []
        for root in roots:
            if not root.exists():
                continue
            for f in sorted(root.glob("*.txt")):
                try:
                    text = f.read_text(encoding="utf-8", errors="surrogateescape")
                except OSError:
                    continue
                for m in re.finditer(r"division_template\s*=\s*\{", text):
                    start = m.start()
                    depth, i = 1, m.end()
                    while i < len(text) and depth:
                        if text[i] == "{":
                            depth += 1
                        elif text[i] == "}":
                            depth -= 1
                        i += 1
                    block = text[start:i]
                    nm = re.search(r'\bname\s*=\s*"([^"]+)"', block)
                    subs = set(re.findall(r"^\t{2}([a-z0-9_]+)\s*=\s*\{", block, re.M))
                    out.append({"name": nm.group(1) if nm else "",
                                "text": block, "subunits": subs,
                                "file": f.name})
        return out

    def _match_template(self, matched_sub: str, unit_key: str,
                        tag: str = "") -> dict | None:
        """Best division template from the mod (common/units + history/units),
        preferring the requested country's own OOB file, and falling back to
        vanilla templates only when the mod has no common/units folder."""
        from .filesystem import workspace

        mod = workspace()
        roots = [mod / "common" / "units", mod / "history" / "units"]
        candidates: list[dict] = []
        for root in roots:
            for t in self._template_blocks([root]):
                if matched_sub in t["subunits"]:
                    candidates.append(t)
                elif unit_key and all(w in t["name"].lower()
                                      for w in re.findall(r"[a-z0-9]+", unit_key.lower())
                                      if w not in ("a", "an", "the", "of", "for")):
                    candidates.append(t)
        if candidates:
            # Prefer templates from the requested country's own OOB file.
            country_file = f"{tag}_" if tag else ""
            for t in candidates:
                if country_file and country_file in t.get("file", ""):
                    return t
            return candidates[0]
        if not (mod / "common" / "units").exists():
            vanilla = CONFIG.vanilla_root / "history" / "units"
            for t in self._template_blocks([vanilla]):
                if matched_sub in t["subunits"]:
                    return t
        return None

    def _oob_template_name(self, unit_key: str, matched_sub: str) -> str:
        if unit_key:
            words = [w for w in re.findall(r"[a-zA-Z0-9]+", unit_key)
                     if w.lower() not in ("a", "an", "the", "of", "for")]
            if words:
                return " ".join(w.capitalize() for w in words) + " Division"
        if matched_sub and "armor" in matched_sub:
            return "Armor Division"
        return "Infantry Division"

    def _template_grid(self, matched_sub: str) -> tuple[list[str], list[str]]:
        """Regiments/support grid matching vanilla layouts. Only uses
        sub-units that exist in the mod's vocabulary."""
        vocab = set(self._sub_unit_vocab())
        armorish = "armor" in matched_sub or "tank" in matched_sub
        if armorish:
            regs = [f"{matched_sub} = {{ x = {x} y = {y} }}"
                    for y in range(2) for x in range(2)]
            if "motorized" in vocab:
                regs += ["motorized = { x = 2 y = 0 }",
                         "motorized = { x = 2 y = 1 }"]
            else:
                regs += [f"{matched_sub} = {{ x = 2 y = 0 }}",
                         f"{matched_sub} = {{ x = 2 y = 1 }}"]
            pref_supp = ["mot_recon", "engineer", "artillery"]
        else:
            regs = [f"{matched_sub} = {{ x = {x} y = {y} }}"
                    for y in range(3) for x in range(3)]
            pref_supp = ["engineer", "artillery"]
        supp = [f"{s} = {{ x = 0 y = {i} }}" for i, s in enumerate(pref_supp)
                if s in vocab]
        return regs, supp

    def _render_template(self, name: str, regs: list[str], supp: list[str]) -> str:
        lines = ["division_template = {", f'\tname = "{name}"', "\tregiments = {"]
        lines += ["\t\t" + r for r in regs]
        lines.append("\t}")
        if supp:
            lines.append("\tsupport = {")
            lines += ["\t\t" + s for s in supp]
            lines.append("\t}")
        lines.append("}")
        return "\n".join(lines)

    def _oob_vps(self, tag: str) -> list[int]:
        """Victory-point provinces of the country, from the mod's
        history/states (fallback vanilla only when the mod lacks files for
        the tag), sorted by VP value descending."""
        from .filesystem import workspace

        vps: list[tuple[int, int]] = []
        mod_states = workspace() / "history" / "states"
        roots = [mod_states]
        if not mod_states.exists():
            roots = [CONFIG.vanilla_root / "history" / "states"]
        found_owner = False
        for root in roots:
            if not root.exists():
                continue
            for f in sorted(root.glob("*.txt")):
                try:
                    text = f.read_text(encoding="utf-8", errors="surrogateescape")
                except OSError:
                    continue
                if f"owner = {tag}" not in text:
                    continue
                found_owner = True
                for m in re.finditer(r"victory_points\s*=\s*\{([^}]*)\}", text, re.S):
                    for pm in re.finditer(r"(\d+)\s+(\d+)", m.group(1)):
                        vps.append((int(pm.group(1)), int(pm.group(2))))
            if found_owner:
                break
        if not found_owner and mod_states.exists():
            # The mod has its own history/states but no files for this tag:
            # per the grounding rule, look in vanilla for the country.
            for f in sorted((CONFIG.vanilla_root / "history" / "states").glob("*.txt")):
                try:
                    text = f.read_text(encoding="utf-8", errors="surrogateescape")
                except OSError:
                    continue
                if f"owner = {tag}" not in text:
                    continue
                for m in re.finditer(r"victory_points\s*=\s*\{([^}]*)\}", text, re.S):
                    for pm in re.finditer(r"(\d+)\s+(\d+)", m.group(1)):
                        vps.append((int(pm.group(1)), int(pm.group(2))))
        vps.sort(key=lambda kv: -kv[1])
        return [p for p, _ in vps]

    def _render_units(self, n: int, div_name: str, template_name: str,
                      vps: list[int]) -> str:
        lines = ["units = {"]
        for i in range(n):
            loc = vps[i % len(vps)]
            name = div_name if n == 1 else f"{i + 1}. {div_name}"
            lines += ["\tdivision = {",
                      f'\t\tname = "{name}"',
                      f"\t\tlocation = {loc}",
                      f'\t\tdivision_template = "{template_name}"',
                      "\t\tstart_experience_factor = 0.1",
                      "\t\tstart_equipment_factor = 0.3",
                      "\t}"]
        lines.append("}")
        return "\n".join(lines)

    def _gen_state_transfer(self, project, tag) -> dict:
        """Transfer the selected states to an existing country/tag."""
        selected = list(project.plan.selected_states or [])
        if not selected:
            return {}
        from .preview.map_preview import _load

        d = _load()
        missing = [s for s in selected if s not in d.states]
        if missing:
            raise ValueError(f"selected states do not exist on the map: {missing}")
        _, ws_tags = self.planner._workspace_countries()
        known = set(self.index.categories().get("countries", {})) | set(ws_tags)
        if tag not in known:
            raise ValueError(f"target country tag not found in vanilla or workspace: {tag}")
        provinces, _ = self._selected_state_territory(selected)
        project.memory.owned_provinces = provinces
        return self._state_override_files(selected, tag, tag)

    def _gen_focus_event(self, project, tag, slug) -> dict:
        """Add an event to a focus in the country's focus tree.

        If an agent focus file (or a vanilla tree for the tag) exists, the event
        reference is inserted into the requested focus of that tree. Otherwise a
        fresh branch is created with the event on that focus.
        """
        from .filesystem import workspace
        from .preview import raw_game_dir

        agent_file = workspace() / "common" / "national_focus" / f"{tag.lower()}_agent_focus.txt"
        vanilla_file = self._vanilla_focus_file_for_tag(tag)
        target = agent_file if agent_file.exists() else vanilla_file
        if target is not None:
            text = target.read_text(encoding="utf-8", errors="surrogateescape")
            blocks = self._focus_blocks(text)
            if blocks:
                idx = self._resolve_focus_index(project, blocks)
                fid, start, end = blocks[idx]
                # Keep the new event id unique against events already referenced.
                for m in EVENT_REF.finditer(text):
                    project.memory.new_ids.setdefault("event", []).append(m.group(1))
                eid = project.memory.next_id("event", f"{tag}_{slug}_agent_event_{idx + 1:02d}")
                project.memory.event_ids.append(eid)
                project.memory.add_loc(f"{eid}.t", f"{eid}.d", f"{eid}.a")
                # Register existing ids so dependency checks don't flag them.
                for bfid, *_ in blocks:
                    if bfid not in project.memory.focus_ids:
                        project.memory.focus_ids.append(bfid)
                    project.memory.add_loc(bfid, bfid + "_desc")
                for m in EVENT_REF.finditer(text):
                    rid = m.group(1)
                    if rid not in project.memory.event_ids:
                        project.memory.event_ids.append(rid)
                for m in IDEA_LIST.finditer(text):
                    for tok in m.group(1).split():
                        if tok not in project.memory.idea_ids:
                            project.memory.idea_ids.append(tok)
                self._register_loc_for_ids(project, text)
                new_text = self._insert_event_into_focus(text, start, end, eid)
                if target.is_relative_to(workspace()):
                    rel = target.relative_to(workspace()).as_posix()
                else:
                    # Vanilla tree: create a workspace override at the same path.
                    rel = target.relative_to(raw_game_dir()).as_posix()
                proposals: dict[str, str] = {rel: new_text}
                events_rel = f"events/{tag.lower()}_agent_events.txt"
                existing_events = workspace() / events_rel
                new_event = self._event_content(project, tag, eid, idx).strip()
                if existing_events.exists():
                    # Merge into the existing file so previously defined events
                    # (still referenced by the tree) are preserved.
                    old_events = existing_events.read_text(
                        encoding="utf-8", errors="surrogateescape")
                    proposals[events_rel] = old_events.rstrip("\n") + "\n\n" + new_event + "\n"
                else:
                    proposals[events_rel] = new_event + "\n"
                self._add_referenced_passthrough(proposals, text)
                return proposals
        # No existing tree: create a fresh branch with the event on the
        # requested focus position.
        proposals = self._gen_focuses(project, tag, slug, "focus_branch")
        proposals.update(self._gen_events(project, tag, slug))
        return proposals

    @staticmethod
    def _focus_blocks(text: str) -> list[tuple[str, int, int]]:
        """Ordered (focus_id, block_start, block_end) list for a focus file."""
        out: list[tuple[str, int, int]] = []
        for m in re.finditer(r"(^|\n)\s*focus\s*=\s*\{", text):
            start = m.start()
            depth, i = 1, m.end()
            while i < len(text) and depth > 0:
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                i += 1
            block = text[start:i]
            idm = re.search(r"\bid\s*=\s*([A-Za-z0-9_]+)", block)
            if idm:
                out.append((idm.group(1), start, i))
        return out

    def _resolve_focus_index(self, project, blocks: list[tuple[str, int, int]]) -> int:
        pos = project.plan.focus_position
        if pos and pos not in ("first", "last") and not pos.isdigit():
            ids = {b[0] for b in blocks}
            if pos not in ids:
                fid = self._match_focus_name(project.plan.request, blocks)
                if fid:
                    return next(i for i, b in enumerate(blocks) if b[0] == fid)
                raise ValueError(f"focus `{pos}` not found in the target tree")
        return self._resolve_focus_index_from_pos(project.plan.focus_position, blocks)

    @staticmethod
    def _focus_readable_name(fid: str) -> str:
        """ARG_army_recruitment_bolster -> 'army recruitment bolster'."""
        m = re.match(r"^[A-Z]{2,4}_(.+)$", fid)
        core = m.group(1) if m else fid
        return core.replace("_", " ").strip().lower()

    def _match_focus_name(self, request: str, blocks) -> str | None:
        """Longest focus-id readable name mentioned in the request."""
        request_low = request.lower()
        best: tuple[str, str] | None = None
        for fid, *_ in blocks:
            name = self._focus_readable_name(fid)
            if name and name in request_low and (best is None or len(name) > len(best[0])):
                best = (name, fid)
        return best[1] if best else None

    def _resolve_focus_index_from_pos(self, pos: str, blocks: list[tuple[str, int, int]]) -> int:
        n = len(blocks)
        if not pos:
            return n - 1
        if pos == "first":
            return 0
        if pos == "last":
            return n - 1
        if pos.isdigit():
            return min(max(int(pos) - 1, 0), n - 1)
        for i, (bfid, *_ ) in enumerate(blocks):
            if bfid == pos:
                return i
        raise ValueError(f"focus `{pos}` not found in the target tree")

    def _gen_focus_effects(self, project, tag, slug) -> dict:
        """Add completion-reward effects to focuses in the country's tree."""
        spec = list(project.plan.effect_spec or [])
        if not spec:
            raise ValueError("no effects specified; ask like '50 political power to each focus'")
        from .filesystem import workspace
        from .preview import raw_game_dir

        agent_file = workspace() / "common" / "national_focus" / f"{tag.lower()}_agent_focus.txt"
        vanilla_file = self._vanilla_focus_file_for_tag(tag)
        target = agent_file if agent_file.exists() else vanilla_file
        if target is not None:
            text = target.read_text(encoding="utf-8", errors="surrogateescape")
            blocks = self._focus_blocks(text)
            if blocks:
                spec = self._resolve_named_focus_spec(project, spec, blocks)
                self._raise_if_named_focus_unresolved(project, spec)
                new_text = self._apply_effects_to_tree(text, blocks, spec)
                for bfid, *_ in blocks:
                    if bfid not in project.memory.focus_ids:
                        project.memory.focus_ids.append(bfid)
                    project.memory.add_loc(bfid, bfid + "_desc")
                for m in IDEA_LIST.finditer(text):
                    for tok in m.group(1).split():
                        if tok not in project.memory.idea_ids:
                            project.memory.idea_ids.append(tok)
                self._register_loc_for_ids(project, text)
                if target.is_relative_to(workspace()):
                    rel = target.relative_to(workspace()).as_posix()
                else:
                    rel = target.relative_to(raw_game_dir()).as_posix()
                proposals: dict[str, str] = {rel: new_text}
                self._add_referenced_passthrough(proposals, text)
                return proposals
        # No existing tree: create a fresh branch, then apply the effects.
        proposals = self._gen_focuses(project, tag, slug, "focus_branch")
        tree_path = next(iter(proposals))
        text = proposals[tree_path]
        blocks = self._focus_blocks(text)
        spec = self._resolve_named_focus_spec(project, spec, blocks)
        self._raise_if_named_focus_unresolved(project, spec)
        proposals[tree_path] = self._apply_effects_to_tree(text, blocks, spec)
        return proposals

    def _gen_remove_content(self, project, tag, slug) -> dict:
        """Remove content (focuses/decisions/events/ideas) or clear its effects."""
        from .filesystem import workspace
        from .preview import raw_game_dir

        spec = project.plan.remove_spec or {}
        target = spec.get("target", "focuses")
        mode = spec.get("mode", "clear_effects")
        path_map = {
            "focuses": f"common/national_focus/{tag.lower()}_agent_focus.txt",
            "decisions": f"common/decisions/{tag}_agent.txt",
            "events": f"events/{tag.lower()}_agent_events.txt",
            "ideas": f"common/ideas/{tag.lower()}_agent_ideas.txt",
        }
        rel = path_map[target]
        full = workspace() / rel
        if not full.exists():
            # Nothing to remove in this workspace — vanilla files are never
            # edited, so a missing workspace file means "no changes".
            return {}
        text = full.read_text(encoding="utf-8", errors="surrogateescape")

        if mode == "remove_all":
            ids = self._ids_in_content(target, text)
            proposals: dict[str, str] = {rel: ""}  # empty proposal == delete
            loc = self._strip_loc_keys(project, tag, target, ids)
            if loc is not None:
                proposals[loc[0]] = loc[1]
            return proposals

        if target == "focuses":
            new_text = self._clear_reward_blocks(text, "completion_reward")
        elif target == "decisions":
            new_text = self._clear_reward_blocks(text, "complete_effect")
        elif target == "events":
            new_text = self._clear_event_option_effects(text)
        else:
            new_text = self._clear_reward_blocks(text, "modifier")
        proposals = {rel: new_text}
        if target == "focuses":
            for bfid, *_ in self._focus_blocks(text):
                if bfid not in project.memory.focus_ids:
                    project.memory.focus_ids.append(bfid)
                project.memory.add_loc(bfid, bfid + "_desc")
        self._register_loc_for_ids(project, text)
        self._add_referenced_passthrough(proposals, text)
        return proposals

    @staticmethod
    def _clear_reward_blocks(text: str, key: str) -> str:
        """Empty every `key = { ... }` block (completion_reward, complete_effect,
        modifier) while keeping the block itself."""
        matches = list(re.finditer(re.escape(key) + r"\s*=\s*\{", text))
        for m in reversed(matches):
            cr_start = m.end()
            depth, i = 1, cr_start
            while i < len(text) and depth > 0:
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                i += 1
            text = text[:cr_start] + "\n\t\t" + text[i - 1:]
        return text

    @staticmethod
    def _clear_event_option_effects(text: str) -> str:
        """Reduce every event option to its name (effects removed)."""
        opts = list(re.finditer(r"(^|\n)\s*option\s*=\s*\{", text))
        for m in reversed(opts):
            start = m.start()
            depth, i = 1, m.end()
            while i < len(text) and depth > 0:
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                i += 1
            block = text[start:i]
            indent = re.match(r"^(\s*)", block).group(1)
            name = re.search(r"^\s*name\s*=\s*(\S+)", block, re.M)
            lines = [f"{indent}option = {{"]
            if name:
                lines.append(f"{indent}\tname = {name.group(1)}")
            lines.append(f"{indent}\t}}")
            text = text[:start] + "\n".join(lines) + text[i:]
        return text

    @staticmethod
    def _ids_in_content(target: str, text: str) -> list[str]:
        if target == "focuses":
            return [fid for fid, *_ in ProjectExecutor._focus_blocks(text)]
        if target == "events":
            return re.findall(r"\bid\s*=\s*([A-Za-z0-9_.]+)", text)
        if target == "decisions":
            return [g[1] for g in re.findall(
                r"(^|\n)\t([A-Z][A-Za-z0-9_]+)\s*=\s*\{", text)]
        return [g[1] for g in re.findall(
            r"(^|\n)\t\t([A-Z][A-Za-z0-9_]+)\s*=\s*\{", text)]

    def _strip_loc_keys(self, project, tag: str, target: str,
                        ids: list[str]) -> tuple[str, str] | None:
        """Remove the localisation keys of removed ids from the country's .yml."""
        from .filesystem import workspace

        rel = f"localisation/english/{tag.lower()}_l_english.yml"
        full = workspace() / rel
        if not full.exists():
            return None
        text = full.read_text(encoding="utf-8-sig", errors="surrogateescape")
        keys: set[str] = set()
        for ident in ids:
            if target == "events":
                keys.update((ident + ".t", ident + ".d", ident + ".a"))
            else:
                keys.update((ident, ident + "_desc"))
        kept = [ln for ln in text.splitlines()
                if not any(ln.strip().startswith(k + ":") for k in keys)]
        return rel, "\n".join(kept) + "\n"

    def _resolve_named_focus_spec(self, project, spec: list[dict],
                                  blocks) -> list[dict]:
        """Replace a position of 'all' with the specific focus named in the
        request when one is identifiable by id or readable name."""
        resolved = self._match_focus_name(project.plan.request, blocks)
        out: list[dict] = []
        for entry in spec:
            if entry.get("position") == "all" and resolved:
                entry = dict(entry)
                entry["position"] = resolved
            out.append(entry)
        return out

    def _raise_if_named_focus_unresolved(self, project, spec: list[dict]) -> None:
        """Never apply an effect to ALL focuses just because a specific focus
        was named but could not be resolved."""
        if not any(e.get("position") == "all" for e in spec):
            return
        mention = re.search(
            r"\b(?:the\s+)?focus\s+([A-Za-z][A-Za-z0-9 '_-]{2,}?)\b"
            r"(?:\s+(?:to|for|as|in)|$)",
            project.plan.request, re.IGNORECASE)
        if mention:
            name = mention.group(1).strip().lower()
            if name not in ("tree", "branch", "path"):
                raise ValueError(
                    f"could not resolve focus '{mention.group(1)}' to an id in "
                    "the target tree; no 'all' fallback was applied")

    def _apply_effects_to_tree(self, text: str, blocks, spec: list[dict]) -> str:
        for entry in spec:
            effect = entry.get("effect", "")
            if effect not in self.validator.effects:
                raise ValueError(f"unverified effect: {effect}")
            line = f"{effect} = {self._format_spec_amount(effect, entry.get('amount'))}"
            pos = entry.get("position", "all")
            # Re-scan after every effect: earlier insertions shift block offsets.
            blocks = self._focus_blocks(text)
            indices = (list(range(len(blocks))) if pos == "all"
                       else [self._resolve_focus_index_from_pos(pos, blocks)])
            # Insert right-to-left so earlier offsets stay valid.
            for idx in sorted(indices, reverse=True):
                _fid, start, end = blocks[idx]
                text = self._insert_into_reward(text, start, end, line)
        return text

    @staticmethod
    def _format_spec_amount(effect: str, amount) -> str:
        if amount is None:
            return str(5)
        val = float(amount)
        if effect in ("add_stability", "add_war_support"):
            return f"{val:.4f}".rstrip("0").rstrip(".")
        return str(int(val)) if val == int(val) else f"{val:.2f}"

    @staticmethod
    def _insert_event_into_focus(text: str, start: int, end: int, eid: str) -> str:
        """Insert a country_event reference into the focus's completion_reward."""
        return ProjectExecutor._insert_into_reward(
            text, start, end, f"country_event = {{ id = {eid} days = 5 }}")

    @staticmethod
    def _insert_into_reward(text: str, start: int, end: int, content: str) -> str:
        """Insert `content` into the focus's completion_reward block."""
        focus_block = text[start:end]
        m = re.search(r"completion_reward\s*=\s*\{", focus_block)
        line = "\t\t\t" + content + "\n"
        if m:
            cr_start = start + m.end()
            depth, i = 1, cr_start
            while i < end and depth > 0:
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                i += 1
            if content.strip() in text[cr_start : i - 1]:
                return text  # already present — idempotent
            return text[: i - 1] + line + text[i - 1:]
        reward = f"\t\tcompletion_reward = {{\n{line}\t\t}}\n"
        return text[: end - 1] + reward + text[end - 1:]

    def _event_content(self, project, tag, eid, idx: int) -> str:
        lines = [
            "country_event = {",
            f"\tid = {eid}",
            f"\ttitle = {eid}.t",
            f"\tdesc = {eid}.d",
            "\tis_triggered_only = yes",
            "\tfire_only_once = yes",
            "\ttrigger = {",
            "\t}",
            "\toption = {",
            f"\t\tname = {eid}.a",
        ]
        lines += ["\t}", "}"]
        return "\n".join(lines) + "\n"

    def _vanilla_focus_file_for_tag(self, tag: str):
        """First vanilla focus file whose tree's country block targets the tag."""
        from .preview import raw_game_dir

        if not tag:
            return None
        base = raw_game_dir() / "common" / "national_focus"
        if not base.exists():
            return None
        for f in sorted(base.glob("*.txt")):
            text = f.read_text(encoding="utf-8", errors="ignore")
            for m in re.finditer(r"focus_tree\s*=\s*\{", text):
                start = m.end()
                depth, i = 1, start
                while i < len(text) and depth > 0:
                    if text[i] == "{":
                        depth += 1
                    elif text[i] == "}":
                        depth -= 1
                    i += 1
                block = text[m.start():i]
                if re.search(rf"modifier\s*=\s*\{{[^}}]*?tag\s*=\s*{re.escape(tag)}\b", block):
                    return f
        return None

    def _register_loc_for_ids(self, project, text: str) -> None:
        """Mark workspace localisation keys for existing ids as present."""
        from .filesystem import workspace

        loc_base = workspace() / "localisation" / "english"
        if not loc_base.exists():
            return
        loc_text = "\n".join(
            f.read_text(encoding="utf-8", errors="ignore") for f in loc_base.glob("*.yml"))
        focus_ids = set(project.memory.focus_ids)
        for ident in list(project.memory.focus_ids) + list(project.memory.event_ids):
            keys = (ident, ident + "_desc") if ident in focus_ids \
                else (ident + ".t", ident + ".d", ident + ".a")
            for key in keys:
                if key in loc_text:
                    project.memory.add_loc(key)

    def _add_referenced_passthrough(self, proposals: dict, text: str) -> None:
        """Include unchanged files that define ids the tree references, so the
        validator sees those ids as allowed (their diffs stay empty)."""
        from .filesystem import workspace

        refs: set[str] = set()
        for m in IDEA_LIST.finditer(text):
            for tok in m.group(1).split():
                if tok not in {"idea", "days", "months", "value", "name", "="} and not tok.isdigit():
                    refs.add(tok)
        for m in EVENT_REF.finditer(text):
            refs.add(m.group(1))
        vanilla_ideas = set(self.index.categories().get("ideas", {}))
        vanilla_events = set(self.index.categories().get("events", {}))
        ws = workspace()
        for ref in refs:
            if ref in vanilla_ideas or ref in vanilla_events:
                continue
            for sub in ("common/ideas", "events"):
                base = ws / sub
                if not base.exists():
                    continue
                for f in base.glob("*.txt"):
                    rel = f.relative_to(ws).as_posix()
                    if rel in proposals:
                        continue
                    fc = f.read_text(encoding="utf-8", errors="ignore")
                    if re.search(rf"\bid\s*=\s*{re.escape(ref)}\b", fc):
                        proposals[rel] = fc
                        break

    def _country_style(self, tag: str) -> str:
        """Grounded graphical culture/color sampled from a vanilla country file."""
        style = (
            "use_legacy_ai_pp_spend = yes\n"
            "\ngraphical_culture = western_european_gfx\n"
            "graphical_culture_2d = western_european_2d\n"
            "\ncolor = { 128 128 128 }\n"
        )
        base = DATA_RAW / "game" / "common" / "countries"
        if base.exists():
            for f in sorted(base.glob("*.txt"))[:3]:
                text = f.read_text(encoding="utf-8", errors="ignore")
                gc = re.search(r"graphical_culture = (\S+)", text)
                gc2 = re.search(r"graphical_culture_2d = (\S+)", text)
                color = re.search(r"color = (?:rgb \{)?\s*([\d\s]+)\s*\}?", text)
                if gc:
                    style = (f"use_legacy_ai_pp_spend = yes\n\ngraphical_culture = {gc.group(1)}\n"
                             + (f"graphical_culture_2d = {gc2.group(1)}\n" if gc2 else "")
                             + (f"\ncolor = rgb {{ {color.group(1).strip()} }}\n" if color else "\ncolor = { 128 128 128 }\n"))
                    break
        return style

    def _gen_ideas(self, project, tag, slug, feature) -> dict:
        n = 5 if feature in ("focus_branch", "civil_war") else 3
        file = f"common/ideas/{tag.lower()}_agent_ideas.txt"
        # Vanilla format: ideas = { country = { <idea> = { modifier = {...} } } }
        blocks = ["ideas = {", "\tcountry = {"]
        requested = (self._requested_idea_modifiers(project)
                     if feature == "focus_with_ideas" else {})
        for i in range(n):
            idea_id = project.memory.next_id("idea", f"{tag}_{slug}_agent_idea_{i + 1:02d}")
            project.memory.idea_ids.append(idea_id)
            project.memory.add_loc(idea_id, idea_id + "_desc")
            blocks.append(f"\t\t{idea_id} = {{")
            blocks.append("\t\t\tmodifier = {")
            if i == 0:
                for mod, val in requested.items():
                    blocks.append(f"\t\t\t\t{mod} = {val}")
            blocks.append("\t\t\t}")
            blocks.append("\t\t}")
        blocks.append("\t}")
        blocks.append("}")
        return {file: "\n".join(blocks) + "\n"}

    def _requested_idea_modifiers(self, project) -> dict[str, str]:
        """Verified modifier values explicitly requested for a national spirit,
        e.g. 'boosting stability and factory output'."""
        from .planner import MODIFIER_WORDS

        low = project.plan.request.lower()
        out: dict[str, str] = {}
        for word, mod in MODIFIER_WORDS.items():
            if word not in low or mod not in self.validator.modifiers:
                continue
            m = re.search(r"(\d+(?:\.\d+)?)\s*%?\s*" + re.escape(word), low)
            if m:
                val = float(m.group(1))
                frac = val / 100.0 if val >= 1 else val
            else:
                frac = 0.05
            out[mod] = f"{frac:.4f}".rstrip("0").rstrip(".")
        return out

    def _gen_focuses(self, project, tag, slug, feature) -> dict:
        n = self._requested_focus_count(project) or \
            {"focus_branch": 10, "civil_war": 8, "releasable": 6,
             "modify_focus_tree": 3, "idea_chain": 3}.get(feature, 5)
        low = project.plan.request.lower()
        civil_war_ideology = _ideology_for_civil_war(low)
        # NEVER overwrite the country's real focus tree. Complete trees go into
        # a dedicated agent file; modify_focus_tree appends to an existing
        # workspace tree (and falls back to a fresh agent tree otherwise).
        full_tree = feature != "modify_focus_tree"
        file = f"common/national_focus/{tag.lower()}_agent_focus.txt"
        if not full_tree:
            from .filesystem import workspace

            existing = workspace() / "common" / "national_focus" / \
                COUNTRY_FILES.get(tag, tag.lower() + ".txt")
            if existing.exists():
                file = f"common/national_focus/{COUNTRY_FILES.get(tag, tag.lower() + '.txt')}"
            else:
                full_tree = True
        anchors = self._vanilla_focus_anchors(tag)
        tree_id = f"{tag}_{slug}_agent_tree"
        lines = []
        if full_tree:
            lines += [
                "focus_tree = {",
                f"\tid = {tree_id}",
                "\tcountry = {",
                "\t\tfactor = 0",
                "\t\tmodifier = {",
                "\t\t\tadd = 10",
                f"\t\t\ttag = {tag}",
                "\t\t}",
                "\t}",
            ]
        # A fresh standalone tree starts at its own root: the first focus must
        # not carry a prerequisite (especially not one pointing at an anchor
        # focus that may not exist in the user's mod). Only when appending
        # into an existing tree does the first new focus attach to an anchor.
        prev = anchors[0] if (anchors and not full_tree) else None
        effects = self._verified_effects()
        idea_ids = list(project.memory.idea_ids)
        want_events = feature == "civil_war" or project.plan.task("events") is not None \
            or project.plan.feature == "focus_event"
        event_idx = self._requested_focus_index(project, n) if want_events else -1
        for i in range(n):
            fid = project.memory.next_id("focus", f"{tag}_{slug}_agent_focus_{i + 1:02d}")
            project.memory.focus_ids.append(fid)
            project.memory.add_loc(fid, fid + "_desc")
            lines.append("\tfocus = {")
            lines.append(f"\t\tid = {fid}")
            lines.append("\t\tcost = 10")
            reward = effects[i % len(effects)]
            icon = self._focus_icon(reward)
            if icon:
                lines.append(f"\t\ticon = {icon}")
            lines.append("\t\tx = 0")
            lines.append(f"\t\ty = {i * 2}")
            if prev:
                lines.append(f"\t\tprerequisite = {{ focus = {prev} }}")
            lines.append("\t\tcompletion_reward = {")
            if feature == "focus_branch" and "civil war" in low and i == n - 1:
                # The civil-war branch culminates in the final focus starting
                # a civil war — no events/decisions/ideas are generated.
                lines.append("\t\t\tstart_civil_war = {")
                lines.append(f"\t\t\t\tideology = {civil_war_ideology}")
                lines.append("\t\t\t\tsize = 0.4")
                lines.append("\t\t\t}")
            if feature == "focus_with_ideas":
                # The requested national spirit is granted by the final focus.
                if i == n - 1 and idea_ids:
                    lines.append(f"\t\t\tadd_ideas = {{ {idea_ids[0]} }}")
                    project.memory.relate("focus", fid, "idea", idea_ids[0])
            if want_events and i == event_idx:
                ev = self._ensure_event(project, tag, slug, i)
                lines.append(f"\t\t\tcountry_event = {{ id = {ev} days = 5 }}")
                project.memory.relate("focus", fid, "event", ev)
            lines.append("\t\t}")
            lines.append("\t}")
            prev = fid
        if full_tree:
            lines.append("}")
        return {file: "\n".join(lines) + "\n"}

    @staticmethod
    def _requested_focus_count(project) -> int | None:
        """Explicit focus count from the request, e.g. 'with 15 focuses'."""
        low = project.plan.request.lower()
        m = re.search(r"\b(\d{1,3})\s*-?\s*focus(?:es)?\b", low)
        if not m:
            return None
        n = int(m.group(1))
        return min(max(n, 3), 40)

    def _requested_focus_index(self, project, n: int) -> int:
        """Index of the focus an event should attach to (creation path)."""
        pos = project.plan.focus_position
        if pos == "first":
            return 0
        if pos == "last":
            return n - 1
        if pos.isdigit():
            return min(max(int(pos) - 1, 0), n - 1)
        return n - 1

    def _ensure_event(self, project, tag, slug, idx) -> str:
        """Create (or reuse) an event id referenced by a focus."""
        for rel in project.memory.relationships:
            if rel["kind"] == "focus" and rel["to_kind"] == "event":
                return rel["to"]
        eid = project.memory.next_id("event", f"{tag}_{slug}_agent_event_{idx + 1:02d}")
        project.memory.event_ids.append(eid)
        return eid

    def _gen_events(self, project, tag, slug) -> dict:
        if project.plan.task("events") is None and project.plan.feature != "focus_event":
            # No events were requested (e.g. a plain focus tree); focus_event
            # creates an events file by definition.
            return {}
        file = f"events/{tag.lower()}_agent_events.txt"
        blocks = []
        event_ids = list(project.memory.event_ids)
        if not event_ids:
            # Standalone events request: create one default event shell.
            eid = project.memory.next_id("event", f"{tag}_{slug}_agent_event_01")
            project.memory.event_ids.append(eid)
            event_ids = [eid]
        for eid in event_ids:
            project.memory.add_loc(f"{eid}.t", f"{eid}.d", f"{eid}.a")
            blocks.append("country_event = {")
            blocks.append(f"\tid = {eid}")
            blocks.append(f"\ttitle = {eid}.t")
            blocks.append(f"\tdesc = {eid}.d")
            blocks.append("\tis_triggered_only = yes")
            blocks.append("\tfire_only_once = yes")
            blocks.append("\ttrigger = {")
            blocks.append("\t}")
            blocks.append("\toption = {")
            blocks.append(f"\t\tname = {eid}.a")
            # Blank option: effects are added by later targeted prompts.
            blocks.append("\t}")
            blocks.append("}")
        return {file: "\n".join(blocks) + "\n"}

    def _gen_decisions(self, project, tag, slug) -> dict:
        file = f"common/decisions/{tag}_agent.txt"
        # Vanilla format: <category> = { <decision_id> = { ... } } — decision
        # ids are direct children of a named category, no `decisions = {`.
        blocks = [f"{tag.lower()}_agent_decisions = {{"]
        for i in range(2):
            did = project.memory.next_id("decision", f"{tag}_{slug}_agent_decision_{i + 1:02d}")
            project.memory.decision_ids.append(did)
            project.memory.add_loc(did, did + "_desc")
            blocks.append(f"\t{did} = {{")
            blocks.append("\t\tavailable = { always = yes }")
            blocks.append("\t\tvisible = { always = yes }")
            # Blank on purpose: effects are added by later targeted prompts.
            blocks.append("\t\tcomplete_effect = {")
            blocks.append("\t\t}")
            blocks.append("\t}")
        blocks.append("}")
        return {file: "\n".join(blocks) + "\n"}

    def _gen_characters(self, project, tag, slug) -> dict:
        file = f"common/characters/{tag}_agent.txt"
        idea_id = project.memory.next_id("idea", f"{tag}_{slug}_agent_advisor_idea_01")
        project.memory.idea_ids.append(idea_id)
        project.memory.add_loc(idea_id, idea_id + "_desc")
        adv_id = project.memory.next_id("advisor", f"{tag}_{slug}_agent_advisor_01")
        project.memory.advisor_ids.append(adv_id)
        project.memory.add_loc(adv_id, adv_id + "_desc")
        project.memory.relate("advisor", adv_id, "idea", idea_id)
        content = (
            "characters = {\n"
            f"\t{adv_id} = {{\n"
            '\t\tname = { first = "Agent" last = "Advisor" }\n'
            f"\t\tadvisor = {{ type = general idea = {idea_id} }}\n"
            "\t}\n"
            "}\n"
        )
        return {file: content}

    def _gen_ai_strategy(self, project, tag, slug) -> dict:
        file = f"common/ai_strategy/{tag}_agent.txt"
        # Vanilla format: <named_strategy> = { allowed/enable/abort,
        # ai_strategy = { type = ... } }. Anonymous blocks are invalid.
        name = f"{tag}_{slug}_agent_strategy"
        content = (
            f"{name} = {{\n"
            "\tallowed = {\n"
            f"\t\toriginal_tag = {tag}\n"
            "\t}\n"
            "\tenable = { always = yes }\n"
            "\tabort = { always = no }\n"
            "\tai_strategy = {\n"
            "\t\ttype = balance\n"
            "\t}\n"
            "}\n"
        )
        return {file: content}

    def _gen_references(self, project, tag) -> dict:
        # Focus-tree selection is wired by the `country = { ... }` block inside
        # the focus tree file itself — vanilla never writes `focus_tree` blocks
        # into history/countries files. Creating extra history files (or worse,
        # duplicating an existing "TAG - Name.txt" as "Name.txt") broke mods.
        return {}

    def _country_name(self, tag: str, countries: dict) -> str:
        path = countries.get(tag, "")
        if path:
            try:
                for line in Path(path).read_text(encoding="utf-8", errors="ignore").splitlines():
                    m = re.match(rf'^{re.escape(tag)}\s*=\s*"?countries/(.+)\.txt"?', line.strip())
                    if m:
                        return m.group(1)
            except OSError:
                pass
        return tag

    def _gen_localisation(self, project, tag) -> dict:
        from .filesystem import workspace

        file = f"localisation/english/{tag.lower()}_l_english.yml"
        existing = workspace() / file
        lines = ["l_english:"]
        seen: set[str] = set()
        if existing.exists():
            # MERGE: preserve every existing key (and comment) in the file.
            # Replacing the file wiped user/vanilla keys in earlier runs.
            for line in existing.read_text(
                    encoding="utf-8-sig", errors="surrogateescape").splitlines():
                stripped = line.strip()
                if stripped.startswith("l_english"):
                    continue
                m = re.match(r"^([A-Za-z0-9_.]+)\s*:", stripped)
                if m:
                    seen.add(m.group(1))
                lines.append(line)
        for key in project.memory.loc_keys:
            if key not in seen:
                lines.append(f' {key}:0 "Agent-generated text for {key}"')
        return {file: "\n".join(lines) + "\n"}

    # -------------------------------------------------------------- helpers
    def _vanilla_focus_anchors(self, tag: str) -> list[str]:
        focuses = self.index.categories().get("focuses", {})
        file = COUNTRY_FILES.get(tag, tag.lower() + ".txt")
        anchors = [k for k, v in focuses.items() if str(v).lower().endswith(file.lower())]
        return sorted(anchors)[:2]

    def _verified_effects(self) -> list[str]:
        return [e for e in VERIFIED_EFFECTS if e in self.validator.effects]

    def _verified_modifiers(self) -> list[str]:
        return [m for m in PREFERRED_MODIFIERS if m in self.validator.modifiers]

    def _focus_icon(self, reward: str) -> str:
        """A verified vanilla GFX icon for a focus (prefers goal icons)."""
        self.validator._ensure_icons()
        icons = sorted(self.validator.icon_set)
        if not icons:
            return ""
        goals = [i for i in icons if "GFX_goal" in i]
        pool = goals or icons
        h = 0
        for ch in reward:
            h = (h * 31 + ord(ch)) & 0xFFFF
        return pool[h % len(pool)]

    @staticmethod
    def _effect_value(effect: str, i: int) -> str:
        if effect in ("add_stability", "add_war_support"):
            return f"{0.05 + (i % 4) * 0.01:.2f}"
        if effect == "add_manpower":
            return str(50000 + i * 10000)
        return str(5 + i)


PREREQ_FOCUS = re.compile(r"prerequisite\s*=\s*\{\s*focus\s*=\s*([A-Za-z0-9_]+)")
EVENT_REF = re.compile(r"country_event\s*=\s*\{\s*id\s*=\s*([A-Za-z0-9_.]+)")
IDEA_LIST = re.compile(r"(?:add_ideas|remove_ideas|add_timed_idea)\s*=\s*\{([^}]*)\}")
ADVISOR_IDEA = re.compile(r"advisor\s*=\s*\{[^}]*?idea\s*=\s*([A-Za-z0-9_]+)")
ICON_REF = re.compile(r"\b(?:icon|picture)\s*=\s*(GFX_[A-Za-z0-9_]+)")
