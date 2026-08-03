# Owner: ACTIVE
"""Session memory: tool history, verified/rejected identifiers, files touched."""

from __future__ import annotations

import json
import time
from pathlib import Path

from .config import CONFIG


class SessionMemory:
    def __init__(self, session_id: str | None = None):
        self.session_id = session_id or f"session_{int(time.time())}"
        self.steps: list[dict] = []
        self.verified_identifiers: dict[str, str] = {}
        self.rejected_identifiers: dict[str, str] = {}
        self.files_touched: list[str] = []
        self.notes: list[str] = []
        # Objects this session created (focus/event/decision ids), for
        # resolving contextual follow-ups like "make it cheaper".
        self.created_ids: list[str] = []
        # The object most recently mentioned/targeted by a follow-up edit
        # ("make it cheaper" after naming GEN_x must edit GEN_x).
        self.last_mentioned_id: str | None = None

    def record(self, tool: str, args: dict, result_summary: str, ok: bool) -> None:
        self.steps.append({
            "tool": tool,
            "args": args,
            "result_summary": result_summary[:500],
            "ok": ok,
            "ts": time.time(),
        })

    def verify_identifier(self, name: str, info: str) -> None:
        self.verified_identifiers[name] = info
        self.rejected_identifiers.pop(name, None)

    def reject_identifier(self, name: str, reason: str) -> None:
        self.rejected_identifiers[name] = reason

    def context_summary(self, max_steps: int = 40) -> str:
        lines = []
        if self.verified_identifiers:
            lines.append("VERIFIED IDENTIFIERS (from the vanilla index):")
            for k, v in list(self.verified_identifiers.items())[:40]:
                lines.append(f"- {k}: {v}")
        if self.rejected_identifiers:
            lines.append("REJECTED IDENTIFIERS (do NOT use):")
            for k, v in list(self.rejected_identifiers.items())[:20]:
                lines.append(f"- {k}: {v}")
        for step in self.steps[-max_steps:]:
            lines.append(f"TOOL {step['tool']}({json.dumps(step['args'])[:160]}): {step['result_summary'][:220]}")
        return "\n".join(lines)

    def save(self, path: Path | None = None) -> Path:
        target = path or CONFIG.memory_dir / f"{self.session_id}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({
            "session_id": self.session_id,
            "steps": self.steps,
            "verified_identifiers": self.verified_identifiers,
            "rejected_identifiers": self.rejected_identifiers,
            "files_touched": self.files_touched,
            "notes": self.notes,
            "created_ids": self.created_ids,
        }, indent=2), encoding="utf-8")
        return target

