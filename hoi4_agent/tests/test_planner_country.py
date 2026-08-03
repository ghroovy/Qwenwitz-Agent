"""Regression tests: new-country detection and no-Germany fallback.

Run:  python -m unittest hoi4_agent.tests.test_planner_country
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from hoi4_agent.planner import Planner  # noqa: E402


class NewCountryDetectionTests(unittest.TestCase):
    def setUp(self):
        self.planner = Planner()

    def test_make_x_a_country(self):
        result = self.planner._detect_new_country("make westeros a country")
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "Westeros")
        # WES already exists in the open workspace mod (Western Sahara), so the
        # tag generator must pick a collision-free alternative.
        self.assertNotEqual(result[1], "WES")
        self.assertEqual(len(result[1]), 3)

    def test_make_x_into_a_nation(self):
        result = self.planner._detect_new_country("make bajookistan into a nation")
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "Bajookistan")

    def test_create_a_country_called(self):
        result = self.planner._detect_new_country("create a new country called Valinor")
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "Valinor")
        self.assertEqual(result[1], "VAL")

    def test_known_country_is_not_new(self):
        self.assertIsNone(self.planner._detect_new_country("make germany a communist country"))
        self.assertIsNone(self.planner._detect_new_country("make france a country"))

    def test_no_country_keyword_returns_none(self):
        self.assertIsNone(self.planner._detect_new_country("make a bajookistan focus tree"))

    def test_project_plan_new_country(self):
        plan = self.planner.plan_project("make westeros a country")
        self.assertEqual(plan.feature, "new_country")
        self.assertEqual(plan.new_country_name, "Westeros")
        self.assertNotEqual(plan.country_tag, "WES")
        self.assertEqual(len(plan.country_tag), 3)

    def test_project_plan_unknown_country_never_germany(self):
        plan = self.planner.plan_project("make a bajookistan focus tree")
        self.assertEqual(plan.feature, "unknown_country")
        self.assertEqual(plan.country_tag, "")

    def test_country_file_phrasings_are_not_new_countries(self):
        self.assertIsNone(self.planner._detect_new_country(
            "add a country hisoory file for spain with fascist ruling party"))
        self.assertIsNone(self.planner._detect_new_country(
            "create a country file for germany"))

    def test_known_country_still_resolves(self):
        plan = self.planner.plan_project("add a communist path for germany")
        self.assertEqual(plan.country_tag, "GER")
        self.assertEqual(plan.feature, "focus_branch")

    def test_resolve_country_no_fallback(self):
        self.assertIsNone(self.planner._resolve_country("make a bajookistan focus tree", "make a bajookistan focus tree"))
        self.assertEqual(self.planner._resolve_country("add a focus for russia", "add a focus for russia"), "SOV")


if __name__ == "__main__":
    unittest.main()
