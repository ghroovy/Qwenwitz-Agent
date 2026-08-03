# Owner: ACTIVE
"""JSON-RPC server (stdio) exposing the Qwenwitz agent to the VS Code extension.

Protocol: one JSON object per line on stdin/stdout:
  {"id": 1, "method": "repair", "params": {...}}
  {"id": 1, "result": {...}}
  {"id": 1, "error": {"code": -32603, "message": "..."}}
The backend architecture is unchanged; this is a thin facade.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Entry-point shim: server.py is launched as a script (python server.py) with
# an arbitrary cwd, so make the repo root importable regardless of location.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hoi4_agent.agent import Agent  # noqa: E402
from hoi4_agent.project import ProjectExecutor, load_project  # noqa: E402


class Hoi4Server:
    def __init__(self):
        # Never auto-apply: every change goes through the pending-diff review
        # flow in the extension (Accept / Reject).
        self.agent = Agent(auto_approve=False, use_model=False)
        self.agent.promptless = True
        self.agent._ask_approval = lambda diff: False
        self.executor = ProjectExecutor(self.agent)

    # ------------------------------------------------------------ dispatcher
    def handle(self, method: str, params: dict) -> dict:
        fn = {
            "ping": lambda p: {"pong": True},
            "create_feature": self.create_feature,
            "continue_feature": self.continue_feature,
            "transfer_states": self.transfer_states,
            "list_countries": self.list_countries,
            "repair": self.repair,
            "explain": self.explain,
            "merge": self.merge,
            "refactor": self.refactor,
            "validate": self.validate,
            "find_vanilla_example": self.find_vanilla_example,
            "inspect_identifier": self.inspect_identifier,
            "search_documentation": self.search_documentation,
            "diagnostics": self.diagnostics,
            "code_action": self.code_action,
            "get_state": self.get_state,
            "approve": lambda p: self.agent.approve_pending(
                p.get("file"), p.get("batch_id"), p.get("all", False)),
            "reject": lambda p: self.agent.reject_pending(
                p.get("file", ""), p.get("batch_id")),
            "undo": lambda p: self.agent.undo_applied(p.get("file", "")),
            "preview_map": self.preview_map,
            "preview_focus_tree": self.preview_focus_tree,
            "preview_events": self.preview_events,
            "preview_decisions": self.preview_decisions,
            "preview_inspect": self.preview_inspect,
            "preview_file": self.preview_file,
        }.get(method)
        if fn is None:
            raise ValueError(f"unknown method: {method}")
        return fn(params)

    # -------------------------------------------------------------- previews
    def preview_map(self, params: dict) -> dict:
        from hoi4_agent.preview.map_preview import preview_map

        return preview_map(
            highlight_tag=params.get("highlight_tag", ""),
            max_width=int(params.get("max_width", 1408)),
            mode=params.get("mode", "province"),
        )

    def preview_focus_tree(self, params: dict) -> dict:
        from hoi4_agent.preview.focus_preview import preview_focus_tree

        return preview_focus_tree(params.get("path"))

    def preview_events(self, params: dict) -> dict:
        from hoi4_agent.preview.event_preview import preview_events

        return preview_events(params.get("path"), max_events=int(params.get("max_events", 300)))

    def preview_decisions(self, params: dict) -> dict:
        from hoi4_agent.preview.decision_preview import preview_decisions

        return preview_decisions(params.get("path"), max_decisions=int(params.get("max_decisions", 300)))

    def preview_inspect(self, params: dict) -> dict:
        from hoi4_agent.preview.inspect_preview import preview_inspect

        return preview_inspect(
            params.get("kind", ""),
            params.get("id", ""),
            tools=self.agent.tools,
            index=self.agent.index,
            validator=self.agent.validator,
        )

    def preview_file(self, params: dict) -> dict:
        """Detect which preview fits a file path."""
        path = params.get("path", "")
        p = path.replace("\\", "/").lower()
        if "map/" in p or p.endswith(".bmp") or p.endswith("definition.csv"):
            return {"kind": "map", "path": path}
        if "national_focus" in p or "focus" in p:
            return {"kind": "focus_tree", "path": path}
        if "events/" in p:
            return {"kind": "events", "path": path}
        if "decisions" in p:
            return {"kind": "decisions", "path": path}
        return {"kind": "unknown", "path": path}

    # -------------------------------------------------------------- commands
    def create_feature(self, params: dict) -> dict:
        request = params.get("request", "")
        # Country-less snippet requests ("write a decision that...") are
        # answered by the deterministic snippet engine, routed into the
        # currently open file in VS Code when one is provided and applicable.
        from hoi4_agent.snippets import SnippetEngine

        engine = SnippetEngine(self.agent)
        mod = engine.modify(request)
        if mod is not None:
            proposals, reason = mod
            if proposals:
                self.agent._prepare_pending(proposals, label="Modified focus",
                                            project_slug="modify")
                return {
                    "summary": ("Modified focus — review the diff and click "
                                "Accept to apply."),
                    "status": "modify",
                    "pending_files": sorted(proposals),
                    "project_slug": "modify",
                    "applied": False,
                }
            summary = ("No change needed — that focus already has the "
                       "requested value." if reason == "already_set"
                       else "I could not find that focus in the workspace — "
                            "open or name the file that contains it.")
            return {"summary": summary, "status": "no_change",
                    "pending_files": [], "project_slug": "modify",
                    "applied": False}
        if not self.executor.planner._detect_new_country(request) \
                and engine.matches(request):
            proposals = engine.generate(
                request, active_file=params.get("active_file") or None)
            if proposals:
                self.agent._prepare_pending(
                    proposals, label="Generated snippet", project_slug="snippet")
                active_file = params.get("active_file") or ""
                targeted = active_file and any(
                    p.replace("\\", "/") == active_file.replace("\\", "/")
                    for p in proposals)
                summary = "Generated snippet"
                if targeted:
                    summary += f" — will append to {active_file}"
                summary += " — review the diffs and click Accept to apply."
                return {
                    "summary": summary,
                    "status": "snippet",
                    "pending_files": sorted(proposals),
                    "project_slug": "snippet",
                    "applied": False,
                }
            return {
                "summary": ("This already exists in your mod — no duplicate "
                            "was generated and nothing was changed."),
                "status": "snippet_exists",
                "pending_files": [],
                "project_slug": "snippet",
                "applied": False,
            }
        countries = self.executor.planner._extract_countries(request)
        if len(countries) > 1:
            from hoi4_agent.planner import MULTI_COUNTRY_FEATURES

            probe = self.executor.planner.plan_project(request)
            if probe.feature in MULTI_COUNTRY_FEATURES:
                return self.executor.run_multi(request, auto_approve=False)
        project = self.executor.create_project(request, params.get("name"))
        if project.plan.feature == "unknown_country":
            return {
                "summary": (
                    "I couldn't identify the country in your request. If it is a brand-new "
                    "country, say: create a new country called <Name>. If it exists only in "
                    "your mod, make sure it is defined in common/country_tags and "
                    "history/countries in the workspace, then try again."
                ),
                "status": "unknown_country",
                "task_status": {},
                "pending_files": [],
                "project_slug": project.slug,
            }
        if project.plan.feature == "new_country" and not project.plan.politics:
            project.save()
            return {
                "needs_input": "politics",
                "question": (
                    f"What ideology should {project.plan.new_country_name} be? "
                    "Reply e.g. 'democracy', 'communism', or '20% fascist, 80% democratic'."
                ),
                "project_slug": project.slug,
            }
        if project.plan.feature == "focus_effects" and not project.plan.effect_spec:
            project.save()
            return {
                "needs_input": "effects",
                "question": (
                    "Which effects should the focuses give? Reply like "
                    "'50 political power to each focus' or "
                    "'political power to focus 1, stability to focus 2'."
                ),
                "project_slug": project.slug,
            }
        if project.plan.feature == "remove_content" and not project.plan.country_tag:
            project.save()
            return {
                "needs_input": "country",
                "question": (
                    f"Which country's {project.plan.remove_spec.get('target', 'content')} "
                    "should I remove? Reply like 'germany'."
                ),
                "project_slug": project.slug,
            }
        if project.plan.feature == "oob":
            missing = self._oob_missing(project.plan)
            if missing:
                field, question = missing[0]
                project.save()
                return {"needs_input": field, "question": question,
                        "project_slug": project.slug}
        return self._run_feature(project)

    @staticmethod
    def _oob_missing(plan) -> list[tuple[str, str]]:
        missing: list[tuple[str, str]] = []
        if not plan.country_tag:
            missing.append(("country", "Which country should the OOB be for? "
                                       "Reply like 'belgium' or 'BEL'."))
        if not plan.division_name:
            missing.append(("division_name", "What should the division(s) be "
                           "called? (default: 'Infantry Division')"))
        if plan.spawn is None:
            missing.append(("spawn", "Should the divisions be spawned at the "
                           "country's victory points? Reply 'spawn' or 'no'."))
        return missing

    def continue_feature(self, params: dict) -> dict:
        project = load_project(params.get("project_slug", ""))
        if project is None:
            raise ValueError(f"project not found: {params.get('project_slug')}")
        field = params.get("field", "") or self._infer_field(project)
        answer = params.get("answer", "")
        if project.plan.feature == "oob":
            if field == "country":
                from hoi4_agent.planner import Planner

                tag = Planner()._resolve_country(answer.lower(), answer)
                if not tag:
                    project.save()
                    return {
                        "needs_input": "country",
                        "question": (f"I couldn't identify the country '{answer}'. "
                                    "Reply with a country name or tag, e.g. 'belgium' or 'BEL'."),
                        "project_slug": project.slug,
                    }
                project.plan.country_tag = tag
            elif field == "division_name":
                project.plan.division_name = (answer or "").strip()
            elif field == "spawn":
                low = (answer or "").lower()
                project.plan.spawn = ("no" not in low and
                                      ("spawn" in low or "yes" in low or "sure" in low))
            project.save()
            missing = self._oob_missing(project.plan)
            if missing:
                f2, question = missing[0]
                return {"needs_input": f2, "question": question,
                        "project_slug": project.slug}
            return self._run_feature(project)
        if field == "effects":
            from hoi4_agent.planner import _parse_effect_spec

            spec = _parse_effect_spec(answer)
            if not spec:
                project.save()
                return {
                    "needs_input": "effects",
                    "question": (
                        "I couldn't find any effects in that answer. Reply like "
                        "'50 political power to each focus'."
                    ),
                    "project_slug": project.slug,
                }
            project.plan.effect_spec = spec
        elif field == "country":
            from hoi4_agent.planner import Planner

            tag = Planner()._resolve_country(answer.lower(), answer)
            if not tag:
                project.save()
                return {
                    "needs_input": "country",
                    "question": (
                        f"I couldn't identify the country '{answer}'. "
                        "Reply with a country name or tag, e.g. 'germany' or 'GER'."
                    ),
                    "project_slug": project.slug,
                }
            project.plan.country_tag = tag
        else:
            project.plan.politics = answer
        project.save()
        return self._run_feature(project)

    @staticmethod
    def _infer_field(project) -> str:
        """When the client doesn't send the field, route by what's missing."""
        if project.plan.feature == "focus_effects" and not project.plan.effect_spec:
            return "effects"
        if project.plan.feature == "remove_content" and not project.plan.country_tag:
            return "country"
        if project.plan.feature == "new_country" and not project.plan.politics:
            return "politics"
        if project.plan.feature == "oob":
            missing = Hoi4Server._oob_missing(project.plan)
            if missing:
                return missing[0][0]
        return "politics"

    def transfer_states(self, params: dict) -> dict:
        """Transfer selected states to an existing country/tag (ownership only)."""
        tag = (params.get("tag") or "").strip().upper()
        states = [int(s) for s in params.get("states", [])
                  if str(s).strip().lstrip("-").isdigit() and int(s) > 0]
        if not tag or not states:
            raise ValueError("transfer_states requires a tag and at least one state id")
        request = f"transfer states {', '.join(str(s) for s in states)} to {tag}"
        project = self.executor.create_project(request)
        return self._run_feature(project)

    def list_countries(self, params: dict) -> dict:
        """Agent-built countries + all country tags available in vanilla/mod."""
        from hoi4_agent.planner import Planner
        from hoi4_agent.project import load_built_countries

        planner = Planner()
        _, ws_tags = planner._workspace_countries()
        vanilla = self.agent.index.categories().get("countries", {})
        tags: dict[str, dict] = {}
        for t, n in ws_tags.items():
            tags[t] = {"name": n, "source": "workspace"}
        for t in vanilla:
            tags.setdefault(t, {"name": t, "source": "vanilla"})
        return {
            "agent_built": load_built_countries(),
            "tags": [
                {"tag": t, "name": info["name"], "source": info["source"]}
                for t, info in sorted(tags.items())
            ],
        }

    def _run_feature(self, project) -> dict:
        result = self.executor.run(project, auto_approve=False)
        self.agent._prepare_pending(
            project.proposals, label=project.plan.name or "Feature",
            project_slug=project.slug)
        pending_files = list(self.agent.pending["proposals"])
        if result["status"] == "pending" and pending_files:
            summary = ("Feature ready — " + ", ".join(pending_files[:4]) +
                       (f" and {len(pending_files) - 4} more" if len(pending_files) > 4 else "") +
                       ". Review the diffs and click Accept to apply.")
        elif result["status"] == "pending":
            summary = ("No new changes — this feature already exists in the workspace. "
                       "Try a different country or a different kind of branch.")
        else:
            summary = result["message"]
        return {
            "summary": summary,
            "status": result["status"],
            "task_status": result["task_status"],
            "pending_files": pending_files,
            "project_slug": project.slug,
        }

    def repair(self, params: dict) -> dict:
        return self.agent.run(params.get("request") or "repair the project")

    def explain(self, params: dict) -> dict:
        return self.agent.run(params.get("request") or "explain the current state")

    def merge(self, params: dict) -> dict:
        return self.agent.run(params.get("request") or "merge these mods")

    def refactor(self, params: dict) -> dict:
        return self.agent.run(params.get("request") or "refactor the project")

    def validate(self, params: dict) -> dict:
        errors = []
        for v in (self.agent.validator.validate_focus_tree(),
                  self.agent.validator.validate_events(),
                  self.agent.validator.validate_localisation()):
            errors.extend(v.get("errors", []))
            errors.extend(w for w in v.get("warnings", []) if w.get("type") == "missing_localisation")
        return {"valid": not errors, "errors": errors}

    def find_vanilla_example(self, params: dict) -> dict:
        return self.agent.tools.find_vanilla_examples(params.get("query", "")).to_dict()

    def inspect_identifier(self, params: dict) -> dict:
        name = params.get("name", "")
        res = self.agent.tools.search_identifier(name)
        out = res.to_dict()
        if not res.ok:
            out["data"]["similar"] = self.agent.tools.find_similar_identifier(name).data.get("results", [])
        return out

    def search_documentation(self, params: dict) -> dict:
        return self.agent.tools.search_documentation(params.get("query", "")).to_dict()

    def diagnostics(self, params: dict) -> dict:
        return self.validate({})

    # ----------------------------------------------------------- code actions
    def code_action(self, params: dict) -> dict:
        kind = params.get("kind")
        if kind in ("quick_fix", "repair_file"):
            result = self.agent.run("repair the project")
            return {"summary": result.get("summary", ""),
                    "pending_files": list(self.agent.pending["proposals"])}
        if kind == "generate_localisation":
            ident = params.get("identifier", "")
            proposals = {}
            loc_base = self._workspace() / "localisation" / "english"
            loc_files = sorted(loc_base.glob("*.yml")) if loc_base.exists() else []
            if loc_files:
                rel = loc_files[0].relative_to(self._workspace()).as_posix()
                proposals[rel] = loc_files[0].read_text(encoding="utf-8-sig")
            else:
                proposals["localisation/english/mod_l_english.yml"] = "l_english:\n"
            from hoi4_agent.repair import RepairEngine

            engine = RepairEngine(self.agent.ctx, self.agent.validator, self.agent.tools, agent=self.agent)
            engine._repair_localisation(proposals, {
                "identifier": ident, "type": "missing_localisation",
                "message": "missing localisation keys: " + ident})
            self.agent._prepare_pending(proposals)
            return {"pending_files": list(self.agent.pending["proposals"])}
        if kind in ("rename_identifier", "replace_verified"):
            old = params.get("identifier", "")
            new = params.get("new_identifier", "")
            if kind == "replace_verified":
                similar = self.agent.index.fuzzy(old, limit=1)
                if not similar:
                    return {"pending_files": [], "message": f"no verified replacement for {old}"}
                new = similar[0]["identifier"]
            proposals = {}
            from hoi4_agent.project_scan import SCAN_DIRS

            for rel_dir in SCAN_DIRS:
                base = self._workspace() / rel_dir
                if not base.exists():
                    continue
                for f in base.rglob("*"):
                    if not f.is_file():
                        continue
                    text = f.read_text(encoding="utf-8",
                                       errors="surrogateescape")
                    if old in text:
                        proposals[f.relative_to(self._workspace()).as_posix()] = text.replace(old, new)
            self.agent._prepare_pending(proposals)
            return {"pending_files": list(self.agent.pending["proposals"]),
                    "replacement": new}
        return {"error": f"unknown code action: {kind}"}

    # ----------------------------------------------------------------- state
    def get_state(self, params: dict) -> dict:
        pending = {}
        for path, diff in self.agent.pending["diffs"].items():
            pending[path] = diff
        backlog = [{
            "id": b["id"],
            "label": b.get("label", ""),
            "project_slug": b.get("project_slug", ""),
            "status": b.get("status", "pending"),
            "created_at": b.get("created_at", 0),
            "files": list(b["diffs"]),
            "new_files": [p for p, old in b.get("originals", {}).items() if not old],
            "diffs": dict(b["diffs"]),
            "contents": {p: b["proposals"][p] for p in b["diffs"]},
        } for b in self.agent.pending_batches]
        approved: list[dict] = []
        seen: set[str] = set()
        for b in reversed(self.agent.pending_batches):
            if b.get("status") not in ("applied", "partial"):
                continue
            for path in b.get("applied", {}):
                if path not in seen:
                    seen.add(path)
                    approved.append({
                        "file": path,
                        "label": b.get("label", ""),
                        "batch_id": b.get("id", ""),
                    })
        return {
            "pending_diffs": pending,
            "backlog": backlog,
            "approved_files": approved,
            "verified_identifiers": dict(list(self.agent.memory.verified_identifiers.items())[:200]),
            "files_modified": self.agent.memory.files_touched,
            "notes": self.agent.memory.notes[-50:],
            "scan_changed_files": len(self._scan().get("changed_files", [])),
        }

    def _workspace(self):
        from hoi4_agent.filesystem import workspace

        return workspace()

    def _scan(self):
        from hoi4_agent.project_scan import ProjectScan

        return ProjectScan().build()


def main() -> None:
    server = Hoi4Server()
    for line in sys.stdin:
        msg = None
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            result = server.handle(msg.get("method", ""), msg.get("params", {}))
            sys.stdout.write(json.dumps({"id": msg.get("id"), "result": result}) + "\n")
        except Exception as exc:  # noqa: BLE001
            sys.stdout.write(json.dumps({
                "id": msg.get("id") if msg else None,
                "error": {"code": -32603, "message": str(exc)},
            }) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
