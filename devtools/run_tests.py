"""Targeted test runner — replaces "run everything" for module work.

Profiles map a subsystem to the smallest test set that covers it. Only
`all` touches the whole system.

Run:  .venv\\Scripts\\python.exe devtools\\run_tests.py planner
      .venv\\Scripts\\python.exe devtools\\run_tests.py all
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / ".venv" / "Scripts" / "python.exe"

PROFILES: dict[str, list[str]] = {
    "planner": [
        "hoi4_agent.tests.test_planner_country",
        "hoi4_agent.tests.test_project_generators",
    ],
    "project": [
        "hoi4_agent.tests.test_project_generators",
    ],
    "repair": [
        "hoi4_agent.tests.test_repair_loop",
    ],
    "validator": [
        "hoi4_agent.tests.test_repair_loop",
        "hoi4_agent.tests.test_project_generators",
    ],
    "preview": [
        "hoi4_agent.tests.test_preview",
    ],
    "approval": [
        "hoi4_agent.tests.test_approval",
    ],
    "workspace": [
        "hoi4_agent.tests.test_workspace_config",
    ],
    "vscode": [],  # handled specially: JS syntax + manifest validation
    "all": [],     # handled specially: unittest discovery
}


def run_python(args: list[str]) -> int:
    return subprocess.call([str(PY), *args], cwd=ROOT)


def vscode_checks() -> int:
    failed = 0
    src = ROOT / "vscode-extension" / "src"
    for f in sorted(src.glob("*.js")):
        rc = subprocess.call(["node", "--check", str(f)], cwd=ROOT)
        print(f"  node --check {f.name}: {'OK' if rc == 0 else 'FAIL'}")
        failed += rc != 0
    pkg = ROOT / "vscode-extension" / "package.json"
    try:
        json.loads(pkg.read_text(encoding="utf-8"))
        print("  package.json: OK")
    except json.JSONDecodeError as exc:
        print(f"  package.json: FAIL ({exc})")
        failed += 1
    return 0 if failed == 0 else 1


def main() -> int:
    profile = sys.argv[1] if len(sys.argv) > 1 else "all"
    if profile not in PROFILES:
        print(f"unknown profile {profile!r}; choose from: {', '.join(sorted(PROFILES))}")
        return 2
    if profile == "all":
        return run_python(["-m", "unittest", "discover", "-s", "hoi4_agent/tests"])
    if profile == "vscode":
        return vscode_checks()
    modules = PROFILES[profile]
    print(f"running {profile} profile: {len(modules)} module(s)")
    return run_python(["-m", "unittest"] + modules)


if __name__ == "__main__":
    sys.exit(main())
