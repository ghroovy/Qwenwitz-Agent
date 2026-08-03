"""Regression tests: nothing applies without approval (pending staging).

Run:  python -m unittest hoi4_agent.tests.test_approval
"""

from __future__ import annotations

import sys
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from hoi4_agent.agent import Agent  # noqa: E402


class ApprovalStagingTests(unittest.TestCase):
    def setUp(self):
        from hoi4_agent import filesystem

        self.tmp = Path(tempfile.mkdtemp(dir=ROOT / "workspace"))
        self._old_root = filesystem.CONFIG.workspace_root
        filesystem.CONFIG.workspace_root = self.tmp

    def tearDown(self):
        from hoi4_agent import filesystem

        filesystem.CONFIG.workspace_root = self._old_root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_promptless_stages_pending_without_applying(self):
        agent = Agent(auto_approve=False, use_model=False)
        agent.promptless = True  # server mode: never ask, never auto-apply
        proposals = {"localisation/english/_approval_test.yml": "l_english:\n X_test_key:0 \"test\"\n"}
        ok, msg = agent._apply_proposals(proposals)
        self.assertFalse(ok)
        self.assertEqual(msg, "pending review")
        self.assertIn("localisation/english/_approval_test.yml", agent.pending["proposals"])
        # Nothing written to disk.
        self.assertFalse((self.tmp / "localisation" / "english" / "_approval_test.yml").exists())

    def test_auto_approve_false_never_applies(self):
        agent = Agent(auto_approve=False, use_model=False)
        agent.promptless = False
        agent._ask_approval = lambda diff: False  # user declines
        ok, msg = agent._apply_proposals({"localisation/english/_declined.yml": "l_english:\n"})
        self.assertFalse(ok)
        self.assertIn("declined", msg)
        self.assertIn("localisation/english/_declined.yml", agent.pending["proposals"])


if __name__ == "__main__":
    unittest.main()
