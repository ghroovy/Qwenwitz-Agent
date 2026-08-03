# Owner: ACTIVE
"""Deterministic tool framework: 20+ tools over workspace, vanilla files,
identifier index, wiki/docs, and validators."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from hoi4_agent._runtime.hoi4parser import node_raw, parse_tree  # noqa: E402

from . import filesystem, patcher  # noqa: E402
from .config import CONFIG  # noqa: E402


@dataclass
class ToolResult:
    tool: str
    ok: bool
    message: str = ""
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"tool": self.tool, "ok": self.ok, "message": self.message, "data": self.data}


class ToolContext:
    def __init__(self, index, validator, memory, code_blocks: list[dict] | None = None):
        self.index = index
        self.validator = validator
        self.memory = memory
        self.code_blocks = code_blocks
        if code_blocks is None:
            self.code_blocks = []
            if CONFIG.code_blocks_file.exists():
                import json

                for line in CONFIG.code_blocks_file.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        self.code_blocks.append(json.loads(line))


def _text_search(files: list[Path], query: str, limit: int) -> list[dict]:
    return filesystem.find_in_files(files, query, limit)


def _vanilla_files() -> list[Path]:
    out: list[Path] = []
    root = filesystem.vanilla()
    if not root.exists():
        return out
    for rel in CONFIG.vanilla_search_dirs:
        base = root / rel
        if base.exists():
            out.extend(filesystem.walk_text_files(base))
    return out


def _scripted_block(path: str, block_id: str, kind: str) -> dict:
    abs_path = filesystem.classify(path)[1]
    text = abs_path.read_text(encoding="utf-8", errors="replace")
    for node in parse_tree(text):
        if node.get("kind") == "block" and node.get("key") == block_id:
            raw = node_raw(text, node)
            return {"type": kind, "id": block_id, "file": path,
                    "text": raw[:6000], "tokens": len(raw.split())}
    return {}


class Tools:
    """Registry of deterministic tools. Each returns ToolResult."""

    def __init__(self, ctx: ToolContext):
        self.ctx = ctx

    # ------------------------------------------------------------- filesystem
    def list_directory(self, path: str = ".") -> ToolResult:
        try:
            data = filesystem.list_directory(path)
            return ToolResult("list_directory", True, f"{len(data['entries'])} entries", data)
        except Exception as exc:  # noqa: BLE001
            return ToolResult("list_directory", False, str(exc))

    def search_files(self, query: str, scope: str = "workspace") -> ToolResult:
        try:
            if scope == "vanilla":
                files = _vanilla_files()
            else:
                files = filesystem.walk_text_files(filesystem.workspace())
            results = _text_search(files, query, CONFIG.max_search_results)
            return ToolResult("search_files", True, f"{len(results)} matches", {"results": results})
        except Exception as exc:  # noqa: BLE001
            return ToolResult("search_files", False, str(exc))

    def read_file(self, path: str, start_line: int = 1, end_line: int | None = None) -> ToolResult:
        try:
            data = filesystem.read_file(path, start_line, end_line or CONFIG.max_read_lines + start_line - 1)
            return ToolResult("read_file", True, f"{data['total_lines']} lines", data)
        except Exception as exc:  # noqa: BLE001
            return ToolResult("read_file", False, str(exc))

    # ----------------------------------------------------------- identifiers
    def search_identifier(self, name: str) -> ToolResult:
        results = self.ctx.index.search(name)
        if results:
            return ToolResult("search_identifier", True, f"{len(results)} match(es)", {"results": results})
        similar = self.ctx.index.fuzzy(name, limit=5)
        return ToolResult(
            "search_identifier", False,
            f"`{name}` could not be verified in the vanilla index",
            {"results": [], "similar": similar},
        )

    def find_similar_identifier(self, name: str) -> ToolResult:
        results = self.ctx.index.fuzzy(name, limit=CONFIG.max_search_results)
        return ToolResult("find_similar_identifier", bool(results),
                          f"{len(results)} suggestion(s)", {"results": results})

    # -------------------------------------------------------------- inspect
    def _inspect_block(self, tool: str, block_type: str, block_id: str, kind_label: str) -> ToolResult:
        matches = [b for b in self.ctx.code_blocks
                   if b["type"] == block_type and b["id"] == block_id]
        if matches:
            b = matches[0]
            return ToolResult(tool, True, f"{kind_label} `{block_id}` found",
                              {"type": b["type"], "id": b["id"], "file": b["file"],
                               "tokens": b["tokens"], "text": b["text"][:6000]})
        return ToolResult(tool, False, f"{kind_label} `{block_id}` not found in vanilla code index")

    def inspect_focus(self, id: str) -> ToolResult:
        return self._inspect_block("inspect_focus", "focus", id, "focus")

    def inspect_event(self, id: str) -> ToolResult:
        for b in self.ctx.code_blocks:
            if b["type"].startswith("event:") and b["id"] == id:
                return ToolResult("inspect_event", True, f"event `{id}` found",
                                  {"type": b["type"], "id": b["id"], "file": b["file"],
                                   "tokens": b["tokens"], "text": b["text"][:6000]})
        return ToolResult("inspect_event", False, f"event `{id}` not found in vanilla code index")

    def inspect_decision(self, id: str) -> ToolResult:
        return self._inspect_block("inspect_decision", "decision", id, "decision")

    def inspect_scripted_effect(self, id: str) -> ToolResult:
        path = self.ctx.index.categories().get("scripted_effects", {}).get(id)
        if not path:
            return ToolResult("inspect_scripted_effect", False, f"scripted effect `{id}` not found in index")
        block = _scripted_block(path, id, "scripted_effect")
        return ToolResult("inspect_scripted_effect", bool(block), "found" if block else "block not found", block)

    def inspect_scripted_trigger(self, id: str) -> ToolResult:
        path = self.ctx.index.categories().get("scripted_triggers", {}).get(id)
        if not path:
            return ToolResult("inspect_scripted_trigger", False, f"scripted trigger `{id}` not found in index")
        block = _scripted_block(path, id, "scripted_trigger")
        return ToolResult("inspect_scripted_trigger", bool(block), "found" if block else "block not found", block)

    # ------------------------------------------------------------- knowledge
    def search_documentation(self, query: str) -> ToolResult:
        docs = self.ctx.validator.effects | self.ctx.validator.triggers
        exact = {k: v for k, v in docs.items() if k == query}
        fuzzy = {k: v for k, v in docs.items() if query in k and query != k}
        entries = []
        for k, v in (exact or fuzzy).items():
            entries.append({"name": k, "scopes": v.get("scopes", []),
                            "description": (v.get("description") or "")[:300],
                            "example": (v.get("example") or "")[:300]})
        if entries:
            return ToolResult("search_documentation", True, f"{len(entries)} doc entr(ies)", {"entries": entries})
        # full-text fallback over the official docs markdown
        files = sorted(CONFIG.docs_dir.glob("*.md")) if CONFIG.docs_dir.exists() else []
        hits = _text_search(files, query, CONFIG.max_search_results)
        return ToolResult("search_documentation", bool(hits),
                          f"{len(hits)} doc snippet(s)", {"entries": [], "snippets": hits})

    def search_wiki(self, query: str) -> ToolResult:
        files = sorted(CONFIG.wiki_dir.glob("*.md")) if CONFIG.wiki_dir.exists() else []
        hits = _text_search(files, query, CONFIG.max_search_results)
        return ToolResult("search_wiki", bool(hits), f"{len(hits)} wiki snippet(s)", {"snippets": hits})

    def find_vanilla_examples(self, query: str) -> ToolResult:
        examples = []
        for b in self.ctx.code_blocks:
            if query in b["text"]:
                idx = b["text"].find(query)
                start = max(0, b["text"].rfind("\n", 0, idx))
                examples.append({"type": b["type"], "id": b["id"], "file": b["file"],
                                 "snippet": b["text"][start:start + 500]})
                if len(examples) >= CONFIG.max_examples:
                    break
        if len(examples) < CONFIG.max_examples:
            extra = _text_search(_vanilla_files(), query, CONFIG.max_examples - len(examples))
            examples.extend({"type": "file_match", "id": None, "file": e["file"],
                             "snippet": f"line {e['line']}: {e['content']}"} for e in extra)
        return ToolResult("find_vanilla_examples", bool(examples),
                          f"{len(examples)} vanilla example(s)", {"examples": examples})

    # ------------------------------------------------------------- validation
    def validate_code(self, snippet: str, allowed_new_ids: str = "") -> ToolResult:
        allowed = set(x.strip() for x in allowed_new_ids.split(",") if x.strip())
        res = self.ctx.validator.validate_code(snippet, allowed_new_ids=allowed)
        return ToolResult("validate_code", res["valid"],
                          "; ".join(e["message"] for e in res["errors"]) if res["errors"] else "code validates",
                          res)

    def validate_focus_tree(self) -> ToolResult:
        res = self.ctx.validator.validate_focus_tree()
        return ToolResult("validate_focus_tree", res["valid"],
                          "; ".join(e["message"] for e in res["errors"]) if res["errors"] else "focus tree valid",
                          res)

    def validate_events(self) -> ToolResult:
        res = self.ctx.validator.validate_events()
        return ToolResult("validate_events", res["valid"],
                          "; ".join(e["message"] for e in res["errors"]) if res["errors"] else "events valid", res)

    def validate_localisation(self) -> ToolResult:
        res = self.ctx.validator.validate_localisation()
        return ToolResult("validate_localisation", res["valid"],
                          "; ".join(e["message"] for e in res["errors"]) if res["errors"] else f"localisation valid ({res['keys']} keys)",
                          res)

    # ----------------------------------------------------------------- patch
    def propose_patch(self, path: str, new_content: str) -> ToolResult:
        try:
            abs_path = filesystem.resolve_write(path)
            old_text = abs_path.read_text(encoding="utf-8") if abs_path.exists() else ""
            diff = patcher.make_diff(path, old_text, new_content)
            return ToolResult("propose_patch", bool(diff.strip()), "diff generated",
                              {"path": path, "diff": diff})
        except Exception as exc:  # noqa: BLE001
            return ToolResult("propose_patch", False, str(exc))

    def apply_patch(self, path: str, diff: str) -> ToolResult:
        try:
            summary = patcher.apply_diff(diff, filesystem.workspace())
            self.ctx.memory.files_touched.extend(a["path"] for a in summary.get("applied", []))
            return ToolResult("apply_patch", True, f"{len(summary['applied'])} file(s) updated", summary)
        except Exception as exc:  # noqa: BLE001
            return ToolResult("apply_patch", False, str(exc))

    def show_diff(self, diff: str) -> ToolResult:
        return ToolResult("show_diff", True, "diff", {"diff": patcher.show_diff(diff)})

    # ---------------------------------------------------------------- lookup
    def registry(self) -> dict:
        return {
            "list_directory": {"fn": self.list_directory, "args": ["path"]},
            "search_files": {"fn": self.search_files, "args": ["query", "scope"]},
            "read_file": {"fn": self.read_file, "args": ["path", "start_line", "end_line"]},
            "search_identifier": {"fn": self.search_identifier, "args": ["name"]},
            "find_similar_identifier": {"fn": self.find_similar_identifier, "args": ["name"]},
            "inspect_focus": {"fn": self.inspect_focus, "args": ["id"]},
            "inspect_event": {"fn": self.inspect_event, "args": ["id"]},
            "inspect_decision": {"fn": self.inspect_decision, "args": ["id"]},
            "inspect_scripted_effect": {"fn": self.inspect_scripted_effect, "args": ["id"]},
            "inspect_scripted_trigger": {"fn": self.inspect_scripted_trigger, "args": ["id"]},
            "search_documentation": {"fn": self.search_documentation, "args": ["query"]},
            "search_wiki": {"fn": self.search_wiki, "args": ["query"]},
            "find_vanilla_examples": {"fn": self.find_vanilla_examples, "args": ["query"]},
            "validate_code": {"fn": self.validate_code, "args": ["snippet", "allowed_new_ids"]},
            "validate_focus_tree": {"fn": self.validate_focus_tree, "args": []},
            "validate_events": {"fn": self.validate_events, "args": []},
            "validate_localisation": {"fn": self.validate_localisation, "args": []},
            "propose_patch": {"fn": self.propose_patch, "args": ["path", "new_content"]},
            "apply_patch": {"fn": self.apply_patch, "args": ["path", "diff"]},
            "show_diff": {"fn": self.show_diff, "args": ["diff"]},
        }

    def call(self, tool_name: str, **kwargs) -> ToolResult:
        entry = self.registry().get(tool_name)
        if not entry:
            return ToolResult(tool_name, False, f"unknown tool: {tool_name}")
        result = entry["fn"](**kwargs)
        self.ctx.memory.record(tool_name, kwargs, f"{result.message} | {str(result.data)[:200]}", result.ok)
        return result
