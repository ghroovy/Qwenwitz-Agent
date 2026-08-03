# Owner: STABLE
"""Shared helpers for the HOI4 dataset pipeline."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable, Iterator

ROOT = Path(__file__).resolve().parent.parent.parent
# --- runtime data (active project) ---
DATA_RAW = ROOT / "data" / "raw"
DATA_PROCESSED = ROOT / "data" / "processed"
DATA_INDEX = DATA_PROCESSED / "index"
ENV_FILE = ROOT / ".env"

# Local-only model layer: never let transformers contact the network.
os.environ.setdefault("HF_HUB_OFFLINE", "1")

# --- training-pipeline paths (archived; only archived scripts use these) ---
ARCHIVE_TRAINING = ROOT / "archive" / "training"
SOURCES_RAW = ARCHIVE_TRAINING / "sources" / "raw"
DATA_CORPUS = ARCHIVE_TRAINING / "datasets" / "corpus"
DATA_GENERATED = ARCHIVE_TRAINING / "datasets" / "generated"
DATA_EVAL = ARCHIVE_TRAINING / "datasets" / "eval"
REPORTS = ROOT / "archive" / "reports"
CONFIG = ARCHIVE_TRAINING / "config"

_TOKENIZER = None


def load_game_path() -> str:
    """Read HOI4_GAME_PATH from .env."""
    if not ENV_FILE.exists():
        raise FileNotFoundError(f"{ENV_FILE} not found")
    for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("HOI4_GAME_PATH="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise ValueError("HOI4_GAME_PATH not set in .env")


def get_tokenizer():
    """Lazily load the cached Qwen3.5 tokenizer (offline)."""
    global _TOKENIZER
    if _TOKENIZER is None:
        from transformers import AutoTokenizer

        _TOKENIZER = AutoTokenizer.from_pretrained(
            os.environ.get("HOI4_AGENT_MODEL", "Qwen/Qwen3.5-2B"),
            local_files_only=True,
        )
    return _TOKENIZER


def count_tokens(text: str) -> int:
    if not text:
        return 0
    try:
        return len(get_tokenizer().encode(text, add_special_tokens=False))
    except Exception:  # noqa: BLE001 - no cached tokenizer (model not downloaded)
        return len(text.split())


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def normalize_for_dedup(text: str) -> str:
    """Normalize code-ish text: lower case, strip comments, collapse whitespace."""
    text = re.sub(r"#[^\n]*", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def strip_comments(text: str) -> str:
    return re.sub(r"#[^\n]*", "", text)


def _balanced(text: str, open_ch: str, close_ch: str) -> bool:
    depth = 0
    for ch in text:
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def check_delimiters(text: str) -> tuple[bool, str]:
    """Check brace/paren/bracket balance ignoring comments and quoted strings."""
    cleaned = strip_comments(text)
    cleaned = re.sub(r'"[^"\n]*"', '""', cleaned)
    for op, cl in (("{", "}"), ("(", ")"), ("[", "]")):
        if not _balanced(cleaned, op, cl):
            return False, f"unbalanced {op}{cl}"
    return True, "ok"


def read_text(path: Path) -> str:
    """Read text with encoding fallbacks."""
    data = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def shingles(text: str, k: int = 6) -> set[str]:
    text = normalize_for_dedup(text)
    return {text[i : i + k] for i in range(max(0, len(text) - k + 1))}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def load_env_dotenv() -> None:
    """Minimal .env loader for the project (no external dep needed)."""
    if ENV_FILE.exists():
        for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
