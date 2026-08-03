"""Generator smoke tests: every emitted file must follow vanilla HOI4 syntax.

These tests run the generators in memory (proposals only) and never write to
the workspace. They exist because the generators once produced files that
loaded in our validator but crashed the game (decisions wrapper, anonymous
AI strategy blocks, ideas without modifier wrappers, overwritten focus trees,
invalid experience effects, .tmp files, missing BOMs).
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from hoi4_agent.agent import Agent  # noqa: E402
from hoi4_agent.planner import Planner  # noqa: E402
from hoi4_agent.project import ProjectExecutor, load_built_countries, remember_built_country  # noqa: E402


class GeneratorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from hoi4_agent.project import Project

        # Keep data/projects clean while running generator tests (plans are
        # exercised in memory; no project should persist to disk).
        cls._orig_save = Project.save
        Project.save = lambda self: None  # type: ignore[method-assign]
        cls.agent = Agent(auto_approve=True, use_model=False)
        cls.agent._ask_approval = lambda diff: False  # never block on stdin
        cls.ex = ProjectExecutor(cls.agent)

    @classmethod
    def tearDownClass(cls):
        from hoi4_agent.project import Project

        Project.save = cls._orig_save  # type: ignore[method-assign]

    def _project(self, request: str):
        return self.ex.create_project(request)

    def _gen(self, project, task_id: str) -> dict[str, str]:
        return self.ex._task_generator(project, task_id)()

    def test_civil_war_focus_tree_is_focus_only(self):
        proj = self._project(
            "Make a communist focus tree for Germany with 15 focuses, "
            "including a civil war branch.")
        self.assertEqual(proj.plan.feature, "focus_branch")
        task_ids = [t.id for t in proj.plan.tasks]
        self.assertEqual(task_ids, ["focuses", "localisation", "validate", "apply"])
        focuses = self._gen(proj, "focuses")
        self.assertEqual(len(focuses), 1)
        text = next(iter(focuses.values()))
        # the tree is one focus_tree block with 15 focuses...
        self.assertEqual(text.count("focus_tree = {"), 1)
        self.assertEqual(text.count("\tfocus = {"), 15)
        # ...the civil-war branch ends with start_civil_war...
        self.assertIn("start_civil_war = {", text)
        self.assertIn("ideology = communism", text)
        # ...and no events/ideas/decisions/ai_strategy/references are made.
        self.assertNotIn("country_event", text)
        self.assertNotIn("add_ideas", text)
        self.assertNotIn("add_timed_idea", text)
        for task in ("events", "ideas", "decisions", "ai_strategy", "references"):
            self.assertIsNone(proj.plan.task(task), task)

    def test_decisions_format(self):
        proj = self._project("add a focus tree with decisions for Canada")
        out = self._gen(proj, "decisions")
        content = next(iter(out.values()))
        self.assertFalse(content.startswith("decisions = {"))
        self.assertNotIn("\tcountry = {", content)
        self.assertIn("can_agent_decisions = {", content)
        self.assertIn("available = { always = yes }", content)
        self.assertIn("complete_effect = {", content)
        self.assertNotIn("add_political_power", content)
        self.assertNotIn("add_stability", content)

    def test_ai_strategy_named_block(self):
        proj = self._project("add a focus tree with ai strategy for Canada")
        out = self._gen(proj, "ai_strategy")
        content = next(iter(out.values()))
        self.assertNotIn("ai_strategy = {\n\t{", content)
        self.assertIn("_agent_strategy = {", content)
        self.assertIn("allowed = {", content)
        self.assertIn("enable = { always = yes }", content)
        self.assertIn("abort = { always = no }", content)
        self.assertIn("ai_strategy = {\n\t\ttype = balance", content)

    def test_ideas_modifier_wrapper(self):
        proj = self._project("add a focus tree with ideas for Canada")
        out = self._gen(proj, "ideas")
        content = next(iter(out.values()))
        self.assertIn("ideas = {\n\tcountry = {", content)
        self.assertNotIn("political = {", content)
        # Blank modifier blocks: no modifier keys until a later prompt adds them.
        self.assertIn("modifier = {\n\t\t\t}", content)
        self.assertNotIn("political_power_gain", content)

    def test_decisions_only_plan(self):
        proj = self._project("Add decisions for chile")
        self.assertEqual(proj.plan.feature, "decisions")
        self.assertEqual([t.id for t in proj.plan.tasks],
                         ["decisions", "localisation", "validate", "apply"])
        out = self._gen(proj, "decisions")
        content = next(iter(out.values()))
        self.assertIn("complete_effect = {\n\t\t}", content)
        self.assertNotIn("add_", content)

    def test_ideas_only_plan_blank_modifiers(self):
        proj = self._project("add national spirits for chile")
        self.assertEqual(proj.plan.feature, "ideas")
        self.assertEqual([t.id for t in proj.plan.tasks],
                         ["ideas", "localisation", "validate", "apply"])
        out = self._gen(proj, "ideas")
        content = next(iter(out.values()))
        self.assertIn("modifier = {\n\t\t\t}", content)
        self.assertNotIn(" = 0.", content)

    def test_focus_with_ideas_planning(self):
        plan = self.ex.planner.plan_project(
            "Add a fascist focus for Brazil that grants a national spirit "
            "boosting stability and factory output.")
        self.assertEqual(plan.feature, "focus_with_ideas")
        self.assertEqual(plan.country_tag, "BRA")
        self.assertEqual([t.id for t in plan.tasks],
                         ["ideas", "focuses", "localisation", "validate", "apply"])

    def test_focus_grants_requested_national_spirit(self):
        proj = self._project(
            "Add a fascist focus for Brazil that grants a national spirit "
            "boosting stability and factory output.")
        ideas = next(iter(self._gen(proj, "ideas").values()))
        # The first (granted) spirit carries the explicitly requested boosts.
        self.assertIn("stability_factor = 0.05", ideas)
        self.assertIn("industrial_capacity_factory = 0.05", ideas)
        focuses = next(iter(self._gen(proj, "focuses").values()))
        blocks = self.ex._focus_blocks(focuses)
        last = focuses[blocks[-1][1]:blocks[-1][2]]
        self.assertIn("add_ideas = {", last)
        others = [focuses[b[1]:b[2]] for b in blocks[:-1]]
        self.assertFalse(any("add_ideas" in seg for seg in others))

    def test_requested_focus_count(self):
        self.assertEqual(self.ex._requested_focus_count(
            self._project("make a focus tree with 15 focuses for germany")), 15)
        self.assertEqual(self.ex._requested_focus_count(
            self._project("make a 10 focus tree for germany")), 10)
        self.assertIsNone(self.ex._requested_focus_count(
            self._project("make a focus tree for germany")))
        self.assertEqual(self.ex._requested_focus_count(
            self._project("make a focus tree with 200 focuses for germany")), 40)

    def test_civil_war_tree_honors_requested_focus_count(self):
        proj = self._project(
            "Make a communist focus tree for Germany with 15 focuses, "
            "including a civil war branch.")
        # A focus-tree request with a civil-war branch is focus-only (no
        # ideas/events/decisions/ai_strategy); the branch ends in
        # start_civil_war, not a generated event.
        self.assertEqual(proj.plan.feature, "focus_branch")
        out = self._gen(proj, "focuses")
        content = next(iter(out.values()))
        blocks = self.ex._focus_blocks(content)
        self.assertEqual(len(blocks), 15)
        last = content[blocks[-1][1]:blocks[-1][2]]
        self.assertIn("start_civil_war = {", last)
        self.assertIn("ideology = communism", last)
        self.assertNotIn("country_event", content)
        # No effects are auto-added unless explicitly requested.
        for key in ("army_experience", "add_political_power", "navy_experience"):
            self.assertNotIn(key, content)

    def test_first_focus_has_no_prerequisite_in_new_tree(self):
        proj = self._project(
            "Make a communist focus tree for Germany with 5 focuses, "
            "including a civil war branch.")
        out = self._gen(proj, "focuses")
        content = next(iter(out.values()))
        blocks = self.ex._focus_blocks(content)
        self.assertEqual(len(blocks), 5)
        first = content[blocks[0][1]:blocks[0][2]]
        self.assertNotIn("prerequisite", first,
                         "the root focus of a new tree must have no prerequisite")
        second = content[blocks[1][1]:blocks[1][2]]
        self.assertIn("prerequisite", second,
                      "subsequent focuses still chain from the previous one")

    def test_remove_effects_planning(self):
        from hoi4_agent.intents import Intent, classify

        req = "remove the effects from germany's focus tree"
        self.assertEqual(classify(req), Intent.CREATE)
        plan = self.ex.planner.plan_project(req)
        self.assertEqual(plan.feature, "remove_content")
        self.assertEqual(plan.country_tag, "GER")
        self.assertEqual(plan.remove_spec, {"target": "focuses", "mode": "clear_effects"})
        self.assertEqual([t.id for t in plan.tasks],
                         ["remove_content", "localisation", "validate", "apply"])

    def test_gen_clear_effects_empties_rewards(self):
        from hoi4_agent import filesystem

        tmp = Path(tempfile.mkdtemp(dir=ROOT / "workspace"))
        old_root = filesystem.CONFIG.workspace_root
        try:
            filesystem.CONFIG.workspace_root = tmp
            focus_dir = tmp / "common" / "national_focus"
            focus_dir.mkdir(parents=True)
            (focus_dir / "ger_agent_focus.txt").write_text(
                "focus_tree = {\n\tid = ger_tree\n"
                "\tfocus = {\n\t\tid = GER_focus_a\n"
                "\t\tcompletion_reward = {\n\t\t\tadd_political_power = 50\n\t\t}\n\t}\n"
                "\tfocus = {\n\t\tid = GER_focus_b\n"
                "\t\tcompletion_reward = {\n\t\t\tarmy_experience = 5\n"
                "\t\t\tadd_ideas = { GER_some_idea }\n\t\t}\n\t}\n}\n",
                encoding="utf-8")
            proj = self.ex.create_project("remove the effects from germany's focus tree")
            out = self._gen(proj, "remove_content")
            tree = out["common/national_focus/ger_agent_focus.txt"]
            self.assertNotIn("add_political_power", tree)
            self.assertNotIn("army_experience", tree)
            self.assertNotIn("add_ideas", tree)
            self.assertIn("completion_reward = {\n\t\t}", tree)
            self.assertIn("id = GER_focus_a", tree)
            self.assertIn("id = GER_focus_b", tree)
        finally:
            filesystem.CONFIG.workspace_root = old_root
            shutil.rmtree(tmp, ignore_errors=True)

    def test_remove_spec_parsing(self):
        from hoi4_agent.planner import _parse_remove_spec

        self.assertEqual(_parse_remove_spec("remove the effects from the decisions"),
                         {"target": "decisions", "mode": "clear_effects"})
        self.assertEqual(_parse_remove_spec("remove events"),
                         {"target": "events", "mode": "remove_all"})
        self.assertEqual(_parse_remove_spec("remove decisions for germany"),
                         {"target": "decisions", "mode": "remove_all"})
        self.assertEqual(_parse_remove_spec("clear the national spirits"),
                         {"target": "ideas", "mode": "remove_all"})
        self.assertEqual(_parse_remove_spec("remove the effects from the national spirits"),
                         {"target": "ideas", "mode": "clear_effects"})
        self.assertIsNone(_parse_remove_spec("add a focus tree"))

    def test_remove_without_country_plans_removal(self):
        plan = self.ex.planner.plan_project("remove decisions")
        self.assertEqual(plan.feature, "remove_content")
        self.assertEqual(plan.country_tag, "")
        self.assertEqual(plan.remove_spec, {"target": "decisions", "mode": "remove_all"})

    def test_remove_events_entirely_deletes_file_and_loc(self):
        from hoi4_agent import filesystem

        tmp = Path(tempfile.mkdtemp(dir=ROOT / "workspace"))
        old_root = filesystem.CONFIG.workspace_root
        try:
            filesystem.CONFIG.workspace_root = tmp
            ev_dir = tmp / "events"
            ev_dir.mkdir(parents=True)
            (ev_dir / "ger_agent_events.txt").write_text(
                "country_event = {\n\tid = GER_test_event\n"
                "\toption = {\n\t\tname = GER_test_event.a\n\t\tadd_political_power = 5\n\t}\n}\n",
                encoding="utf-8")
            loc_dir = tmp / "localisation" / "english"
            loc_dir.mkdir(parents=True)
            (loc_dir / "ger_l_english.yml").write_text(
                "l_english:\n GER_test_event.t:0 \"T\"\n GER_test_event.d:0 \"D\"\n"
                " GER_test_event.a:0 \"A\"\n GER_keep:0 \"Keep\"\n", encoding="utf-8")
            proj = self.ex.create_project("remove events from germany")
            self.assertEqual(proj.plan.feature, "remove_content")
            self.assertEqual(proj.plan.remove_spec,
                             {"target": "events", "mode": "remove_all"})
            out = self._gen(proj, "remove_content")
            self.assertEqual(out["events/ger_agent_events.txt"], "")
            loc = out["localisation/english/ger_l_english.yml"]
            self.assertNotIn("GER_test_event.t", loc)
            self.assertIn("GER_keep:0", loc)
            # Applying the deletion proposal removes the file.
            self.ex.agent._prepare_pending(out, label="remove events")
            self.ex.agent.approve_pending(approve_all=True)
            self.assertFalse((ev_dir / "ger_agent_events.txt").exists())
        finally:
            filesystem.CONFIG.workspace_root = old_root
            shutil.rmtree(tmp, ignore_errors=True)

    def test_clear_decision_effects(self):
        from hoi4_agent import filesystem

        tmp = Path(tempfile.mkdtemp(dir=ROOT / "workspace"))
        old_root = filesystem.CONFIG.workspace_root
        try:
            filesystem.CONFIG.workspace_root = tmp
            dec_dir = tmp / "common" / "decisions"
            dec_dir.mkdir(parents=True)
            (dec_dir / "GER_agent.txt").write_text(
                "ger_agent_decisions = {\n"
                "\tGER_test_decision = {\n"
                "\t\tavailable = { always = yes }\n"
                "\t\tcomplete_effect = {\n\t\t\tadd_political_power = 50\n\t\t}\n\t}\n}\n",
                encoding="utf-8")
            proj = self.ex.create_project("remove the effects from germany's decisions")
            self.assertEqual(proj.plan.feature, "remove_content")
            out = self._gen(proj, "remove_content")
            tree = out["common/decisions/GER_agent.txt"]
            self.assertNotIn("add_political_power", tree)
            self.assertIn("complete_effect = {\n\t\t}", tree)
            self.assertIn("GER_test_decision", tree)
        finally:
            filesystem.CONFIG.workspace_root = old_root
            shutil.rmtree(tmp, ignore_errors=True)

    def test_events_direct_option_blocks(self):
        proj = self._project("add a focus tree with events for Canada")
        self.assertIsNotNone(proj.plan.task("events"), "events task should exist when requested")
        self._gen(proj, "focuses")  # last focus references an event -> populates event ids
        out = self._gen(proj, "events")
        content = next(iter(out.values()))
        self.assertIn("\toption = {", content)
        self.assertNotIn("options = {", content)
        self.assertIn("country_event = {", content)
        self.assertNotIn("add_political_power", content)

    def test_standalone_events_request_generates_blank_event(self):
        proj = self._project("add events for chile")
        self.assertEqual(proj.plan.feature, "events")
        self.assertEqual([t.id for t in proj.plan.tasks],
                         ["events", "localisation", "validate", "apply"])
        out = self._gen(proj, "events")
        content = next(iter(out.values()))
        self.assertIn("country_event = {", content)
        self.assertIn("\toption = {", content)
        self.assertIn("name = CHL_events_chl_agent_event_01.a", content)
        self.assertNotIn("add_political_power", content)
        self.assertNotIn("add_ideas", content)

    def test_focus_branch_default_has_no_events(self):
        proj = self._project("add a communist path for Canada")
        self.assertIsNone(proj.plan.task("events"), "events must not be created by default")
        task_ids = [t.id for t in proj.plan.tasks]
        self.assertNotIn("ideas", task_ids)
        self.assertNotIn("decisions", task_ids)
        self.assertNotIn("ai_strategy", task_ids)
        out = self._gen(proj, "focuses")
        content = next(iter(out.values()))
        self.assertNotIn("country_event = {", content)
        self.assertEqual(self._gen(proj, "events"), {}, "no events file without event ids")

    def test_plain_focus_tree_has_blank_rewards_and_no_ideas(self):
        proj = self._project("make a focus tree for China")
        self.assertEqual(proj.plan.feature, "focus_branch")
        self.assertEqual([t.id for t in proj.plan.tasks],
                         ["focuses", "localisation", "validate", "apply"])
        out = self._gen(proj, "focuses")
        content = next(iter(out.values()))
        self.assertIn("completion_reward = {\n\t\t}", content)
        self.assertNotIn("add_political_power", content)
        self.assertNotIn("add_stability", content)
        self.assertNotIn("add_war_support", content)
        self.assertNotIn("add_ideas", content)
        self.assertEqual(self._gen(proj, "events"), {}, "no events for a plain tree")

    def test_focus_tree_with_events_includes_event_reference(self):
        proj = self._project("add a focus tree with events for Canada")
        self.assertIsNotNone(proj.plan.task("events"))
        out = self._gen(proj, "focuses")
        content = next(iter(out.values()))
        self.assertIn("country_event = { id = ", content)
        self.assertIn("days = 5", content)

    def test_focus_event_planning(self):
        plan = self.ex.planner.plan_project(
            "add an event to the first focus in the uruguay focus tree")
        self.assertEqual(plan.feature, "focus_event")
        self.assertEqual(plan.country_tag, "URG")
        self.assertEqual(plan.focus_position, "first")

    def test_focus_event_ordinal_position(self):
        from hoi4_agent.planner import _extract_focus_position

        self.assertEqual(
            _extract_focus_position("add an event to the second focus in germany's focus tree"),
            "2")
        self.assertEqual(
            _extract_focus_position("add an event to the third focus in germany's focus tree"),
            "3")
        self.assertEqual(
            _extract_focus_position("add an event to the first focus in germany's focus tree"),
            "first")
        plan = self.ex.planner.plan_project(
            "add an event to the second focus in germany's focus tree")
        self.assertEqual(plan.feature, "focus_event")
        self.assertEqual(plan.focus_position, "2")

    def test_focus_event_targets_second_focus(self):
        from hoi4_agent import filesystem

        tmp = Path(tempfile.mkdtemp(dir=ROOT / "workspace"))
        old_root = filesystem.CONFIG.workspace_root
        try:
            filesystem.CONFIG.workspace_root = tmp
            focus_dir = tmp / "common" / "national_focus"
            focus_dir.mkdir(parents=True)
            (focus_dir / "ger_agent_focus.txt").write_text(
                "focus_tree = {\n\tid = ger_tree\n"
                "\tfocus = {\n\t\tid = GER_test_focus_01\n"
                "\t\tcompletion_reward = {\n\t\t}\n\t}\n"
                "\tfocus = {\n\t\tid = GER_test_focus_02\n"
                "\t\tcompletion_reward = {\n\t\t}\n\t}\n"
                "\tfocus = {\n\t\tid = GER_test_focus_03\n"
                "\t\tcompletion_reward = {\n\t\t}\n\t}\n}\n",
                encoding="utf-8")
            proj = self.ex.create_project(
                "add an event to the second focus in germany's focus tree")
            out = self._gen(proj, "focus_event")
            tree = out["common/national_focus/ger_agent_focus.txt"]
            blocks = self.ex._focus_blocks(tree)
            self.assertIn("country_event = { id = ", tree[blocks[1][1]:blocks[1][2]])
            self.assertFalse(any("country_event" in tree[b[1]:b[2]]
                                 for b in (blocks[0], blocks[2])))
        finally:
            filesystem.CONFIG.workspace_root = old_root
            shutil.rmtree(tmp, ignore_errors=True)

    def test_focus_event_fallback_creates_tree_with_event_on_first_focus(self):
        proj = self._project("add an event to the first focus in the cuba focus tree")
        self.assertEqual(proj.plan.feature, "focus_event")
        self.assertEqual(proj.plan.focus_position, "first")
        out = self._gen(proj, "focus_event")
        tree = out["common/national_focus/cub_agent_focus.txt"]
        blocks = self.ex._focus_blocks(tree)
        self.assertIn("country_event = { id = ", tree[blocks[0][1]:blocks[0][2]])
        self.assertFalse(any("country_event" in tree[b[1]:b[2]] for b in blocks[1:]))
        self.assertIn("events/cub_agent_events.txt", out)

    def test_focus_event_modifies_existing_tree(self):
        from hoi4_agent import filesystem

        tmp = Path(tempfile.mkdtemp(dir=ROOT / "workspace"))
        old_root = filesystem.CONFIG.workspace_root
        try:
            filesystem.CONFIG.workspace_root = tmp
            focus_dir = tmp / "common" / "national_focus"
            focus_dir.mkdir(parents=True)
            (focus_dir / "cub_agent_focus.txt").write_text(
                "focus_tree = {\n\tid = cub_tree\n"
                "\tfocus = {\n\t\tid = CUB_test_focus_01\n"
                "\t\tcompletion_reward = {\n\t\t\tadd_political_power = 5\n\t\t}\n\t}\n"
                "\tfocus = {\n\t\tid = CUB_test_focus_02\n"
                "\t\tcompletion_reward = {\n\t\t\tadd_stability = 0.1\n\t\t}\n\t}\n}\n",
                encoding="utf-8")
            proj = self.ex.create_project("add an event to the first focus in the cuba focus tree")
            out = self._gen(proj, "focus_event")
            self.assertIn("common/national_focus/cub_agent_focus.txt", out)
            tree = out["common/national_focus/cub_agent_focus.txt"]
            blocks = self.ex._focus_blocks(tree)
            self.assertIn("country_event = { id = ", tree[blocks[0][1]:blocks[0][2]])
            self.assertFalse("country_event" in tree[blocks[1][1]:blocks[1][2]])
            self.assertIn("events/cub_agent_events.txt", out)
        finally:
            filesystem.CONFIG.workspace_root = old_root
            shutil.rmtree(tmp, ignore_errors=True)

    def test_focus_effects_planning(self):
        plan = self.ex.planner.plan_project("add effects to the chinese focus tree")
        self.assertEqual(plan.feature, "focus_effects")
        self.assertEqual(plan.country_tag, "CHI")
        self.assertEqual(plan.effect_spec, [], "vague request needs a follow-up question")

    def test_extract_countries(self):
        plan = Planner()
        self.assertEqual(
            plan._extract_countries("add focus trees for congo, philippines, and france"),
            ["COG", "PHI", "FRA"])
        self.assertEqual(plan._extract_countries("add a focus tree for congo"), ["COG"])
        self.assertEqual(
            plan._extract_countries("add communist paths for germany and canada"),
            ["GER", "CAN"])
        self.assertEqual(
            plan._extract_countries("add focus trees for republic of congo and france"),
            ["RCG", "FRA"])
        # Short workspace names must not match inside other words ("anc" in "france").
        self.assertNotIn("ANC", plan._extract_countries("add a focus tree for france"))

    def test_run_multi_country(self):
        from hoi4_agent import filesystem
        from hoi4_agent.config import CONFIG

        tmp = Path(tempfile.mkdtemp(dir=ROOT / "workspace"))
        old_root = filesystem.CONFIG.workspace_root
        old_mem = CONFIG.memory_dir
        try:
            filesystem.CONFIG.workspace_root = tmp
            CONFIG.memory_dir = tmp / "state"
            proj_result = self.ex.run_multi(
                "add focus trees for congo, philippines, and france", auto_approve=False)
            self.assertEqual(proj_result["status"], "pending")
            self.assertEqual(proj_result["multi_country"], ["COG", "PHI", "FRA"])
            pending = set(proj_result.get("pending_files", []))
            for tag in ("COG", "PHI", "FRA"):
                self.assertIn(f"common/national_focus/{tag.lower()}_agent_focus.txt", pending)
        finally:
            filesystem.CONFIG.workspace_root = old_root
            CONFIG.memory_dir = old_mem
            shutil.rmtree(tmp, ignore_errors=True)

    def test_parse_effect_spec(self):
        from hoi4_agent.planner import _parse_effect_spec

        self.assertEqual(
            _parse_effect_spec("50 political power to each focus"),
            [{"position": "all", "effect": "add_political_power", "amount": 50.0}],
        )
        self.assertEqual(
            _parse_effect_spec("political power to focus 1, stability to focus 2"),
            [{"position": "1", "effect": "add_political_power", "amount": None},
             {"position": "2", "effect": "add_stability", "amount": None}],
        )
        spec = _parse_effect_spec("5% stability to the first focus")
        self.assertEqual(spec[0]["position"], "first")
        self.assertEqual(spec[0]["effect"], "add_stability")
        self.assertAlmostEqual(spec[0]["amount"], 0.05)
        plus = _parse_effect_spec("stability +10 to focus 1, 100 political power to focus 2")
        self.assertEqual(plus[0]["position"], "1")
        self.assertEqual(plus[0]["effect"], "add_stability")
        self.assertAlmostEqual(plus[0]["amount"], 0.1)
        self.assertEqual(plus[1]["amount"], 100.0)
        self.assertEqual(_parse_effect_spec("add some things"), [])

    def test_continue_feature_field_inference(self):
        """A follow-up answer without a field must still route to the effects
        spec (not politics) for focus_effects projects."""
        from hoi4_agent.server import Hoi4Server

        proj = self._project("add effects to the chinese focus tree")
        self.assertEqual(Hoi4Server._infer_field(proj), "effects")
        proj2 = self._project("create a new country called Zorgland")
        self.assertEqual(Hoi4Server._infer_field(proj2), "politics")

    def test_gen_focus_effects_applies_to_all_cleanly(self):
        from hoi4_agent import filesystem

        tmp = Path(tempfile.mkdtemp(dir=ROOT / "workspace"))
        old_root = filesystem.CONFIG.workspace_root
        try:
            filesystem.CONFIG.workspace_root = tmp
            focus_dir = tmp / "common" / "national_focus"
            focus_dir.mkdir(parents=True)
            (focus_dir / "cub_agent_focus.txt").write_text(
                "focus_tree = {\n\tid = cub_tree\n"
                "\tfocus = {\n\t\tid = CUB_test_focus_01\n"
                "\t\tcompletion_reward = {\n\t\t}\n\t}\n"
                "\tfocus = {\n\t\tid = CUB_test_focus_02\n"
                "\t\tcompletion_reward = {\n\t\t\tcountry_event = { id = CUB_test_event days = 5 }\n\t\t}\n\t}\n}\n",
                encoding="utf-8")
            proj = self.ex.create_project("add effects to the cuba focus tree")
            proj.plan.effect_spec = [{"position": "all", "effect": "add_political_power", "amount": 50.0}]
            out = self._gen(proj, "focus_effects")
            tree = out["common/national_focus/cub_agent_focus.txt"]
            blocks = self.ex._focus_blocks(tree)
            self.assertEqual(len(blocks), 2)
            for block in blocks:
                seg = tree[block[1]:block[2]]
                self.assertIn("add_political_power = 50", seg)
                self.assertEqual(seg.count("completion_reward"), 1)
        finally:
            filesystem.CONFIG.workspace_root = old_root
            shutil.rmtree(tmp, ignore_errors=True)

    def test_gen_focus_effects_targets_single_focus(self):
        proj = self.ex.create_project("add 50 political power to focus 1 of the cuba focus tree")
        self.assertEqual(proj.plan.feature, "focus_effects")
        self.assertEqual(proj.plan.effect_spec,
                         [{"position": "1", "effect": "add_political_power", "amount": 50.0}])
        out = self._gen(proj, "focus_effects")
        tree = out["common/national_focus/cub_agent_focus.txt"]
        blocks = self.ex._focus_blocks(tree)
        self.assertIn("add_political_power = 50", tree[blocks[0][1]:blocks[0][2]])
        self.assertFalse(any("add_political_power" in tree[b[1]:b[2]] for b in blocks[1:]))

    def test_change_specific_focus_effect(self):
        from hoi4_agent import filesystem

        req = "change the focus ARG_army_recruitment_bolster to add 10000 manpower as an effect"
        plan = self.ex.planner.plan_project(req)
        self.assertEqual(plan.feature, "focus_effects")
        self.assertEqual(plan.country_tag, "ARG")
        self.assertEqual(plan.effect_spec,
                         [{"position": "ARG_army_recruitment_bolster",
                           "effect": "add_manpower", "amount": 10000.0}])

        tmp = Path(tempfile.mkdtemp(dir=ROOT / "workspace"))
        old_root = filesystem.CONFIG.workspace_root
        try:
            filesystem.CONFIG.workspace_root = tmp
            focus_dir = tmp / "common" / "national_focus"
            focus_dir.mkdir(parents=True)
            (focus_dir / "arg_agent_focus.txt").write_text(
                "focus_tree = {\n\tid = arg_tree\n"
                "\tfocus = {\n\t\tid = ARG_army_recruitment_bolster\n"
                "\t\tcompletion_reward = {\n\t\t}\n\t}\n"
                "\tfocus = {\n\t\tid = ARG_other_focus\n"
                "\t\tcompletion_reward = {\n\t\t}\n\t}\n}\n",
                encoding="utf-8")
            proj = self.ex.create_project(req)
            out = self._gen(proj, "focus_effects")
            tree = out["common/national_focus/arg_agent_focus.txt"]
            blocks = self.ex._focus_blocks(tree)
            by_id = {b[0]: b for b in blocks}
            self.assertIn("add_manpower = 10000",
                          tree[by_id["ARG_army_recruitment_bolster"][1]:
                               by_id["ARG_army_recruitment_bolster"][2]])
            self.assertNotIn("add_manpower",
                             tree[by_id["ARG_other_focus"][1]: by_id["ARG_other_focus"][2]])
        finally:
            filesystem.CONFIG.workspace_root = old_root
            shutil.rmtree(tmp, ignore_errors=True)

    def test_country_from_focus_id(self):
        self.assertEqual(
            self.ex.planner._country_from_focus_id(
                "change the focus ARG_army_recruitment_bolster to add manpower"),
            "ARG")
        self.assertIsNone(self.ex.planner._country_from_focus_id("no id here"))

    def test_gen_localisation_merges_existing_keys(self):
        from hoi4_agent import filesystem

        tmp = Path(tempfile.mkdtemp(dir=ROOT / "workspace"))
        old_root = filesystem.CONFIG.workspace_root
        try:
            filesystem.CONFIG.workspace_root = tmp
            loc_dir = tmp / "localisation" / "english"
            loc_dir.mkdir(parents=True)
            (loc_dir / "arg_l_english.yml").write_text(
                "l_english:\n ARG_army_recruitment_bolster:0 \"Army Recruitment Bolster\"\n"
                " ARG_existing:0 \"Existing\"\n",
                encoding="utf-8")
            proj = self._project("add a focus tree for argentina")
            proj.memory.add_loc("ARG_new_key")
            out = self._gen(proj, "localisation")
            content = out["localisation/english/arg_l_english.yml"]
            self.assertIn("ARG_army_recruitment_bolster:0 \"Army Recruitment Bolster\"", content)
            self.assertIn("ARG_existing:0 \"Existing\"", content)
            self.assertIn("ARG_new_key:0", content)
        finally:
            filesystem.CONFIG.workspace_root = old_root
            shutil.rmtree(tmp, ignore_errors=True)

    def test_focus_effects_name_resolution(self):
        from hoi4_agent import filesystem

        tmp = Path(tempfile.mkdtemp(dir=ROOT / "workspace"))
        old_root = filesystem.CONFIG.workspace_root
        try:
            filesystem.CONFIG.workspace_root = tmp
            focus_dir = tmp / "common" / "national_focus"
            focus_dir.mkdir(parents=True)
            (focus_dir / "arg_agent_focus.txt").write_text(
                "focus_tree = {\n\tid = arg_tree\n"
                "\tfocus = {\n\t\tid = ARG_army_recruitment_bolster\n"
                "\t\tcompletion_reward = {\n\t\t}\n\t}\n"
                "\tfocus = {\n\t\tid = ARG_other_focus\n"
                "\t\tcompletion_reward = {\n\t\t}\n\t}\n}\n",
                encoding="utf-8")
            proj = self.ex.create_project(
                "change the focus Army Recruitment Bolster for Argentina to add "
                "10000 manpower as an effect")
            out = self._gen(proj, "focus_effects")
            tree = out["common/national_focus/arg_agent_focus.txt"]
            blocks = self.ex._focus_blocks(tree)
            by_id = {b[0]: b for b in blocks}
            self.assertIn("add_manpower = 10000",
                          tree[by_id["ARG_army_recruitment_bolster"][1]:
                               by_id["ARG_army_recruitment_bolster"][2]])
            self.assertNotIn("add_manpower",
                             tree[by_id["ARG_other_focus"][1]: by_id["ARG_other_focus"][2]])
        finally:
            filesystem.CONFIG.workspace_root = old_root
            shutil.rmtree(tmp, ignore_errors=True)

    def test_focus_effects_named_unresolvable_raises(self):
        from hoi4_agent import filesystem

        tmp = Path(tempfile.mkdtemp(dir=ROOT / "workspace"))
        old_root = filesystem.CONFIG.workspace_root
        try:
            filesystem.CONFIG.workspace_root = tmp
            focus_dir = tmp / "common" / "national_focus"
            focus_dir.mkdir(parents=True)
            (focus_dir / "arg_agent_focus.txt").write_text(
                "focus_tree = {\n\tid = arg_tree\n"
                "\tfocus = {\n\t\tid = ARG_some_focus\n"
                "\t\tcompletion_reward = {\n\t\t}\n\t}\n}\n",
                encoding="utf-8")
            proj = self.ex.create_project(
                "change the focus Nonexistent Widget for Argentina to add "
                "100 manpower as an effect")
            with self.assertRaises(ValueError):
                self._gen(proj, "focus_effects")
        finally:
            filesystem.CONFIG.workspace_root = old_root
            shutil.rmtree(tmp, ignore_errors=True)

    def test_gen_focuses_event_position(self):
        proj = self._project("add a focus tree with events for Canada")
        proj.plan.focus_position = "first"
        out = self._gen(proj, "focuses")
        tree = next(iter(out.values()))
        blocks = self.ex._focus_blocks(tree)
        self.assertIn("country_event = { id = ", tree[blocks[0][1]:blocks[0][2]])
        self.assertFalse(any("country_event" in tree[b[1]:b[2]] for b in blocks[1:]))

    def test_focuses_never_overwrite_vanilla_tree(self):
        proj = self._project("add a communist path for Canada")
        out = self._gen(proj, "focuses")
        (path, content), = out.items()
        self.assertIn("can_agent_focus.txt", path)
        self.assertNotIn("common/national_focus/canada.txt", path)
        self.assertTrue(content.startswith("focus_tree = {"))
        self.assertIn("icon = GFX_", content)
        self.assertIn("completion_reward = {", content)

    def test_effects_are_valid(self):
        verified = self.ex._verified_effects()
        self.assertNotIn("add_army_experience", verified)
        self.assertNotIn("add_navy_experience", verified)
        self.assertNotIn("add_air_experience", verified)
        for e in verified:
            self.assertIn(e, self.agent.validator.effects, msg=f"{e} not documented")

    def test_references_no_history_clobber(self):
        proj = self._project("add a communist path for Canada")
        out = self._gen(proj, "references")
        self.assertEqual(out, {})

    def test_selected_states_planning(self):
        proj = self._project("create a new country called Bajookistan using states 5, 10, 25")
        self.assertEqual(proj.plan.selected_states, [5, 10, 25])
        self.assertEqual(proj.plan.new_country_name, "Bajookistan")
        self.assertEqual(proj.plan.country_tag, "BAJ")

    def test_country_name_with_digits_and_tag_collision(self):
        proj = self._project("create a new country called Bluenada2 using states 5, 10")
        self.assertEqual(proj.plan.new_country_name, "Bluenada2")
        tag = proj.plan.country_tag
        self.assertEqual(len(tag), 3)
        # Must not collide with any vanilla or workspace-mod tag.
        planner = Planner()
        from hoi4_agent import planner as planner_mod

        existing = set(planner_mod.COUNTRY_TAGS.values()) | set(planner._load_vanilla_country_names())
        existing |= set(planner._workspace_countries()[1])
        self.assertNotIn(tag, existing, msg=f"tag {tag} already used by another country")

    def test_plain_new_country_name(self):
        proj = self._project("make Westeros a country")
        self.assertEqual(proj.plan.new_country_name, "Westeros")
        proj2 = self._project("create a new country called Bajookistan")
        self.assertEqual(proj2.plan.new_country_name, "Bajookistan")
        self.assertEqual(proj2.plan.country_tag, "BAJ")

    def test_country_files_with_selected_states(self):
        proj = self._project("create a new country called Bajookistan using states 5, 10, 25")
        proj.plan.politics = "democratic"
        out = self._gen(proj, "country_files")
        paths = set(out)
        self.assertIn("common/countries/Bajookistan.txt", paths)
        self.assertIn("history/countries/BAJ - Bajookistan.txt", paths)
        state_files = [p for p in paths if p.startswith("history/states/")]
        self.assertEqual(len(state_files), 3)
        for p in state_files:
            content = out[p]
            self.assertIn("owner = BAJ", content)
            self.assertIn("add_core_of = BAJ", content)
        history = out["history/countries/BAJ - Bajookistan.txt"]
        self.assertIn("capital = 5", history)
        self.assertIn("set_politics = {", history)
        # OOB must use the selected states' provinces, not free land.
        self.assertTrue(proj.memory.owned_provinces)
        oob = self._gen(proj, "oob")
        oob_text = next(iter(oob.values()))
        for p in proj.memory.owned_provinces[:2]:
            self.assertIn(str(p), oob_text)

    def test_state_overrides_validate(self):
        """Saudi states (854/855/857) use state-file keys like
        buildings_max_level_factor that must not be flagged as modifiers."""
        proj = self._project("create a new country called North Saud using states 854, 855, 857")
        proj.plan.politics = "communist"
        out = self._gen(proj, "country_files")
        state_files = [p for p in out if p.startswith("history/states/")]
        self.assertEqual(len(state_files), 3)
        res = self.agent.validator.validate_proposal(out)
        self.assertTrue(res["valid"], msg=[e["message"] for e in res.get("errors", [])])
        for p in state_files:
            self.assertIn("owner = NO1", out[p])
            self.assertIn("add_core_of = NO1", out[p])

    def test_country_without_states_has_no_synthetic_state(self):
        """Creating a country without selecting states must NOT fabricate a
        900xxx state file; it should be built territory-less."""
        proj = self._project("create a new country called Zorgland")
        self.assertEqual(proj.plan.feature, "new_country")
        self.assertEqual(proj.plan.selected_states, [])
        proj.plan.politics = "fascist"
        out = self._gen(proj, "country_files")
        state_files = [p for p in out if p.startswith("history/states/")]
        self.assertEqual(state_files, [], "no synthetic state should be created")
        history = out["history/countries/ZOR - Zorgland.txt"]
        self.assertNotIn("set_oob", history)
        self.assertIn("set_politics = {", history)
        self.assertEqual(self._gen(proj, "oob"), {}, "no OOB without territory")

    def test_transfer_states_planning(self):
        plan = self.ex.planner.plan_project("transfer states 854, 855, 857 to SAU")
        self.assertEqual(plan.feature, "transfer_states")
        self.assertEqual(plan.country_tag, "SAU")
        self.assertEqual(plan.selected_states, [854, 855, 857])

    def test_gen_state_transfer(self):
        proj = self.ex.create_project("transfer states 854, 855, 857 to SAU")
        out = self._gen(proj, "state_transfer")
        self.assertEqual(sorted(out), [
            "history/states/854-SAU.txt",
            "history/states/855-SAU.txt",
            "history/states/857-SAU.txt",
        ])
        for content in out.values():
            self.assertIn("owner = SAU", content)
            self.assertIn("add_core_of = SAU", content)

    def test_built_countries_registry(self):
        remember_built_country("Zorgland", "ZOR", [854, 855])
        try:
            built = load_built_countries()
            self.assertTrue(any(b["tag"] == "ZOR" and b["name"] == "Zorgland" for b in built))
            self.assertEqual(next(b for b in built if b["tag"] == "ZOR")["states"], [854, 855])
        finally:
            remember_built_country("Zorgland", "ZOR", [])  # keep registry deterministic
            entries = [b for b in load_built_countries() if b["tag"] != "ZOR"]
            from hoi4_agent.project import BUILT_COUNTRIES_FILE

            BUILT_COUNTRIES_FILE.write_text(
                __import__("json").dumps(entries, ensure_ascii=False, indent=2),
                encoding="utf-8")

    def test_round_trip_plan_persists_selected_states(self):
        proj = self._project("create a new country called Bajookistan using states 5, 10")
        data = proj.to_dict()
        from hoi4_agent.project import Project

        restored = Project.from_dict(data)
        self.assertEqual(restored.plan.selected_states, [5, 10])

    def test_agent_run_routes_projects_through_executor(self):
        """agent.run must use the ProjectExecutor (not the legacy V1 planner)
        for focus-tree requests: staged into pending, agent-owned file, and
        idempotent on re-run."""
        from hoi4_agent import filesystem
        from hoi4_agent.config import CONFIG

        tmp = Path(tempfile.mkdtemp(dir=ROOT / "workspace"))
        old_root, old_mem = filesystem.CONFIG.workspace_root, CONFIG.memory_dir
        filesystem.CONFIG.workspace_root = tmp
        CONFIG.memory_dir = tmp / "state"
        try:
            # A fresh, fully isolated agent: never reuse the class-level agent
            # (its backlog is bound to the real memory dir).
            agent = Agent(auto_approve=False, use_model=False)
            agent.promptless = True
            agent._ask_approval = lambda diff: False
            before = len(agent.pending_batches)
            r1 = agent.run("make a focus tree for canada with 10 focuses")
            self.assertTrue(len(agent.pending_batches) > before)
            files = sorted(agent.pending_batches[-1]["proposals"])
            self.assertIn("common/national_focus/can_agent_focus.txt", files)
            self.assertNotIn("common/national_focus/canada.txt", files,
                             "must never write to the real country focus file")
            self.assertEqual(r1.get("intent"), "focus_branch")
            agent.approve_pending(approve_all=True)
            n = len(agent.pending_batches)
            r2 = agent.run("make a focus tree for canada with 10 focuses")
            self.assertEqual(len(agent.pending_batches), n,
                             "re-running the same prompt must not duplicate")
            self.assertEqual(r2.get("pending_files"), [])
        finally:
            filesystem.CONFIG.workspace_root = old_root
            CONFIG.memory_dir = old_mem
            shutil.rmtree(tmp, ignore_errors=True)

    def test_remove_content_with_missing_file_is_noop(self):
        """Removing content that doesn't exist in the workspace must return no
        proposals (and must never touch vanilla files), not raise."""
        proj = self._project("remove all events from the sweden tree")
        self.assertEqual(proj.plan.feature, "remove_content")
        out = self._gen(proj, "remove_content")
        self.assertEqual(out, {})


if __name__ == "__main__":
    unittest.main()
