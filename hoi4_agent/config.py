# Owner: STABLE
"""Agent configuration: paths, safety rules, caps."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The agent is local-only by design: never let transformers reach the network
# (e.g. PEFT adapter checks), even when a model file is missing.
os.environ.setdefault("HF_HUB_OFFLINE", "1")


def load_vanilla_path() -> str:
    env = ROOT / ".env"
    if env.exists():
        for raw in env.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith("HOI4_GAME_PATH="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def load_workspace_path() -> str:
    """Workspace (mod) root. Precedence: HOI4_WORKSPACE_PATH env var,
    then the same key in the project .env file, then <repo>/workspace."""
    env = os.environ.get("HOI4_WORKSPACE_PATH", "")
    if env.strip():
        return env.strip().strip('"').strip("'")
    env_file = ROOT / ".env"
    if env_file.exists():
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith("HOI4_WORKSPACE_PATH="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return str(ROOT / "workspace")


def load_mod_start_date() -> str:
    """START_DATE from the mod's own common/defines/*.lua (e.g. "2002.1.1.12").
    Falls back to the vanilla default when the mod defines none."""
    ws = Path(CONFIG.workspace_root)
    defines_dir = ws / "common" / "defines"
    if defines_dir.exists():
        for f in sorted(defines_dir.glob("*.lua")):
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            m = re.search(r"START_DATE\s*=\s*[\"']([0-9.]+)[\"']", text)
            if m:
                return m.group(1)
    return "1936.1.1.12"


def mod_start_year() -> int:
    """The year of the mod's custom start date (or 1936 by default)."""
    try:
        return int(load_mod_start_date().split(".")[0])
    except (ValueError, IndexError):
        return 1936


@dataclass
class AgentConfig:
    workspace_root: Path = field(default_factory=lambda: Path(load_workspace_path()))
    vanilla_root: Path = field(default_factory=lambda: Path(load_vanilla_path()))
    index_dir: Path = ROOT / "data" / "processed" / "index"
    wiki_dir: Path = ROOT / "data" / "raw" / "wiki"
    docs_dir: Path = ROOT / "data" / "raw" / "game" / "documentation"
    code_blocks_file: Path = ROOT / "data" / "processed" / "index" / "code_blocks.jsonl"
    memory_dir: Path = ROOT / "data" / "agent_state"
    # Reasoning model. Qwen/Qwen3.5-2B is the recommended default; any cached
    # Qwen model works via HOI4_AGENT_MODEL (e.g. Qwen/Qwen2.5-0.5B-Instruct).
    model_id: str = os.environ.get("HOI4_AGENT_MODEL", "Qwen/Qwen3.5-2B")
    use_model: bool = os.environ.get("HOI4_AGENT_USE_MODEL", "1") == "1"
    max_read_lines: int = 500
    max_examples: int = 5
    max_search_results: int = 20
    auto_approve: bool = False
    # Directories that are never written (vanilla reference tree).
    read_only_roots: tuple[str, ...] = ("vanilla",)
    # Vanilla sub-trees searched by search_files / find_vanilla_examples.
    vanilla_search_dirs: tuple[str, ...] = (
        "common/national_focus",
        "common/decisions",
        "common/scripted_effects",
        "common/scripted_triggers",
        "common/on_actions",
        "common/ideas",
        "events",
        "localisation/english",
    )
    # Fuzzy lookup categories (localisation keys are exact/prefix only).
    fuzzy_categories: tuple[str, ...] = (
        "focuses", "events", "decisions", "ideas", "scripted_effects",
        "scripted_triggers", "on_actions", "countries", "states",
    )


CONFIG = AgentConfig()
