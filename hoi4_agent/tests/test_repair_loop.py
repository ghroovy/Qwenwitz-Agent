"""Automated tests for the repair loop (no model, fully deterministic).

Run:  python -m unittest hoi4_agent.tests.test_repair_loop
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from hoi4_agent._runtime.common import read_json  # noqa: E402
from hoi4_agent.identifier_index import IdentifierIndex  # noqa: E402
from hoi4_agent.memory import SessionMemory  # noqa: E402
from hoi4_agent.repair import RepairEngine  # noqa: E402
from hoi4_agent.tools import ToolContext, Tools  # noqa: E402
from hoi4_agent.validator import Validator  # noqa: E402


def make_engine():
    index = IdentifierIndex()
    validator = Validator(
        index,
        read_json(ROOT / "data" / "processed" / "index" / "effects.json"),
        read_json(ROOT / "data" / "processed" / "index" / "triggers.json"),
        read_json(ROOT / "data" / "processed" / "index" / "modifiers.json"),
    )
    ctx = ToolContext(index, validator, SessionMemory())
    tools = Tools(ctx)
    return RepairEngine(ctx, validator, tools, agent=None), validator, index


def repair_until_valid(proposals: dict[str, str], max_attempts: int = 5):
    engine, validator, index = make_engine()
    final, validation, log = engine.run_repair_loop(proposals, max_attempts=max_attempts)
    return final, validation, log, validator


class TestRepairLoop(unittest.TestCase):
    FOCUS_FILE = "common/national_focus/test.txt"
    LOC_FILE = "localisation/english/test_l_english.yml"

    def test_broken_braces(self):
        content = (
            "focus = {\n"
            "\tid = TST_brace_test\n"
            "\tcompletion_reward = {\n"
            "\t\tadd_political_power = 10\n"
            "\t}\n"  # missing closing brace for focus
        )
        final, validation, log, _ = repair_until_valid({self.FOCUS_FILE: content})
        self.assertTrue(validation["valid"], validation["errors"])
        self.assertIn("brace_mismatch", [e["type"] for e in log[0].validator_errors])

    def test_invalid_identifier(self):
        ideas = read_json(ROOT / "data" / "processed" / "index" / "ideas.json")
        real = next(k for k in ideas if k.startswith("GER_"))
        typo = real[:-1] + "x"
        content = (
            "focus = {\n"
            f"\tid = TST_idea_test\n"
            "\tcompletion_reward = {\n"
            f"\t\tadd_ideas = {{ {typo} }}\n"
            "\t}\n"
            "}\n"
        )
        final, validation, log, _ = repair_until_valid({self.FOCUS_FILE: content})
        self.assertTrue(validation["valid"], validation["errors"])
        self.assertIn(real, final[self.FOCUS_FILE])
        self.assertIn("unknown_identifier", [e["type"] for e in log[0].validator_errors])

    def test_duplicate_ids(self):
        content = (
            "focus = {\n\tid = TST_dup\n\tcompletion_reward = { add_political_power = 1 }\n}\n"
            "focus = {\n\tid = TST_dup\n\tcompletion_reward = { add_political_power = 2 }\n}\n"
        )
        final, validation, log, _ = repair_until_valid(
            {self.FOCUS_FILE: content, self.LOC_FILE: "l_english:\n TST_dup:0 \"x\"\n TST_dup_desc:0 \"x\"\n"})
        self.assertTrue(validation["valid"], validation["errors"])
        ids = [line.strip().split(" = ")[1] for line in final[self.FOCUS_FILE].splitlines() if "id = " in line]
        self.assertEqual(len(set(ids)), 2)
        self.assertIn("duplicate_identifier", [e["type"] for e in log[0].validator_errors])

    def test_missing_localisation(self):
        content = (
            "focus = {\n\tid = TST_loc_missing\n\tcompletion_reward = { add_political_power = 1 }\n}\n"
        )
        final, validation, log, _ = repair_until_valid({self.FOCUS_FILE: content})
        self.assertTrue(validation["valid"], validation["errors"])
        self.assertTrue(any("localisation" in p for p in final))
        self.assertIn("missing_localisation", [e["type"] for e in log[0].validator_errors])

    def test_scope_error(self):
        content = (
            "focus = {\n"
            "\tid = TST_scope_test\n"
            "\tcompletion_reward = {\n"
            "\t\tadd_building_construction = { type = industrial_complex level = 1 }\n"
            "\t}\n"
            "}\n"
        )
        final, validation, log, _ = repair_until_valid({self.FOCUS_FILE: content})
        self.assertTrue(validation["valid"], validation["errors"])
        self.assertIn("random_owned_controlled_state", final[self.FOCUS_FILE])
        self.assertIn("invalid_scope", [e["type"] for e in log[0].validator_errors])

    def test_broken_reference(self):
        focuses = read_json(ROOT / "data" / "processed" / "index" / "focuses.json")
        real = next(k for k in focuses if k.startswith("GER_"))
        typo = real[:-1] + "x"
        content = (
            "focus = {\n"
            f"\tid = TST_ref_test\n"
            f"\tprerequisite = {{ focus = {typo} }}\n"
            "\tcompletion_reward = { add_political_power = 1 }\n"
            "}\n"
        )
        final, validation, log, _ = repair_until_valid({self.FOCUS_FILE: content})
        self.assertTrue(validation["valid"], validation["errors"])
        self.assertIn(real, final[self.FOCUS_FILE])
        self.assertIn("broken_reference", [e["type"] for e in log[0].validator_errors])

    def test_mixed_errors(self):
        focuses = read_json(ROOT / "data" / "processed" / "index" / "focuses.json")
        real_focus = next(k for k in focuses if k.startswith("GER_"))
        content = (
            "focus = {\n"
            f"\tid = TST_mixed\n"
            f"\tprerequisite = {{ focus = {real_focus[:-1] + 'x'} }}\n"
            "\tcompletion_reward = {\n"
            "\t\tadd_building_construction = { type = industrial_complex level = 1 }\n"
            "\t\tadd_political_power = 5\n"
            "\t}\n"  # missing focus closing brace
        )
        final, validation, log, _ = repair_until_valid({self.FOCUS_FILE: content})
        self.assertTrue(validation["valid"], validation["errors"])
        self.assertIn("random_owned_controlled_state", final[self.FOCUS_FILE])
        self.assertTrue(any("localisation" in p for p in final))
        self.assertGreaterEqual(len(log), 1)

    def test_unfixable_exhausts_retries(self):
        content = (
            "focus = {\n"
            "\tid = TST_doomed\n"
            "\tcompletion_reward = { add_ideas = { zzz_nonexistent_idea_xyz } }\n"
            "}\n"
        )
        final, validation, log, _ = repair_until_valid({self.FOCUS_FILE: content}, max_attempts=3)
        self.assertFalse(validation["valid"])
        self.assertEqual(len(log), 3)


if __name__ == "__main__":
    unittest.main()
