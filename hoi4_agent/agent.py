# Owner: ACTIVE
"""The agent loop: plan -> inspect -> generate grounded patch -> validate ->
show diff -> approval -> apply. The model is only a reasoning layer; every
identifier decision is made by the deterministic tools."""

from __future__ import annotations

import json
import re
import time
import uuid
from collections import Counter
from pathlib import Path

from .config import CONFIG  # noqa: E402
from .filesystem import FilesystemError, read_file, read_text_keep, workspace  # noqa: E402
from .identifier_index import IdentifierIndex  # noqa: E402
from .intents import Intent, classify  # noqa: E402
from .memory import SessionMemory  # noqa: E402
from .merge import MergeEngine  # noqa: E402
from .planner import (COUNTRY_FILES, FEATURE_TASK_SETS,  # noqa: E402
                      MULTI_COUNTRY_FEATURES, STATUS_BY_TOOL, Planner)
from .project_scan import ProjectScan  # noqa: E402
from .refactor import RefactorEngine  # noqa: E402
from .repair import RepairEngine  # noqa: E402
from .tools import ToolContext, Tools  # noqa: E402
from .validator import Validator  # noqa: E402
from hoi4_agent._runtime.common import check_delimiters, read_json  # noqa: E402

REPAIR_HINTS = {
    "unknown_identifier": "replace with a verified identifier from the index",
    "broken_reference": "restore the referenced identifier or point to an existing one",
    "missing_localisation": "add the missing localisation key",
    "invalid_scope": "wrap the effect in the correct scope block",
    "brace_mismatch": "balance the braces",
    "invalid_effect": "correct the effect name to a documented effect",
    "invalid_trigger": "correct the trigger name to a documented trigger",
    "invalid_modifier": "use a documented modifier key",
    "duplicate_identifier": "rename the duplicate to a unique id",
    "duplicate_event_id": "rename the duplicate event id",
    "missing_required_block": "add the required block (id/options)",
    "unknown_icon": "use a verified vanilla GFX icon",
    "missing_sprite": "set a valid icon/sprite",
}


class Agent:
    def __init__(self, auto_approve: bool = False, use_model: bool | None = None):
        self.index = IdentifierIndex()
        effects_docs = read_json(CONFIG.index_dir / "effects.json")
        triggers_docs = read_json(CONFIG.index_dir / "triggers.json")
        modifiers_docs = read_json(CONFIG.index_dir / "modifiers.json")
        self.validator = Validator(self.index, effects_docs, triggers_docs, modifiers_docs)
        try:
            self.validator.register_workspace(ProjectScan().build())
        except Exception:  # noqa: BLE001 - a scan problem must not kill the agent
            pass
        self.memory = SessionMemory()
        self.ctx = ToolContext(self.index, self.validator, self.memory)
        self.tools = Tools(self.ctx)
        self.repair = RepairEngine(self.ctx, self.validator, self.tools, agent=self)
        self.planner = Planner()
        self.auto_approve = auto_approve
        self.use_model = CONFIG.use_model if use_model is None else use_model
        self.promptless = False
        self.pending: dict = {"proposals": {}, "diffs": {}, "originals": {}, "applied": {}}
        self.pending_batches: list[dict] = []
        self._load_backlog()
        self._model = None
        self._tokenizer = None

    # ------------------------------------------------------------------ run
    def run(self, request: str, approve: bool | None = None,
            active_file: str | None = None) -> dict:
        intent = classify(request)
        if intent == Intent.EXPLAIN:
            return self._pipeline_explain(request)
        if intent == Intent.REPAIR:
            return self._pipeline_repair(request)
        if intent == Intent.MERGE:
            return self._pipeline_merge(request)
        if intent == Intent.REFACTOR:
            return self._pipeline_refactor(request)
        if intent == Intent.MODIFY:
            from .project import ProjectExecutor

            from .snippets import SnippetEngine

            mod = SnippetEngine(self).modify(request)
            if mod is not None:
                proposals, reason = mod
                if proposals:
                    return self._snippet_result(request, proposals,
                                                 project_slug="modify")
                if reason == "already_set":
                    return {
                        "intent": "modify", "applied": False, "pending_files": [],
                        "summary": ("No change needed — that focus already has "
                                    "the requested value."),
                    }
                return {
                    "intent": "modify", "applied": False, "pending_files": [],
                    "summary": ("I could not find that focus in the workspace "
                                "— open or name the file that contains it."),
                }
            project = ProjectExecutor(self).create_project(request)
            if project.plan.feature == "unknown_country":
                return {
                    "intent": "modify", "applied": False, "pending_files": [],
                    "summary": (
                        "I could not identify what to modify. Name the country "
                        "or file, e.g. 'change the cost of the focus "
                        "GER_army_recruitment_bolster to 15'."
                    ),
                }
            return ProjectExecutor(self).run(project, auto_approve=self.auto_approve)
        if intent == Intent.CREATE:
            from .project import ProjectExecutor

            # Country-less code-snippet requests ("write a decision that...")
            # are answered by the deterministic snippet engine instead of
            # falling through to "unknown country".
            from .snippets import SnippetEngine

            snippet_engine = SnippetEngine(self)
            if not self.planner._detect_new_country(request) \
                    and snippet_engine.matches(request):
                snippets = snippet_engine.generate(request, active_file=active_file)
                if snippets is None:
                    return self._snippet_exists_result(request)
                if snippets:
                    return self._snippet_result(request, snippets,
                                                 active_file=active_file)
            countries = self.planner._extract_countries(request)
            if len(countries) > 1:
                probe = self.planner.plan_project(request)
                if probe.feature in MULTI_COUNTRY_FEATURES:
                    return ProjectExecutor(self).run_multi(
                        request, auto_approve=self.auto_approve)
            if self.planner._detect_new_country(request):
                probe = self.planner.plan_project(request)
                if not probe.politics:
                    return {
                        "intent": "new_country", "needs_input": "politics",
                        "summary": (
                            f"What ideology should {probe.new_country_name} be? "
                            "Reply e.g. 'democracy', 'communism', or "
                            "'20% fascist, 80% democratic'."
                        ),
                    }
                project = ProjectExecutor(self).create_project(request)
                project.plan.politics = probe.politics
                return ProjectExecutor(self).run(project, auto_approve=self.auto_approve)
            probe = self.planner.plan_project(request)
            if probe.feature == "unknown_country":
                return {
                    "intent": "unknown_country", "patch": None, "applied": False,
                    "summary": (
                        "I could not identify the country in your request. If it is a brand-new "
                        "country, say: create a new country called <Name>. If it exists only in "
                        "your mod, make sure it is defined in common/country_tags or "
                        "history/countries in the workspace, then try again."
                    ),
                }
            if probe.feature == "transfer_states":
                from .project import ProjectExecutor

                project = ProjectExecutor(self).create_project(request)
                return ProjectExecutor(self).run(project, auto_approve=self.auto_approve)
            if probe.feature == "focus_event":
                from .project import ProjectExecutor

                project = ProjectExecutor(self).create_project(request)
                return ProjectExecutor(self).run(project, auto_approve=self.auto_approve)
            if probe.feature == "focus_effects":
                if not probe.effect_spec:
                    return {
                        "intent": "focus_effects",
                        "needs_input": "effects",
                        "summary": (
                            "Which effects should the focuses give? Reply like "
                            "'50 political power to each focus'."
                        ),
                    }
                from .project import ProjectExecutor

                project = ProjectExecutor(self).create_project(request)
                project.plan.effect_spec = probe.effect_spec
                return ProjectExecutor(self).run(project, auto_approve=self.auto_approve)
            if probe.feature == "remove_content":
                if not probe.country_tag:
                    return {
                        "intent": "remove_content",
                        "needs_input": "country",
                        "summary": (
                            "Which country's content should I remove? Reply "
                            "like 'germany'."
                        ),
                    }
                from .project import ProjectExecutor

                project = ProjectExecutor(self).create_project(request)
                return ProjectExecutor(self).run(project, auto_approve=self.auto_approve)
            # Every remaining project feature (focus trees/branches, decisions,
            # events, ideas, advisors, civil-war paths, releasables, ...) goes
            # through the same ProjectExecutor the extension uses. The legacy
            # V1 planner fallback below is NOT used for CREATE requests: it
            # wrote to the real country focus file and regenerated duplicate
            # ids on every run.
            if probe.feature in FEATURE_TASK_SETS:
                from .project import ProjectExecutor

                if probe.feature == "oob":
                    missing = []
                    if not probe.country_tag:
                        missing.append(("country", "Which country should the OOB be for? "
                                                   "Reply like 'belgium' or 'BEL'."))
                    if not probe.division_name:
                        missing.append(("division_name", "What should the division(s) be "
                                       "called? (default: 'Infantry Division')"))
                    if probe.spawn is None:
                        missing.append(("spawn", "Should the divisions be spawned at the "
                                       "country's victory points? Reply 'spawn' or 'no'."))
                    if missing:
                        field, question = missing[0]
                        project = ProjectExecutor(self).create_project(request)
                        project.plan.division_name = probe.division_name
                        project.plan.spawn = probe.spawn
                        project.plan.oob_count = probe.oob_count
                        project.plan.unit_key = probe.unit_key
                        project.save()
                        return {
                            "intent": "oob", "needs_input": field,
                            "question": question, "summary": question,
                            "project_slug": project.slug,
                        }
                    project = ProjectExecutor(self).create_project(request)
                    project.plan.division_name = probe.division_name
                    project.plan.spawn = probe.spawn
                    project.plan.oob_count = probe.oob_count
                    project.plan.unit_key = probe.unit_key
                    project.save()
                    result = ProjectExecutor(self).run(project,
                                                       auto_approve=self.auto_approve)
                    self._prepare_pending(
                        project.proposals, label=project.plan.name or "Feature",
                        project_slug=project.slug)
                    pending_files = sorted(self.pending.get("proposals", {}))
                    summary = ("OOB ready — review the diffs and click Accept "
                               "to apply." if pending_files else
                               "No new changes — this OOB already exists.")
                    return {
                        "intent": "oob",
                        "status": result.get("status", "pending"),
                        "applied": result.get("applied", False),
                        "message": result.get("message", ""),
                        "pending_files": pending_files,
                        "summary": summary,
                    }
                project = ProjectExecutor(self).create_project(request)
                result = ProjectExecutor(self).run(project,
                                                   auto_approve=self.auto_approve)
                self._prepare_pending(
                    project.proposals,
                    label=project.plan.name or "Feature",
                    project_slug=project.slug)
                pending_files = sorted(self.pending.get("proposals", {}))
                if pending_files:
                    summary = ("Feature ready — review the diffs and click "
                               "Accept to apply.")
                else:
                    summary = ("No new changes — this feature already exists "
                               "in the workspace.")
                return {
                    "intent": probe.feature,
                    "status": result.get("status", "pending"),
                    "applied": result.get("applied", False),
                    "message": result.get("message", ""),
                    "pending_files": pending_files,
                    "summary": summary,
                }
        plan = self.planner.plan(request)
        log: list[dict] = []
        for step in plan.steps:
            tool = step["tool"]
            print("  " + STATUS_BY_TOOL.get(tool, "Working..."))
            if tool == "__generate_patch__":
                patch_data = self._generate_patch(request, plan)
                log.append({"tool": "generate_patch", "ok": bool(patch_data), "data": patch_data})
                continue
            if plan.intent in ("add_focus", "add_generic") and tool in (
                "validate_code", "validate_focus_tree", "validate_localisation",
                "show_diff", "apply_patch",
            ):
                continue  # handled by _handle_patch_workflow after inspection
            result = self.tools.call(tool, **step["args"])
            log.append({"tool": tool, "ok": result.ok, "message": result.message, "data": result.data})
            if tool in ("search_identifier",) and not result.ok:
                self.memory.reject_identifier(plan.parsed.get("country_tag", ""), result.message)

        # Build the proposed content from gathered facts for add intents.
        if plan.intent in ("add_focus", "add_generic"):
            return self._handle_patch_workflow(request, plan, log)
        return {
            "intent": plan.intent,
            "statuses": plan.statuses,
            "steps": log,
            "patch": None,
            "applied": False,
            "summary": _summarize(plan.intent, log),
        }

    def _snippet_result(self, request: str, snippets: dict[str, str],
                        active_file: str | None = None,
                        project_slug: str = "snippet") -> dict:
        """Stage a deterministic snippet for review (same approval flow as any
        other generated change)."""
        self._prepare_pending(snippets, label="Generated snippet",
                              project_slug=project_slug)
        targeted = active_file is not None and any(
            p.replace("\\", "/") == active_file.replace("\\", "/")
            for p in snippets)
        summary = "Generated snippet"
        if targeted:
            summary += f" — will append to {active_file}"
        summary += " — review the diffs and click Accept to apply."
        from .strict import check_budget, infer_budget

        budget = check_budget(infer_budget(request), snippets)
        return {
            "intent": "snippet",
            "patch": None,
            "applied": False,
            "pending_files": sorted(snippets),
            "summary": summary,
            "budget": budget,
        }

    def _snippet_exists_result(self, request: str) -> dict:
        """Idempotence: the requested object already exists -> no changes."""
        return {
            "intent": "snippet",
            "applied": False,
            "pending_files": [],
            "summary": ("This already exists in your mod — no duplicate was "
                        "generated and nothing was changed."),
        }

    # ------------------------------------------------------- V2 pipelines
    def _run_repair_on(self, proposals: dict[str, str], max_attempts: int = 5) -> tuple[bool, dict]:
        proposals, validation, log = self.repair.run_repair_loop(proposals, max_attempts=max_attempts)
        return validation["valid"], validation

    def _compute_diffs(self, proposals: dict[str, str]) -> str:
        from . import patcher
        from .filesystem import read_text_keep, workspace

        diffs = []
        for path, content in proposals.items():
            old = read_text_keep(workspace() / path) if (workspace() / path).exists() else ""
            if content != old:
                diffs.append(patcher.make_diff(path, old, content))
        return "\n".join(d for d in diffs if d.strip())

    def _prepare_pending(self, proposals: dict[str, str],
                         label: str = "", project_slug: str = "") -> dict:
        """Record a review batch and append it to the backlog.

        Unapproved batches accumulate instead of being replaced, so work is
        never lost when the user moves on to another prompt.
        """
        from . import patcher
        from .filesystem import read_text_keep, workspace

        batch = {
            "id": uuid.uuid4().hex[:8],
            "label": label or "Pending changes",
            "project_slug": project_slug or "",
            "status": "pending",
            "created_at": time.time(),
            "proposals": {}, "diffs": {}, "originals": {}, "applied": {},
        }
        for path, content in proposals.items():
            old = read_text_keep(workspace() / path) if (workspace() / path).exists() else ""
            if content == old:
                continue
            batch["proposals"][path] = content
            batch["originals"][path] = old
            batch["diffs"][path] = patcher.make_diff(path, old, content)
        self.pending = batch
        if batch["proposals"]:
            self.pending_batches.append(batch)
            self._save_backlog()
        return self.pending

    def _batch(self, batch_id: str | None) -> dict:
        if batch_id:
            for b in self.pending_batches:
                if b.get("id") == batch_id:
                    return b
            return {"id": batch_id, "status": "missing", "proposals": {},
                    "diffs": {}, "originals": {}, "applied": {}}
        return self.pending

    def approve_pending(self, file: str | None = None, batch_id: str | None = None,
                        approve_all: bool = False) -> dict:
        """Apply approved pending files.

        Only batches that are still pending (or partially applied) are ever
        touched — already-applied/rejected batches are never re-applied, so
        "Approve All Backlog" can't duplicate previously approved work.
        """
        if approve_all:
            batches = [b for b in self.pending_batches
                       if b.get("status") in ("pending", "partial")]
        elif batch_id:
            batch = self._batch(batch_id)
            if batch.get("status") not in ("pending", "partial"):
                return {"applied": [], "failed": [],
                        "message": f"batch {batch_id} is already applied or rejected"}
            batches = [batch]
        else:
            batches = ([self.pending]
                       if self.pending.get("status") in ("pending", "partial") else [])
        from .filesystem import read_text_keep, workspace
        from .snippets import merge_snippet_text

        applied: list[str] = []
        failed: list[str] = []
        for batch in batches:
            already = set(batch.get("applied", {}))
            targets = [file] if file else [p for p in batch["proposals"] if p not in already]
            for path in targets:
                if path not in batch["proposals"]:
                    continue
                if not batch["proposals"][path].strip():
                    # Empty proposal means deletion.
                    target = workspace() / path
                    if target.exists():
                        target.unlink()
                        batch["applied"][path] = batch["originals"].get(path, "")
                        if path not in applied:
                            applied.append(path)
                    continue
                current = read_text_keep(workspace() / path) \
                    if (workspace() / path).exists() else ""
                content = batch["proposals"][path]
                is_loc = path.endswith(".yml")
                if current and (is_loc or batch.get("project_slug") == "snippet") \
                        and current.strip() and current.strip() not in content:
                    # Snippet batches are incremental and yml batches are
                    # key-wise: when several are approved together, merge into
                    # the current file instead of applying stale stage-time
                    # diffs (which would append duplicates or clobber earlier
                    # batches).
                    # When the proposal already contains the current content
                    # (generate-time merge), replace directly.
                    content = merge_snippet_text(current, content,
                                                 is_loc)
                    if content == current:
                        batch["applied"][path] = batch["originals"].get(path, "")
                        continue
                if content == current:
                    batch["applied"][path] = batch["originals"].get(path, "")
                    continue
                from . import patcher
                d = patcher.make_diff(path, current, content)
                if not d.strip():
                    continue
                res = self.tools.apply_patch(path, d)
                if res.ok:
                    batch["applied"][path] = batch["originals"].get(path, "")
                    if path not in applied:
                        applied.append(path)
                else:
                    failed.append(f"{path}: {res.message}")
            remaining = [p for p in batch["proposals"] if p not in batch.get("applied", {})]
            if not remaining:
                batch["status"] = "applied"
            elif batch.get("applied"):
                batch["status"] = "partial"
        self._save_backlog()
        return {"applied": applied, "failed": failed}

    def reject_pending(self, file: str = "", batch_id: str | None = None) -> dict:
        """Drop a file (or the whole batch when file='') without writing."""
        batch = self._batch(batch_id)
        if batch.get("status") in ("applied", "rejected"):
            return {"rejected": file or "batch", "remaining": list(batch["proposals"]),
                    "message": "batch already applied or rejected"}
        if file:
            for key in ("proposals", "diffs", "originals"):
                batch[key].pop(file, None)
        else:
            batch["proposals"].clear()
            batch["diffs"].clear()
            batch["originals"].clear()
        if not batch["proposals"] and batch.get("status") == "pending":
            batch["status"] = "rejected"
        self._save_backlog()
        return {"rejected": file or "batch", "remaining": list(batch["proposals"])}

    def undo_applied(self, file: str) -> dict:
        """Restore the pre-apply content for a file (undo support)."""
        from .filesystem import write_text, workspace

        for batch in reversed(self.pending_batches):
            original = batch["applied"].pop(file, None)
            if original is not None:
                if original:
                    write_text(file, original)
                else:
                    # The file did not exist before apply: undo means deleting it.
                    (workspace() / file).unlink(missing_ok=True)
                self._save_backlog()
                return {"undone": True, "file": file}
        return {"undone": False, "reason": "no undo snapshot"}

    def _save_backlog(self) -> None:
        try:
            path = CONFIG.memory_dir / "pending_backlog.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            data = [{
                "id": b["id"], "label": b.get("label", ""),
                "project_slug": b.get("project_slug", ""),
                "status": b.get("status", "pending"),
                "created_at": b.get("created_at", 0),
                "proposals": b["proposals"], "originals": b["originals"],
                "applied": b["applied"],
            } for b in self.pending_batches]
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8", errors="surrogateescape")
        except OSError:
            pass

    def _load_backlog(self) -> None:
        from . import patcher
        from .filesystem import read_text_keep, workspace

        try:
            path = CONFIG.memory_dir / "pending_backlog.json"
            if not path.exists():
                return
            data = json.loads(path.read_text(encoding="utf-8",
                                             errors="surrogateescape"))
        except (OSError, ValueError):
            return
        for item in data:
            batch = {
                "id": item.get("id", ""), "label": item.get("label", ""),
                "project_slug": item.get("project_slug", ""),
                "status": item.get("status", "pending"),
                "created_at": item.get("created_at", 0),
                "proposals": item.get("proposals", {}),
                "originals": item.get("originals", {}),
                "applied": item.get("applied", {}),
                "diffs": {},
            }
            for path, content in batch["proposals"].items():
                old = read_text_keep(workspace() / path) \
                    if (workspace() / path).exists() else ""
                batch["diffs"][path] = patcher.make_diff(path, old, content)
            self.pending_batches.append(batch)
        if self.pending_batches:
            self.pending = self.pending_batches[-1]

    def _apply_proposals(self, proposals: dict[str, str]) -> tuple[bool, str]:
        from . import patcher
        from .filesystem import workspace

        if self.promptless:
            self._prepare_pending(proposals)
            return False, "pending review"
        diff = self._compute_diffs(proposals)
        if not diff.strip():
            return False, "no changes"
        approved = self.auto_approve or CONFIG.auto_approve or self._ask_approval(diff)
        if not approved:
            self._prepare_pending(proposals)
            return False, "approval declined"
        failures = []
        for path, content in proposals.items():
            full = workspace() / path
            old = read_text_keep(full) if full.exists() else ""
            if not content.strip():
                if full.exists():
                    full.unlink()
                continue
            if content != old:
                d = patcher.make_diff(path, old, content)
                res = self.tools.apply_patch(path, d)
                if not res.ok:
                    failures.append(f"{path}: {res.message}")
        if failures:
            return False, "apply failed: " + "; ".join(failures)
        return True, "applied"

    def _ask_approval(self, diff: str) -> bool:
        print(diff)
        answer = input("\nApply this patch? [y/N] ").strip().lower()
        return answer in ("y", "yes")

    def _pipeline_repair(self, request: str) -> dict:
        ProjectScan().build()
        errors: list[dict] = []
        # full-workspace basic validity scan (any file type, incl. AI strategy)
        for f in workspace().rglob("*"):
            if not f.is_file() or "localisation" in f.as_posix():
                continue
            rel = f.relative_to(workspace()).as_posix()
            text = f.read_text(encoding="utf-8", errors="replace")
            ok, msg = check_delimiters(text)
            if not ok:
                errors.append(self.validator._err("brace_mismatch",
                                                  f"unbalanced delimiters: {msg}", file=rel))
            if rel.startswith("events/"):
                from hoi4_agent._runtime.hoi4parser import parse_tree

                self.validator._check_required_blocks(rel, text, parse_tree(text), errors)
        for v in (self.validator.validate_focus_tree(), self.validator.validate_events(),
                  self.validator.validate_localisation()):
            errors.extend(v.get("errors", []))
            errors.extend(w for w in v.get("warnings", []) if w.get("type") == "missing_localisation")
        if not errors:
            return {"intent": "repair", "patch": None, "applied": False,
                    "summary": "No validator errors found in the workspace; nothing to repair."}
        files = sorted({e["file"] for e in errors if e.get("file")})
        if any(e["type"] == "missing_localisation" for e in errors):
            loc_files = [f for f in files if "localisation" in f]
            if not loc_files:
                loc_base = workspace() / "localisation" / "english"
                if loc_base.exists():
                    files.extend(f.relative_to(workspace()).as_posix()
                                 for f in loc_base.glob("*.yml"))
        proposals = {}
        for f in files:
            p = Path(f)
            full = p if p.is_absolute() else workspace() / f
            proposals[f] = read_text_keep(full) if full.exists() else ""
        # Deterministic repair for duplicate localisation keys: rebuild the
        # affected yml files with duplicates removed.
        if any(e["type"] == "duplicate_identifier" and e.get("file", "").endswith(".yml")
               for e in errors):
            from .snippets import dedupe_loc_keys

            loc_base = workspace() / "localisation" / "english"
            if loc_base.exists():
                for f in sorted(loc_base.glob("*.yml")):
                    rel = f.relative_to(workspace()).as_posix()
                    text = read_text_keep(f)
                    deduped = dedupe_loc_keys(text)
                    if deduped != text:
                        proposals[rel] = deduped
                        if rel not in files:
                            files.append(rel)
        valid, validation = self._run_repair_on(proposals)
        from .repair import convert_known_syntax

        proposals = convert_known_syntax(proposals)
        valid, validation = self._run_repair_on(proposals)
        applied = False
        msg = "not applied (remaining validator issues)"
        if valid:
            applied, msg = self._apply_proposals(proposals)
        by_type = Counter(e["type"] for e in errors)
        summary = (f"Repaired {len(files)} file(s): {dict(by_type)}. " +
                   ("All validators pass." if valid else
                    f"Remaining issues: {self._format_errors(validation['errors'])}") +
                   f" Apply: {msg}.")
        return {"intent": "repair", "patch": None, "applied": applied, "summary": summary,
                "errors_found": dict(by_type),
                "pending_files": list(self.pending["proposals"]) if not applied else []}

    def _pipeline_merge(self, request: str) -> dict:
        scan = ProjectScan().build()
        candidates = [rel for rel, kind in scan["graphs"]["file_graph"].items()
                      if kind in ("focus", "event", "decision", "loc")]
        engine = MergeEngine(self.index)
        result = engine.merge(candidates)
        proposals = result["proposals"]
        valid, validation = self._run_repair_on(proposals)
        applied, msg = self._apply_proposals(proposals)
        report = result["report"]
        summary = (
            f"Merged {len(report['files'])} file(s): {report['blocks_total']} blocks; "
            f"{len(report['duplicates'])} duplicate(s) ({sum(1 for d in report['duplicates'] if d['identical'])} identical, kept first); "
            f"{len(report['remaining_conflicts'])} conflicting definition(s) need review; "
            f"{len(report['loc_conflicts'])} localisation conflict(s). "
            f"Apply: {msg}."
        )
        return {"intent": "merge", "patch": None, "applied": applied, "summary": summary,
                "merge_report": report,
                "pending_files": list(self.pending["proposals"]) if not applied else []}

    def _pipeline_refactor(self, request: str) -> dict:
        ProjectScan().build()
        analysis = RefactorEngine(self.index).analyze()
        dup_files = {occ["file"] for b in analysis["duplicate_blocks"] for occ in b["occurrences"]}
        if not dup_files and not analysis["suggestions"]:
            return {"intent": "refactor", "patch": None, "applied": False,
                    "summary": "No duplicated code detected."}
        proposals = {}
        for f in dup_files:
            full = workspace() / f
            proposals[f] = full.read_text(encoding="utf-8",
                                          errors="surrogateescape")
        proposals, removed = RefactorEngine(self.index).dedupe(proposals)
        valid, validation = self._run_repair_on(proposals)
        applied, msg = self._apply_proposals(proposals)
        summary = (f"Refactor: removed {removed} duplicate block(s); "
                   f"{len(analysis['suggestions'])} reusable scripted-effect suggestion(s) "
                   f"({[s['name'] for s in analysis['suggestions']]}). Apply: {msg}.")
        return {"intent": "refactor", "patch": None, "applied": applied, "summary": summary,
                "suggestions": analysis["suggestions"],
                "pending_files": list(self.pending["proposals"]) if not applied else []}

    def _pipeline_explain(self, request: str) -> dict:
        """Explain a clicked preview node or a user-asked identifier.

        Map clicks (province / state / strategic region / supply area) are
        answered directly from grounded map data. Identifier requests only
        show validator output when the request itself points at an error —
        otherwise unrelated workspace noise (e.g. duplicate keys in a big
        mod) would drown the answer.
        """
        scan = ProjectScan().build()
        low = request.lower()

        # --- map-click context -------------------------------------------
        m = re.search(
            r"\b(province|state|strategic region|strategic_region|supply area|supply_area)\b"
            r"[^\d`A-Z]{0,30}`?(\d+)`?",
            low,
        )
        if m:
            kind = {"strategic region": "strategic_region",
                    "strategic_region": "strategic_region",
                    "supply area": "supply_area",
                    "supply_area": "supply_area"}.get(m.group(1), m.group(1))
            num = int(m.group(2))
            from .preview import map_preview

            if kind == "province":
                info = map_preview.province_info(num)
                if not info.get("ok"):
                    return {"intent": "explain", "patch": None, "applied": False,
                            "summary": info.get("message", f"province {num} unknown")}
                lines = [f"Province {num}: {info.get('type', '?')} terrain"
                         f"{', coastal' if info.get('coastal') else ''}"]
                if info.get("state"):
                    lines.append(f"State {info['state']}"
                                 + (f" ({info.get('state_name')})" if info.get("state_name") else "")
                                 + (f" — owned by {info['owner']}" if info.get("owner") else " — unowned"))
                if info.get("terrain"):
                    lines.append(f"Terrain: {info['terrain']}")
                return {"intent": "explain", "patch": None, "applied": False,
                        "summary": "\n".join(lines)}
            if kind == "state":
                info = map_preview.state_info(num)
            elif kind == "strategic_region":
                info = map_preview.strategic_region_info(num)
            else:
                info = map_preview.supply_area_info(num)
            if not info.get("ok"):
                return {"intent": "explain", "patch": None, "applied": False,
                        "summary": info.get("message", f"{kind} {num} unknown")}
            return {"intent": "explain", "patch": None, "applied": False,
                    "summary": self._format_map_info(kind, info)}

        # --- identifier requests -----------------------------------------
        idents = [a or b for a, b in re.findall(r"`([A-Za-z0-9_.]+)`|\b([A-Z]{2,4}_[A-Za-z0-9_]+)\b", request)]
        m = re.search(r'"identifier"\s*:\s*"([A-Za-z0-9_.]+)"', request)
        if m and m.group(1) not in idents:
            idents.append(m.group(1))
        parts: list[str] = []
        for ident in idents[:3]:
            res = self.tools.search_identifier(ident)
            if res.ok:
                parts.append(f"`{ident}` verified: " + "; ".join(
                    f"{r['label']} ({r['source']})" for r in res.data["results"]))
            else:
                similar = [s["identifier"] for s in res.data.get("similar", [])][:3]
                parts.append(f"`{ident}` could not be verified" + (f"; similar: {similar}" if similar else ""))
        dep = scan.get("graphs", {}).get("dependency_graph", {})
        for ident in idents[:3]:
            refs = [rel for rel, refmap in dep.items() if ident in refmap]
            if refs:
                parts.append(f"`{ident}` is referenced by: {refs}")
        et = re.search(r'"type"\s*:\s*"([a-z_]+)"', request)
        if et:
            parts.append(f"error type: {et.group(1)}; smallest repair: "
                         f"{REPAIR_HINTS.get(et.group(1), 'fix the reported block and re-validate')}")
        errors: list[dict] = []
        # Only run/attach validator output when the user asked about an error.
        if et or re.search(r"\b(errors?|failing|broken|invalid|validator)\b", low) \
                or '"file"' in request:
            for v in (self.validator.validate_focus_tree(), self.validator.validate_events(),
                      self.validator.validate_localisation()):
                errors.extend(v.get("errors", []))
            if errors:
                e = errors[0]
                parts.append(f"validator output: [{e['type']}] {e['message']} "
                             f"(file={e.get('file')}, line={e.get('line')})")
        if not parts or (not idents and not et and not errors):
            hit = self._wiki_lookup(request)
            if hit:
                parts.append(f"reference ({hit[0]}): {hit[1]}")
            else:
                sw = self.tools.search_wiki(request)
                if sw.ok and sw.data.get("snippets"):
                    parts.append("reference: " + sw.data["snippets"][0]["content"][:220])
        summary = "\n".join(parts) if parts else "No issues, identifiers, or references found."
        return {"intent": "explain", "patch": None, "applied": False, "summary": summary}

    @staticmethod
    def _format_map_info(kind: str, info: dict) -> str:
        lines = [f"{kind.replace('_', ' ').title()} {info['id']}"]
        if info.get("name"):
            lines.append(f"Name: {info['name']}")
        if info.get("owner"):
            lines.append(f"Owner: {info['owner']}")
        if info.get("province_count") is not None:
            lines.append(f"Provinces: {info['province_count']}")
        if info.get("state_count") is not None:
            lines.append(f"States: {info['state_count']}")
        return "\n".join(lines)

    def _wiki_lookup(self, request: str) -> tuple[str, str] | None:
        """Scored line match over wiki pages; prefers the Troubleshooting page."""
        terms = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9._-]+", request)
                 if w.lower() not in {"where", "are", "which", "what", "does", "the",
                                      "this", "that", "with", "for", "and", "is"} and len(w) > 2]
        terms = [t for t in terms if t.lower() not in {"file", "console", "command", "do", "testing"}]
        best: tuple[str, str, int, bool] | None = None
        for f in sorted(CONFIG.wiki_dir.glob("*.md")):
            name = f.name.lower()
            if "troubleshooting" not in name and "localisation" not in name:
                continue
            preferred = "troubleshooting" in name
            text = f.read_text(encoding="utf-8", errors="ignore")
            for line in text.splitlines():
                low = line.lower()
                score = sum(1 for t in terms if t.lower() in low)
                if score == 0:
                    continue
                if best is None or score > best[2] or (score == best[2] and preferred and not best[3]):
                    best = (f.name, line.strip()[:220], score, preferred)
        return (best[0], best[1]) if best else None

    # -------------------------------------------------------------- patch flow
    def _handle_patch_workflow(self, request: str, plan, log: list[dict]) -> dict:
        generated = self._generate_patch(request, plan)
        if not generated:
            return {"intent": plan.intent, "steps": log, "patch": None, "applied": False,
                    "summary": "Could not generate a grounded patch. No changes made."}
        new_content, loc_lines, target_path = generated

        # Merge into the workspace files (focus tree + localisation).
        try:
            old = read_file(target_path)["content"] if _file_exists(target_path) else ""
        except FilesystemError:
            old = ""
        tag = plan.parsed["country_tag"]
        focus_name = COUNTRY_FILES.get(tag, tag.lower() + ".txt")
        if old.strip():
            merged_focus = old.rstrip() + "\n\n" + new_content.strip() + "\n"
        else:
            # No existing tree in the workspace: emit a complete valid tree
            # instead of a bare `focus = {...}` block.
            tree_id = f"{tag}_reward_agent_tree"
            merged_focus = (
                "focus_tree = {\n"
                f"\tid = {tree_id}\n"
                "\tcountry = {\n"
                "\t\tfactor = 0\n"
                "\t\tmodifier = {\n"
                "\t\t\tadd = 10\n"
                f"\t\t\ttag = {tag}\n"
                "\t\t}\n"
                "\t}\n"
                + new_content.strip() + "\n"
                "}\n"
            )
        loc_path = f"localisation/english/{focus_name.replace('.txt', '_l_english.yml')}"
        try:
            old_loc = read_file(loc_path)["content"] if _file_exists(loc_path) else ""
        except FilesystemError:
            old_loc = ""
        merged_loc = (old_loc.rstrip() + "\n" + loc_lines) if old_loc.strip() else "l_english:\n" + loc_lines

        proposals = {target_path: merged_focus, loc_path: merged_loc}
        proposals, validation, repair_log = self.repair.run_repair_loop(
            proposals, max_attempts=5
        )
        self._save_repair_log(repair_log, plan)
        if not validation["valid"]:
            return {
                "intent": plan.intent, "steps": log, "patch": None, "applied": False,
                "summary": "Repair loop exhausted 5 attempts. " + self._format_errors(validation["errors"]),
                "validation": validation, "repair_log": [r.__dict__ for r in repair_log],
            }
        merged_focus = proposals[target_path]
        merged_loc = proposals[loc_path]
        diff_focus = self.tools.propose_patch(target_path, merged_focus).data.get("diff", "")
        diff_loc = self.tools.propose_patch(loc_path, merged_loc).data.get("diff", "")
        diff = diff_focus + diff_loc

        show = self.tools.show_diff(diff).data["diff"]
        validators = {
            "validate_proposal": validation,
        }
        approved = self.auto_approve
        if not approved and not CONFIG.auto_approve:
            approved = self._ask_approval(show)
        applied = False
        if approved:
            for d in (diff_focus, diff_loc):
                if d.strip():
                    res = self.tools.apply_patch(target_path if d == diff_focus else loc_path, d)
                    if not res.ok:
                        return {"intent": plan.intent, "steps": log, "patch": diff, "applied": False,
                                "summary": f"Apply failed: {res.message}", "validation": validators}
            applied = True
            self.memory.save()
        else:
            # Keep the validated proposal in the pending queue so the UI can
            # show Accept / Reject without regenerating anything.
            self._prepare_pending(proposals)
        return {
            "intent": plan.intent,
            "statuses": plan.statuses,
            "steps": log,
            "patch": diff,
            "diff": show,
            "validators": validators,
            "applied": applied,
            "pending_files": list(self.pending["proposals"]) if not applied else [],
            "repair_attempts": len(repair_log),
            "summary": (
                f"Applied focus `{plan.parsed['new_id']}` to {target_path} "
                f"and localisation to {loc_path}." if applied else
                "Diff ready; not applied (no approval)."
            ),
        }

    def _save_repair_log(self, repair_log, plan) -> None:
        import json
        import time

        path = CONFIG.memory_dir / "repair_log.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            for r in repair_log:
                fh.write(json.dumps({
                    "ts": time.time(),
                    "intent": plan.intent,
                    "attempt": r.attempt,
                    "validator_errors": r.validator_errors,
                    "tool_calls": r.tool_calls,
                    "model_response": r.model_response[:2000],
                    "diffs": r.diffs[:2000],
                    "elapsed_sec": r.elapsed_sec,
                    "success": r.success,
                }, ensure_ascii=False) + "\n")

    @staticmethod
    def _format_errors(errors: list[dict]) -> str:
        return "; ".join(f"[{e.get('type')}] {e.get('message')}" for e in errors[:10])

    # --------------------------------------------------------- patch generation
    def _generate_patch(self, request: str, plan) -> tuple[str, str, str] | None:
        tag = plan.parsed.get("country_tag", "GER")
        effect = plan.parsed.get("effect", "add_political_power")
        amount = plan.parsed.get("amount", 50.0)
        # Verify country tag exists; otherwise refuse.
        country_hits = self.index.exact(tag)
        if not country_hits:
            self.memory.reject_identifier(tag, "not in vanilla countries index")
            return None
        self.memory.verify_identifier(tag, f"country tag ({country_hits[0]['source']})")
        # Verify effect against official docs.
        if effect not in self.validator.effects:
            self.memory.reject_identifier(effect, "not in official effects documentation")
            return None
        scopes = self.validator.effects[effect].get("scopes", [])
        if "COUNTRY" not in scopes:
            self.memory.reject_identifier(effect, f"requires scopes {scopes}, not usable in a country-scope focus reward")
            return None
        self.memory.verify_identifier(effect, f"effect, scopes: {scopes}")

        new_id = self._fresh_id(tag, "reward")
        plan.parsed["new_id"] = new_id
        if effect in ("add_stability", "add_war_support") and amount >= 1:
            amount = amount / 100.0
        amount_txt = _format_amount(amount)
        icon = self._focus_icon(effect)
        icon_line = f"\ticon = {icon}\n" if icon else ""
        focus = (
            f"focus = {{\n"
            f"\tid = {new_id}\n"
            f"\tcost = 10\n"
            f"{icon_line}"
            f"\tx = 0\n"
            f"\ty = 0\n"
            f"\tcompletion_reward = {{\n"
            f"\t\t{effect} = {amount_txt}\n"
            f"\t}}\n"
            f"}}\n"
        )
        loc = f' {new_id}:0 "Agent {tag} Reward Focus"\n {new_id}_desc:0 "A focus created by the HOI4 coding agent."\n'
        target = plan.target_files[0] if plan.target_files else f"common/national_focus/{COUNTRY_FILES.get(tag, tag.lower() + '.txt')}"
        return focus, loc, target

    def _focus_icon(self, reward: str) -> str:
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

    def _fresh_id(self, tag: str, slug: str) -> str:
        base = f"{tag}_{slug}_agent"
        n = 0
        while True:
            candidate = base if n == 0 else f"{base}_{n}"
            if not (self.index.contains(candidate) or _id_in_workspace(candidate)):
                return candidate
            n += 1

    # ---------------------------------------------------------------- model
    def _load_model(self):
        if self._model is not None:
            return
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self._tokenizer = AutoTokenizer.from_pretrained(CONFIG.model_id, local_files_only=True)
        self._model = AutoModelForCausalLM.from_pretrained(
            CONFIG.model_id, dtype=torch.float16, local_files_only=True
        ).to("cuda")
        self._model.eval()

    def generate_with_model(self, prompt: str, max_new_tokens: int = 400) -> str:
        """Optional reasoning layer for free-form content (never for identifiers)."""
        if not self.use_model:
            return ""
        self._load_model()
        import torch

        messages = [{"role": "system", "content": (
            "You are an HOI4 coding agent. Use ONLY identifiers from the provided "
            "context. If an identifier is not listed, write UNVERIFIED instead of inventing it."
        )}, {"role": "user", "content": prompt}]
        text = self._tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = self._tokenizer(text, return_tensors="pt").to("cuda")
        with torch.no_grad():
            out = self._model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False,
                                       pad_token_id=self._tokenizer.pad_token_id or self._tokenizer.eos_token_id)
        return self._tokenizer.decode(out[0, inputs["input_ids"].shape[1]:], skip_special_tokens=True)


def _file_exists(rel: str) -> bool:
    return (workspace() / rel).exists()


def _id_in_workspace(ident: str) -> bool:
    focus_dir = workspace() / "common" / "national_focus"
    for f in focus_dir.glob("*.txt") if focus_dir.exists() else []:
        if ident in f.read_text(encoding="utf-8", errors="ignore"):
            return True
    return False


def _format_amount(amount: float) -> str:
    if amount == int(amount):
        return str(int(amount))
    return f"{amount:.2f}"


def _summarize(intent: str, log: list[dict]) -> str:
    if intent == "validate":
        ok = all(s["ok"] for s in log)
        return "All validators passed." if ok else "Validation found problems: " + "; ".join(
            s["message"] for s in log if not s["ok"]
        )
    if intent == "explain":
        parts = []
        for s in log:
            if s["tool"] == "search_identifier" and s["ok"]:
                for r in s["data"].get("results", []):
                    parts.append(f"{r['identifier']} ({r['label']}, {r['source']})")
            elif not s["ok"] and s["data"].get("similar"):
                parts.append(f"{s['message']} similar: "
                             + ", ".join(x["identifier"] for x in s["data"]["similar"]))
        return "Identifiers found: " + "; ".join(parts) if parts else "No identifiers matched."
    return "Inspected workspace and vanilla references."
