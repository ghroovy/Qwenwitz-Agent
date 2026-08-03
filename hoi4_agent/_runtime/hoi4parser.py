# Owner: STABLE
"""Minimal HOI4/Clausewitz script tokenizer, tree parser, and doc parsers.

The real game uses the Clausewitz engine; this is a pragmatic reader that
handles the constructs that matter for dataset work: comments, quoted
strings, key = value pairs, and nested blocks. It is intentionally tolerant
of odd-but-legal game files.
"""

from __future__ import annotations

import re
from typing import Any


def tokenize(text: str) -> list[dict[str, Any]]:
    """Return tokens: {'type': 'ident'|'str'|'lbrace'|'rbrace', 'text', 'start', 'end'}."""
    tokens: list[dict[str, Any]] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in " \t\r\n":
            i += 1
            continue
        if ch == "#":
            while i < n and text[i] not in "\r\n":
                i += 1
            continue
        if ch == "{":
            tokens.append({"type": "lbrace", "text": "{", "start": i, "end": i + 1})
            i += 1
            continue
        if ch == "}":
            tokens.append({"type": "rbrace", "text": "}", "start": i, "end": i + 1})
            i += 1
            continue
        if ch == '"':
            start = i
            i += 1
            buf: list[str] = []
            while i < n:
                if text[i] == "\\" and i + 1 < n:
                    buf.append(text[i : i + 2])
                    i += 2
                    continue
                if text[i] == '"':
                    i += 1
                    break
                buf.append(text[i])
                i += 1
            tokens.append({"type": "str", "text": "".join(buf), "start": start, "end": i})
            continue
        start = i
        while i < n and text[i] not in " \t\r\n{}#\"":
            i += 1
        tokens.append({"type": "ident", "text": text[start:i], "start": start, "end": i})
    return tokens


def _parse_nodes(tokens: list[dict[str, Any]], start: int = 0) -> tuple[list[dict[str, Any]], int]:
    nodes: list[dict[str, Any]] = []
    i = start
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if tok["type"] == "rbrace":
            return nodes, i + 1
        if tok["type"] == "lbrace":
            children, i = _parse_nodes(tokens, i + 1)
            nodes.append(
                {
                    "kind": "block",
                    "key": None,
                    "children": children,
                    "start": tok["start"],
                    "end": tokens[i - 1]["end"] if i > 0 else tok["end"],
                }
            )
            continue
        if tok["type"] == "ident" and i + 2 < n and tokens[i + 1]["type"] == "ident" and tokens[i + 1]["text"] == "=":
            key = tok["text"]
            val = tokens[i + 2]
            if val["type"] == "lbrace":
                children, after = _parse_nodes(tokens, i + 3)
                nodes.append(
                    {
                        "kind": "block",
                        "key": key,
                        "children": children,
                        "start": tok["start"],
                        "end": tokens[after - 1]["end"] if after > 0 else val["end"],
                    }
                )
                i = after
                continue
            nodes.append(
                {
                    "kind": "kv",
                    "key": key,
                    "value": val["text"],
                    "start": tok["start"],
                    "end": val["end"],
                }
            )
            i += 3
            continue
        nodes.append(
            {
                "kind": "bare",
                "key": tok["text"],
                "start": tok["start"],
                "end": tok["end"],
            }
        )
        i += 1
    return nodes, i


def parse_tree(text: str) -> list[dict[str, Any]]:
    return _parse_nodes(tokenize(text))[0]


def walk(nodes: list[dict[str, Any]], key: str | None = None) -> list[dict[str, Any]]:
    """Depth-first walk; return all nodes whose key matches (if given)."""
    out: list[dict[str, Any]] = []
    for node in nodes:
        if node["kind"] in ("block", "kv") and (key is None or node["key"] == key):
            out.append(node)
        if node["kind"] == "block":
            out.extend(walk(node["children"], key))
    return out


def block_children_by_key(node: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Group immediate block children by their key."""
    out: dict[str, list[dict[str, Any]]] = {}
    for child in node.get("children", []):
        if child["kind"] in ("block", "kv") and child["key"] is not None:
            out.setdefault(child["key"], []).append(child)
    return out


def node_raw(text: str, node: dict[str, Any]) -> str:
    return text[node["start"] : node["end"]]


def first_kv(nodes: list[dict[str, Any]], key: str) -> str | None:
    for node in walk(nodes, key):
        if node["kind"] == "kv":
            return node["value"]
    return None


def find_kv(nodes: list[dict[str, Any]], key: str) -> list[str]:
    return [node["value"] for node in walk(nodes, key) if node["kind"] == "kv"]


# ---------------------------------------------------------------------------
# Official auto-generated documentation parsers
# ---------------------------------------------------------------------------


def parse_effect_trigger_docs(text: str) -> dict[str, dict[str, Any]]:
    """Parse effects_documentation.md / triggers_documentation.md entries."""
    entries: dict[str, dict[str, Any]] = {}
    lines = text.splitlines()
    cur: str | None = None
    in_code = False
    code_lines: list[str] = []
    for line in lines:
        m = re.match(r"^## ([a-z_][a-z0-9_]*)$", line.strip())
        if m:
            if cur is not None:
                entries[cur]["description"] = " ".join(entries[cur].get("description_raw", [])).strip()
                entries[cur]["example"] = "\n".join(entries[cur].get("example_lines", [])).strip()
            cur = m.group(1)
            entries[cur] = {"scopes": [], "targets": [], "description_raw": [], "example_lines": []}
            in_code = False
            continue
        if cur is None:
            continue
        if line.strip().startswith("```"):
            in_code = not in_code
            if not in_code:
                code = "\n".join(code_lines).strip()
                if entries[cur]["example_lines"]:
                    entries[cur]["example_lines"].append(code)
                else:
                    # first fence is the human description, later fences examples
                    if not entries[cur]["description_raw"]:
                        entries[cur]["description_raw"] = code.splitlines()
                    else:
                        entries[cur]["example_lines"].append(code)
                code_lines = []
            continue
        if in_code:
            code_lines.append(line)
            continue
        sm = re.match(r"^\*\s*Supported Scopes:\s*(.+)$", line.strip())
        if sm:
            entries[cur]["scopes"] = re.split(r"[,\s]+", sm.group(1).strip())
            continue
        tm = re.match(r"^\*\s*Supported Targets:\s*(.+)$", line.strip())
        if tm:
            entries[cur]["targets"] = re.split(r"[,\s]+", tm.group(1).strip())
            continue
        dm = re.match(r"^\*\s*description:\s*(.+)$", line.strip(), re.IGNORECASE)
        if dm:
            entries[cur]["description_raw"].append(dm.group(1).strip())
            continue
        dl = re.match(r"^\*\s*description", line.strip(), re.IGNORECASE)
        if dl:
            rest = line.strip()[len(dl.group(0)) :].strip(" :")
            if rest:
                entries[cur]["description_raw"].append(rest)
    if cur is not None:
        entries[cur]["description"] = " ".join(entries[cur].get("description_raw", [])).strip()
        entries[cur]["example"] = "\n".join(entries[cur].get("example_lines", [])).strip()
    for e in entries.values():
        e.pop("description_raw", None)
        e.pop("example_lines", None)
    return entries


def parse_modifiers_docs(text: str) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    lines = text.splitlines()
    cur: str | None = None
    for line in lines:
        m = re.match(r"^##\s+(.+)$", line.strip())
        if m:
            key = m.group(1).strip()
            key = re.sub(r"<span[^>]*>.*?</span>", "", key)
            key = re.sub(r"<[^>]+>", "", key).strip()
            if not key or key in ("Table of Content",):
                continue
            cur = key
            entries[cur] = {"description": "", "categories": [], "format": ""}
            continue
        if cur is None:
            continue
        dm = re.match(r"^\*\*\*?\s*Description\*\*?\s*:\s*(.+)$", line.strip(), re.IGNORECASE)
        if dm:
            entries[cur]["description"] = dm.group(1).strip()
            continue
        cm = re.match(r"^\*\*\*?\s*Categories\*\*?\s*:\s*(.+)$", line.strip(), re.IGNORECASE)
        if cm:
            entries[cur]["categories"] = [c.strip() for c in cm.group(1).split(",")]
            continue
        fm = re.match(r"^\*\s*(Number|Yes/No|Boolean|Text|Country|State|.* decimals).*$", line.strip())
        if fm and not entries[cur]["format"]:
            entries[cur]["format"] = line.strip().lstrip("* ").strip()
    return entries


def parse_dynamic_variables_docs(text: str) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    lines = text.splitlines()
    cur: str | None = None
    for line in lines:
        m = re.match(r"^### ([a-zA-Z_][a-zA-Z0-9_@\.]*)$", line.strip())
        if m:
            cur = m.group(1)
            entries[cur] = {"description": "", "scope": ""}
            continue
        if cur is None:
            continue
        sm = re.match(r"^## Dynamic variables for scope (.+)$", line.strip())
        if sm:
            entries[cur]["scope"] = sm.group(1).strip()
            continue
        dm = re.match(r"^\*\s*description:\s*(.+)$", line.strip(), re.IGNORECASE)
        if dm and not entries[cur]["description"]:
            entries[cur]["description"] = dm.group(1).strip()
    return entries

