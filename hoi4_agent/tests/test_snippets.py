"""Regression tests: grounded snippet engine + agent routing.

Covers the autonomous-improvement fix that took the country-less benchmark
cases from 0/25 to 25/25:

* snippet requests produce validated, grounded proposals (no invented
  identifiers, no undocumented effects/triggers/modifiers);
* country-targeted project requests are never hijacked by the snippet engine;
* new-country detection no longer misfires on "country history file".

Run:  python -m unittest hoi4_agent.tests.test_snippets
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from hoi4_agent import filesystem  # noqa: E402
from hoi4_agent.agent import Agent  # noqa: E402
from hoi4_agent.config import CONFIG  # noqa: E402
from hoi4_agent.snippets import SnippetEngine  # noqa: E402

import re  # noqa: E402


class SnippetEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(dir=ROOT / "workspace"))
        self._old_root = filesystem.CONFIG.workspace_root
        self._old_mem = CONFIG.memory_dir
        filesystem.CONFIG.workspace_root = self.tmp
        CONFIG.memory_dir = self.tmp / "state"
        self.agent = Agent(auto_approve=False, use_model=False)
        self.agent.promptless = True
        self.agent._ask_approval = lambda diff: False
        self.engine = SnippetEngine(self.agent)

    def tearDown(self):
        filesystem.CONFIG.workspace_root = self._old_root
        CONFIG.memory_dir = self._old_mem
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _assert_valid_snippet(self, prompt):
        gen = self.engine.generate(prompt)
        self.assertIsNotNone(gen, prompt)
        res = self.agent.validator.validate_proposal(gen)
        self.assertTrue(res.get("valid"), f"{prompt}: {res['errors'][:3]}")
        text = "\n".join(gen.values())
        declared = set(re.findall(r"\bid\s*=\s*([A-Za-z0-9_.]+)", text))
        declared |= set(re.findall(r"^[ \t]{0,2}([A-Z]{2,4}_[A-Za-z0-9_]+)\s*=\s*\{",
                                   text, re.M))
        known = self.agent.index.known_set()
        invented = [tok for tok in re.findall(r"\b([A-Z]{2,4}_[A-Za-z0-9_.]+)\b", text)
                    if not tok.startswith("GFX_")
                    and tok not in declared and tok not in known]
        self.assertFalse(invented, f"invented identifiers in {prompt}: {invented}")
        return gen

    def test_focus_snippet(self):
        gen = self._assert_valid_snippet(
            "Add a focus called 'Rhineland Remilitarization' that costs 10 "
            "political power and gives a temporary army bonus.")
        text = "\n".join(gen.values())
        self.assertIn("completion_reward", text)
        self.assertIn("add_tech_bonus", text)
        # "Rhineland" resolves to a real tag (RHI) -> vanilla-conventional id
        self.assertIn("RHI_rhineland_remilitarization", text)
        # a bare focus block outside focus_tree is invalid; the snippet must
        # be wrapped in a focus_tree block for a fresh file.
        self.assertIn("focus_tree = {", text)
        self.assertIn("RHI_rhineland_remilitarization_tree", text)
        self.assertLess(text.index("focus_tree = {"), text.index("focus = {"))

    def test_event_chain_snippet(self):
        gen = self._assert_valid_snippet(
            "Write a 2-part event chain where accepting the first event "
            "triggers the second after a delay.")
        text = "\n".join(gen.values())
        self.assertIn("country_event = {", text)
        self.assertIn("days = 7", text)

    def test_scripted_trigger_snippet(self):
        gen = self._assert_valid_snippet(
            "Create a scripted_trigger checking whether a country controls at "
            "least 80% of a listed set of states.")
        text = "\n".join(gen.values())
        self.assertIn("controls_state", text)
        self.assertIn("num_of_controlled_states", text)

    def test_decision_guarantee_snippet(self):
        gen = self._assert_valid_snippet(
            "Create a targeted decision to guarantee another country's "
            "independence, using target scope.")
        text = "\n".join(gen.values())
        self.assertIn("give_guarantee", text)
        self.assertIn("target_trigger", text)

    def test_country_targeted_requests_never_match(self):
        for prompt in (
            "add a focus tree for canada",
            "add focus trees for congo, philippines, and france",
            "make a communist focus tree for Germany with 15 focuses",
            "add decisions for chile",
            "add an event to the second focus in germany's focus tree",
            "remove the effects from germany's focus tree",
            "add effects to the chinese focus tree",
        ):
            self.assertFalse(self.engine.matches(prompt), prompt)

    def test_snippet_requests_match(self):
        for prompt in (
            "Write a decision that costs political power, has a 30-day "
            "cooldown, and requires a specific technology.",
            "Write a news event with 3 options that add different opinion "
            "modifiers depending on choice.",
            "Write a modifier definition usable in country scope that boosts "
            "research speed.",
            "Create a country history file setting starting ideology, ruling "
            "party, and initial law.",
            # single-object requests route to the snippet engine even with a
            # country mention (the project pipeline is for trees/branches/plurals)
            "add a fascist focus for Brazil that grants a national spirit",
            "Add a focus for Italy called 'Mare Nostrum' that grants a "
            "temporary naval attack bonus.",
            # "prereqs" (no 'u') must not be treated as a tree request
            "write a focus for turkey that costs 10 and prereqs two other focuses",
        ):
            self.assertTrue(self.engine.matches(prompt), prompt)

    def test_modify_reports_not_found_vs_already_set(self):
        from hoi4_agent.snippets import SnippetEngine

        engine = SnippetEngine(self.agent)
        self.assertIsNone(engine.modify("make a focus tree for canada"))
        proposals, reason = engine.modify(
            "change the cost of the focus GER_missing_focus to 15")
        self.assertEqual((proposals, reason), ({}, "not_found"))

        target = Path(self.tmp) / "common" / "national_focus" / "ger_test.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "focus_tree = {\n\tid = GER_t\n\tfocus = {\n"
            "\t\tid = GER_existing_focus\n\t\tcost = 10\n\t}\n}\n",
            encoding="utf-8")
        proposals, reason = engine.modify(
            "change the cost of the focus GER_existing_focus in "
            "common/national_focus/ger_test.txt to 15")
        self.assertEqual(reason, "changed")
        self.assertIn("cost = 15", proposals["common/national_focus/ger_test.txt"])
        # apply the edit like approve_pending would, then re-run
        target.write_text(proposals["common/national_focus/ger_test.txt"],
                          encoding="utf-8")
        proposals, reason = engine.modify(
            "change the cost of the focus GER_existing_focus in "
            "common/national_focus/ger_test.txt to 15")
        self.assertEqual((proposals, reason), ({}, "already_set"))

    def test_modify_without_country_asks(self):
        before = len(self.agent.pending_batches)
        result = self.agent.run("delete the effects from my focus tree")
        self.assertEqual(result.get("needs_input"), "country")
        self.assertEqual(len(self.agent.pending_batches), before)

    def test_modify_unknown_country_helpful_message(self):
        result = self.agent.run("change every focus cost to 5")
        self.assertEqual(result.get("intent"), "modify")
        self.assertIn("could not identify", result.get("summary", ""))
        self.assertEqual(result.get("pending_files"), [])

    def test_agent_routes_snippet_to_pending(self):
        before = len(self.agent.pending_batches)
        result = self.agent.run(
            "Write a scripted_effect that adds war support and stability, "
            "scaled by a variable amount.")
        self.assertEqual(result.get("intent"), "snippet")
        self.assertTrue(len(self.agent.pending_batches) > before)
        self.assertTrue(result.get("pending_files"))

    def test_agent_routes_project_to_executor(self):
        before = len(self.agent.pending_batches)
        result = self.agent.run("add a focus tree for canada")
        self.assertNotEqual(result.get("intent"), "snippet")
        self.assertTrue(len(self.agent.pending_batches) > before)

    def test_active_file_focus_append(self):
        target = Path(self.tmp) / "common" / "national_focus" / "germany.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "focus_tree = {\n"
            "\tid = GER_agent_tree\n"
            "\tcountry = {\n"
            "\t\tfactor = 0\n"
            "\t}\n"
            "\tfocus = {\n"
            "\t\tid = GER_existing\n"
            "\t\tcost = 10\n"
            "\t}\n"
            "}\n",
            encoding="utf-8")
        gen = self.engine.generate(
            "Add a focus called 'Rhineland Remilitarization' that costs 10 "
            "political power and gives a temporary army bonus.",
            active_file="common/national_focus/germany.txt")
        self.assertIn("common/national_focus/germany.txt", gen)
        self.assertNotIn("common/national_focus/agent_focus.txt", gen)
        text = gen["common/national_focus/germany.txt"]
        self.assertIn("GER_existing", text)
        self.assertIn("RHI_rhineland_remilitarization", text)
        # the new focus must sit INSIDE the existing focus_tree block (before
        # its closing brace), not dangling after it.
        self.assertLess(text.index("RHI_rhineland_remilitarization"),
                        text.rfind("}"))
        self.assertNotIn("}\n\nfocus = {", text)
        from hoi4_agent._runtime.common import check_delimiters

        ok, _ = check_delimiters(text)
        self.assertTrue(ok, "merged focus file must have balanced braces")
        self.assertEqual(text.count("focus_tree = {"), 1)
        # localisation is still staged (as its own new file)
        self.assertTrue(any(p.endswith(".yml") for p in gen))

    def test_active_file_not_applicable_falls_back(self):
        gen = self.engine.generate(
            "Write a decision that costs political power, has a 30-day "
            "cooldown, and requires a specific technology.",
            active_file="README.md")
        self.assertIn("common/decisions/agent_decisions.txt", gen)
        self.assertNotIn("README.md", gen)

    def test_active_file_wrong_kind_falls_back(self):
        gen = self.engine.generate(
            "Write a decision that costs political power.",
            active_file="events/agent_events.txt")
        self.assertIn("common/decisions/agent_decisions.txt", gen)
        self.assertNotIn("events/agent_events.txt", gen)

    def test_active_localisation_merge(self):
        loc = Path(self.tmp) / "localisation" / "english" / "ger_l_english.yml"
        loc.parent.mkdir(parents=True, exist_ok=True)
        loc.write_text("l_english:\n GER_existing:0 \"Existing\"\n",
                       encoding="utf-8")
        gen = self.engine.generate(
            "Add a focus called 'Rhineland Remilitarization' that costs 10 "
            "political power.",
            active_file="localisation/english/ger_l_english.yml")
        text = gen["localisation/english/ger_l_english.yml"]
        self.assertIn("GER_existing", text)
        self.assertIn("RHI_rhineland_remilitarization", text)
        self.assertNotIn("agent_snippet_l_english.yml", gen)
        # the focus block itself still goes to the default new focus file
        self.assertIn("common/national_focus/agent_focus.txt", gen)

    def test_agent_run_active_file(self):
        before = len(self.agent.pending_batches)
        result = self.agent.run(
            "Write a scripted_effect that adds war support and stability, "
            "scaled by a variable amount.",
            active_file="common/scripted_effects/agent_effects.txt")
        self.assertEqual(result.get("intent"), "snippet")
        self.assertIn("will append to", result.get("summary", ""))
        batch = self.agent.pending_batches[-1]
        self.assertIn("common/scripted_effects/agent_effects.txt",
                      batch["proposals"])

    def test_explicit_path_in_prompt_wins(self):
        target = Path(self.tmp) / "common" / "decisions" / "my_decisions.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("existing_decision = {\n\tavailable = { always = yes }\n}\n",
                          encoding="utf-8")
        gen = self.engine.generate(
            "Write a decision that costs 50 political power to "
            "common/decisions/my_decisions.txt")
        self.assertIn("common/decisions/my_decisions.txt", gen)
        self.assertNotIn("common/decisions/agent_decisions.txt", gen)
        self.assertIn("existing_decision",
                      gen["common/decisions/my_decisions.txt"])

    def test_new_country_history_file_not_a_country(self):
        from hoi4_agent.planner import Planner

        self.assertIsNone(Planner()._detect_new_country(
            "Create a country history file setting starting ideology, ruling "
            "party, and initial law."))
        self.assertIsNotNone(Planner()._detect_new_country(
            "create a new country called Bluenada"))


class FocusTreeCivilWarRoutingTests(unittest.TestCase):
    """A focus-tree request mentioning a civil war branch must produce ONLY a
    focus tree (plus localisation), never ideas/events/decisions/ai_strategy."""

    def test_civil_war_focus_tree_routes_to_focus_branch(self):
        from hoi4_agent.planner import Planner

        plan = Planner().plan_project(
            "Make a communist focus tree for Germany with 15 focuses, "
            "including a civil war branch.")
        self.assertEqual(plan.feature, "focus_branch")
        self.assertEqual([t.id for t in plan.tasks],
                         ["focuses", "localisation", "validate", "apply"])
        self.assertEqual(plan.country_tag, "GER")

    def test_pure_civil_war_path_stays_civil_war(self):
        from hoi4_agent.planner import Planner

        plan = Planner().plan_project("create a civil war path for germany")
        self.assertEqual(plan.feature, "civil_war")
        self.assertIn("events", [t.id for t in plan.tasks])


if __name__ == "__main__":
    unittest.main()
