"""Tests for configurable workspace (mod) root.

Run:  python -m unittest hoi4_agent.tests.test_workspace_config
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from hoi4_agent.config import load_workspace_path  # noqa: E402


class WorkspaceConfigTests(unittest.TestCase):
    def test_env_var_wins(self):
        os.environ["HOI4_WORKSPACE_PATH"] = r"C:\tmp\my-test-mod"
        try:
            self.assertEqual(Path(load_workspace_path()), Path(r"C:\tmp\my-test-mod"))
        finally:
            os.environ.pop("HOI4_WORKSPACE_PATH", None)

    def test_env_var_quotes_stripped(self):
        os.environ["HOI4_WORKSPACE_PATH"] = r'"C:\tmp\quoted mod"'
        try:
            self.assertEqual(Path(load_workspace_path()), Path(r"C:\tmp\quoted mod"))
        finally:
            os.environ.pop("HOI4_WORKSPACE_PATH", None)

    def test_falls_back_to_env_file_or_default(self):
        os.environ.pop("HOI4_WORKSPACE_PATH", None)
        value = load_workspace_path()
        # Either the .env override exists (a real mod path) or the repo default.
        self.assertTrue(Path(value).is_absolute())
        self.assertTrue("workspace" in value or "MeltingPot" in value)


if __name__ == "__main__":
    unittest.main()
