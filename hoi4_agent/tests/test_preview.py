"""Automated tests for the preview system (map, focus tree, events, decisions).

Run:  python -m unittest hoi4_agent.tests.test_preview
"""

from __future__ import annotations

import base64
import json
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from hoi4_agent.identifier_index import IdentifierIndex  # noqa: E402
from hoi4_agent.preview import decision_preview, event_preview, focus_preview, map_preview  # noqa: E402
from hoi4_agent.preview.inspect_preview import preview_inspect  # noqa: E402
from hoi4_agent.preview.localisation import localisation_size  # noqa: E402


class PreviewTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = IdentifierIndex()

    def _decode_ids(self, payload):
        raw = base64.b64decode(payload["ids"])
        return np.frombuffer(raw, dtype="<u2").reshape(payload["height"], payload["width"])


class MapPreviewTests(PreviewTestBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.map_payload = map_preview.preview_map()

    def test_structure(self):
        p = self.map_payload
        self.assertIn("image", p)
        self.assertIn("ids", p)
        self.assertIn("owners", p)
        self.assertGreater(p["width"], 500)
        self.assertGreater(p["height"], 200)
        self.assertEqual(len(p["owner_tags"]), len(set(p["owner_tags"])))
        self.assertIn("land", p["province_types"])
        self.assertIn("sea", p["province_types"])

    def test_grounded_province_grid(self):
        p = self.map_payload
        grid = self._decode_ids(p)
        # Every non-zero id must be a real province from definition.csv.
        ids = set(np.unique(grid))
        known = set(map_preview._load().id_meta.keys())
        self.assertTrue(ids.issubset(known))
        self.assertGreater(len(ids), 10000)  # most provinces survive downsampling

    def test_pixel_color_round_trip(self):
        """Image pixel color at (x, y) must match definition.csv RGB of the id there."""
        p = self.map_payload
        grid = self._decode_ids(p)
        img = np.array(__import__("PIL").Image.open(__import__("io").BytesIO(
            base64.b64decode(p["image"].split(",", 1)[1]))))
        d = map_preview._load()
        found = 0
        for y in range(0, p["height"], 40):
            for x in range(0, p["width"], 40):
                pid = int(grid[y, x])
                if not pid:
                    continue
                meta = d.id_meta.get(pid, {})
                if not meta:
                    continue
                self.assertEqual(
                    (int(img[y, x, 0]), int(img[y, x, 1]), int(img[y, x, 2])),
                    (meta.get("r"), meta.get("g"), meta.get("b")),
                    msg=f"pixel mismatch at {x},{y} for province {pid}",
                )
                found += 1
                if found > 20:
                    return
        self.assertGreater(found, 0, "no province pixels found to check")

    def test_province_info(self):
        info = map_preview.province_info(1)
        self.assertTrue(info["ok"])
        self.assertIn("type", info)
        self.assertIn("state", info)
        self.assertIn("owner", info)
        self.assertIn("state_info", info)
        bad = map_preview.province_info(999999)
        self.assertFalse(bad["ok"])

    def test_view_modes(self):
        for mode in ("province", "state", "country", "strategic_region", "supply_area"):
            p = map_preview.preview_map(mode=mode)
            self.assertEqual(p["mode"], mode)
            self.assertIn("mode_ids", p)
            self.assertIsInstance(p["mode_meta"], list)
            grid = np.frombuffer(base64.b64decode(p["mode_ids"]), dtype="<u2")
            self.assertEqual(len(grid), p["height"] * p["width"])
            nonzero = set(np.unique(grid))
            if mode == "state":
                known = set(map_preview._load().states.keys())
                grid_ids = {p["mode_meta"][v - 1]["id"] for v in nonzero if v}
                self.assertTrue(grid_ids.issubset(known))
                self.assertGreater(len(nonzero), 500)
                self.assertGreater(len(p["mode_meta"]), 500)
            elif mode == "strategic_region":
                known = set(map_preview._load().regions.keys())
                self.assertTrue(nonzero.issubset(known))
                self.assertGreater(len(p["mode_meta"]), 100)
            elif mode == "country":
                self.assertEqual(len(p["mode_meta"]), len(p["owner_tags"]))

    def test_state_info(self):
        info = map_preview.state_info(5)
        self.assertTrue(info["ok"])
        self.assertEqual(info["type"], "state")
        self.assertIn("name", info)
        self.assertIn("owner", info)
        self.assertGreater(info["province_count"], 0)
        bad = map_preview.state_info(999999)
        self.assertFalse(bad["ok"])

    def test_strategic_region_info(self):
        info = map_preview.strategic_region_info(6)
        self.assertTrue(info["ok"])
        self.assertIn("name", info)
        self.assertGreater(info["province_count"], 0)

    def test_supply_area_info(self):
        info = map_preview.supply_area_info(1)
        self.assertTrue(info["ok"])
        self.assertGreater(info["state_count"], 0)


class FocusPreviewTests(PreviewTestBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.payload = focus_preview.preview_focus_tree("common/national_focus/germany.txt")

    def test_structure(self):
        p = self.payload
        self.assertEqual(p["kind"], "focus_tree")
        self.assertEqual(len(p["trees"]), 1)
        tree = p["trees"][0]
        self.assertGreater(tree["focus_count"], 100)
        self.assertIn("GER", tree["country_tags"])

    def test_known_focus_with_localisation(self):
        tree = self.payload["trees"][0]
        by_id = {n["id"]: n for n in tree["nodes"]}
        self.assertIn("GER_oppose_hitler", by_id)
        node = by_id["GER_oppose_hitler"]
        self.assertEqual(node["title"], "Oppose Hitler")
        self.assertIsInstance(node["x"], int)
        self.assertIsInstance(node["y"], int)

    def test_identifiers_grounded_in_index(self):
        tree = self.payload["trees"][0]
        known = set(self.index.categories().get("focuses", {}).keys())
        for n in tree["nodes"]:
            self.assertIn(n["id"], known, msg=f"focus {n['id']} not in vanilla index")

    def test_edges_consistent(self):
        nodes = {n["id"] for n in self.payload["trees"][0]["nodes"]}
        for e in self.payload["edges"]:
            self.assertIn(e["from"], nodes)
            self.assertIn(e["to"], nodes)


class EventPreviewTests(PreviewTestBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.payload = event_preview.preview_events("events/AAT_Denmark.txt", max_events=200)

    def test_structure(self):
        p = self.payload
        self.assertEqual(p["kind"], "events")
        self.assertGreater(p["count"], 0)
        self.assertIn("denmark_political_events.1", {e["id"] for e in p["events"]})

    def test_titles_and_chains(self):
        by_id = {e["id"]: e for e in self.payload["events"]}
        first = by_id["denmark_political_events.1"]
        self.assertEqual(first["type"], "country_event")
        self.assertEqual(first["title"], "Motion of No Confidence")
        self.assertIn("denmark_political_events.2", first["refs"])
        self.assertIn("denmark_political_events.3", first["refs"])
        self.assertGreaterEqual(first["option_count"], 2)


class DecisionPreviewTests(PreviewTestBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.payload = decision_preview.preview_decisions("common/decisions/AFG.txt")

    def test_structure(self):
        p = self.payload
        self.assertEqual(p["kind"], "decisions")
        self.assertGreaterEqual(p["count"], 20)
        self.assertGreater(len(p["categories"]), 5)
        by_id = {d["id"] for d in p["decisions"]}
        self.assertIn("AFG_propose_oil_concession_deal", by_id)

    def test_titles(self):
        titled = [d for d in self.payload["decisions"] if d["title"]]
        self.assertGreater(len(titled), 5)


class InspectPreviewTests(PreviewTestBase):
    def test_inspect_focus(self):
        r = preview_inspect("focus", "GER_oppose_hitler", tools=None, index=self.index)
        self.assertTrue(r["ok"])
        self.assertEqual(r["localisation"]["title"], "Oppose Hitler")

    def test_inspect_province(self):
        r = preview_inspect("province", "12000", index=self.index)
        self.assertTrue(r["ok"])
        self.assertIn("type", r)
        self.assertIn("state", r)
        # Owner may come from vanilla or the configured workspace mod; either is fine.
        self.assertIn("owner", r)

    def test_inspect_unknown_identifier(self):
        r = preview_inspect("identifier", "GER_fake_identifier_xyz", tools=None, index=self.index)
        self.assertFalse(r["ok"])
        self.assertIn("similar", r)

    def test_localisation_index(self):
        self.assertGreater(localisation_size(), 100000)


if __name__ == "__main__":
    unittest.main()
