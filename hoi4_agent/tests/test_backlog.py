"""Tests for the pending-diff backlog (accumulating review batches)."""

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


class BacklogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(dir=ROOT / "workspace"))
        self._old_root = filesystem.CONFIG.workspace_root
        self._old_mem = CONFIG.memory_dir
        filesystem.CONFIG.workspace_root = self.tmp
        CONFIG.memory_dir = self.tmp / "state"
        loc = self.tmp / "localisation" / "english"
        loc.mkdir(parents=True)
        (loc / "ind_l_english.yml").write_text(
            "l_english:\n IND_base:0 \"Base\"\n", encoding="utf-8")

    def tearDown(self):
        filesystem.CONFIG.workspace_root = self._old_root
        CONFIG.memory_dir = self._old_mem
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _agent(self):
        agent = Agent(auto_approve=True, use_model=False)
        agent._ask_approval = lambda diff: False
        return agent

    def test_batches_accumulate(self):
        agent = self._agent()
        agent._prepare_pending(
            {"common/national_focus/ind_agent_focus.txt": "focus_tree = {\n}\n"},
            label="Focus Tree IND", project_slug="focus-tree-ind")
        agent._prepare_pending(
            {"common/decisions/IND_agent.txt": "ind_agent_decisions = {\n}\n"},
            label="Decisions IND", project_slug="decisions-ind")
        self.assertEqual(len(agent.pending_batches), 2)
        self.assertEqual([b["label"] for b in agent.pending_batches],
                         ["Focus Tree IND", "Decisions IND"])
        # The latest batch is still self.pending for backward compatibility.
        self.assertEqual(list(agent.pending["proposals"]),
                         ["common/decisions/IND_agent.txt"])

    def test_approve_all_applies_and_merges_shared_loc_file(self):
        agent = self._agent()
        base = "l_english:\n IND_base:0 \"Base\"\n"
        agent._prepare_pending({
            "localisation/english/ind_l_english.yml": base + " IND_focus_01:0 \"F1\"\n",
        }, label="b1")
        agent._prepare_pending({
            "localisation/english/ind_l_english.yml": base + " IND_dec_01:0 \"D1\"\n",
        }, label="b2")
        r = agent.approve_pending(approve_all=True)
        self.assertEqual(r["failed"], [])
        text = (self.tmp / "localisation" / "english" / "ind_l_english.yml").read_text(
            encoding="utf-8-sig")
        self.assertIn("IND_base", text)
        self.assertIn("IND_focus_01", text)
        self.assertIn("IND_dec_01", text)
        self.assertEqual([b["status"] for b in agent.pending_batches],
                         ["applied", "applied"])

    def test_reject_batch_and_undo(self):
        agent = self._agent()
        agent._prepare_pending(
            {"common/national_focus/ind_agent_focus.txt": "focus_tree = {\n}\n"},
            label="b1")
        batch_id = agent.pending_batches[0]["id"]
        agent.reject_pending(batch_id=batch_id)
        self.assertEqual(agent.pending_batches[0]["status"], "rejected")
        self.assertEqual(agent.pending_batches[0]["proposals"], {})

        agent._prepare_pending(
            {"common/decisions/IND_agent.txt": "ind_agent_decisions = {\n}\n"},
            label="b2")
        agent.approve_pending(batch_id=agent.pending_batches[-1]["id"])
        self.assertTrue((self.tmp / "common/decisions/IND_agent.txt").exists())
        undo = agent.undo_applied("common/decisions/IND_agent.txt")
        self.assertTrue(undo["undone"])
        self.assertFalse((self.tmp / "common/decisions/IND_agent.txt").exists())

    def test_backlog_persists_across_agent_instances(self):
        agent = self._agent()
        agent._prepare_pending(
            {"common/national_focus/ind_agent_focus.txt": "focus_tree = {\n}\n"},
            label="Persisted Batch", project_slug="focus-tree-ind")
        agent2 = self._agent()
        self.assertEqual(len(agent2.pending_batches), 1)
        self.assertEqual(agent2.pending_batches[0]["label"], "Persisted Batch")
        self.assertEqual(agent2.pending_batches[0]["project_slug"], "focus-tree-ind")

    def test_approve_all_skips_already_applied_batches(self):
        agent = self._agent()
        agent._prepare_pending(
            {"common/national_focus/ind_agent_focus.txt": "focus_tree = {\n\tid = ind\n}\n"},
            label="Focus IND")
        first_id = agent.pending_batches[0]["id"]
        agent._prepare_pending(
            {"common/decisions/IND_agent.txt": "ind_agent_decisions = {\n}\n"},
            label="Decisions IND")
        # Approve the first batch only.
        agent.approve_pending(batch_id=first_id)
        self.assertEqual(agent.pending_batches[0]["status"], "applied")
        # Approve All must NOT re-apply the applied batch (no duplication).
        r = agent.approve_pending(approve_all=True)
        self.assertEqual(r["applied"], ["common/decisions/IND_agent.txt"])
        self.assertEqual(r["failed"], [])
        focus = (self.tmp / "common/national_focus" / "ind_agent_focus.txt").read_text(
            encoding="utf-8")
        self.assertEqual(focus.count("focus_tree = {"), 1)
        # Second approve-all is a no-op.
        self.assertEqual(agent.approve_pending(approve_all=True)["applied"], [])

    def test_approve_all_after_everything_applied_is_noop(self):
        agent = self._agent()
        agent._prepare_pending(
            {"common/national_focus/ind_agent_focus.txt": "focus_tree = {\n}\n"},
            label="Focus IND")
        agent.approve_pending(approve_all=True)
        before = (self.tmp / "common/national_focus" / "ind_agent_focus.txt").read_text(
            encoding="utf-8")
        r = agent.approve_pending(approve_all=True)
        self.assertEqual(r["applied"], [])
        after = (self.tmp / "common/national_focus" / "ind_agent_focus.txt").read_text(
            encoding="utf-8")
        self.assertEqual(before, after)

    def test_per_file_approval_then_approve_all_completes_batch(self):
        agent = self._agent()
        agent._prepare_pending({
            "common/national_focus/ind_agent_focus.txt": "focus_tree = {\n}\n",
            "common/decisions/IND_agent.txt": "ind_agent_decisions = {\n}\n",
        }, label="Mixed")
        batch = agent.pending_batches[0]
        agent.approve_pending(file="common/national_focus/ind_agent_focus.txt",
                              batch_id=batch["id"])
        self.assertEqual(batch["status"], "partial")
        r = agent.approve_pending(approve_all=True)
        self.assertEqual(r["applied"], ["common/decisions/IND_agent.txt"])
        self.assertEqual(batch["status"], "applied")
        self.assertTrue((self.tmp / "common/decisions/IND_agent.txt").exists())


if __name__ == "__main__":
    unittest.main()
