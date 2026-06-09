"""Tree-sitter chunking for a single Python file.

Produces Chunk objects with 1-based, inclusive start/end line numbers.
Diff parsing (PR mode) comes in Week 3.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import tree_sitter_python as tspython
from tree_sitter import Language, Node, Parser


# Initialise parser once at import time
_PY_LANGUAGE = Language(tspython.language())
_PARSER = Parser(_PY_LANGUAGE)


@dataclass
class Chunk:
    path: str
    name: str                              # function/class name, or "<module>"
    kind: Literal["function", "class", "method", "module"]
    start_line: int                        # 1-based, inclusive
    end_line: int                          # 1-based, inclusive
    code: str                              # source text of this chunk
    context: str = ""                      # structural context outside the chunk


@dataclass
class _ReviewTarget:
    node: Node
    kind: Literal["function", "class", "method"]
    name: str
    context: str = ""
    coverage_node: Node | None = None


def _unwrap_definition(node: Node) -> Node:
    """Return the function/class node wrapped by decorators, if any."""
    if node.type == "decorated_definition":
        for child in node.children:
            if child.type in ("function_definition", "class_definition"):
                return child
    return node


def _definition_name(node: Node) -> str:
    inner = _unwrap_definition(node)
    for child in inner.children:
        if child.type == "identifier":
            return child.text.decode()
    return "<module>"


def _class_method_nodes(class_node: Node) -> list[Node]:
    """Return direct function definitions in a class body."""
    inner = _unwrap_definition(class_node)
    if inner.type != "class_definition":
        return []

    methods: list[Node] = []
    for child in inner.children:
        if child.type != "block":
            continue
        for member in child.children:
            member_inner = _unwrap_definition(member)
            if member_inner.type == "function_definition":
                methods.append(member)
    return methods


def _source_for_node(node: Node, source_lines: list[str]) -> str:
    start = node.start_point[0]
    end = node.end_point[0]
    return "\n".join(source_lines[start : end + 1])


def _top_level_import_context(root: Node, source_lines: list[str]) -> str:
    imports: list[str] = []
    for node in root.children:
        if node.type in ("import_statement", "import_from_statement"):
            imports.append(_source_for_node(node, source_lines))
    if not imports:
        return ""
    return "Top-level imports:\n" + "\n".join(imports)


def _class_context(class_node: Node, source_lines: list[str]) -> str:
    inner = _unwrap_definition(class_node)
    if inner.type != "class_definition":
        return ""

    context_lines = [source_lines[inner.start_point[0]]]
    for child in inner.children:
        if child.type != "block":
            continue
        for member in child.children:
            member_inner = _unwrap_definition(member)
            if member_inner.type in ("function_definition", "class_definition"):
                continue
            text = _source_for_node(member, source_lines)
            if text.strip():
                context_lines.append(text)

    return "Enclosing class context:\n" + "\n".join(context_lines)


def _join_context(*parts: str) -> str:
    return "\n\n".join(part for part in parts if part)


def _top_level_review_nodes(root: Node, source_lines: list[str]) -> list[_ReviewTarget]:
    """Collect definitions worth sending to the model as primary chunks."""
    review_nodes: list[_ReviewTarget] = []
    import_context = _top_level_import_context(root, source_lines)
    for node in root.children:
        inner = _unwrap_definition(node)
        if inner.type == "function_definition":
            review_nodes.append(_ReviewTarget(
                node=node,
                kind="function",
                name=_definition_name(node),
                context=import_context,
            ))
        elif inner.type == "class_definition":
            methods = _class_method_nodes(node)
            if methods:
                class_name = _definition_name(node)
                class_context = _join_context(import_context, _class_context(node, source_lines))
                review_nodes.extend(
                    _ReviewTarget(
                        node=method,
                        kind="method",
                        name=f"{class_name}.{_definition_name(method)}",
                        context=class_context,
                        coverage_node=node,
                    )
                    for method in methods
                )
            else:
                review_nodes.append(_ReviewTarget(
                    node=node,
                    kind="class",
                    name=_definition_name(node),
                    context=import_context,
                ))
    return review_nodes


def _node_to_chunk(
    node: Node,
    source_lines: list[str],
    path: str,
    kind: Literal["function", "class", "method"],
    name: str,
    context: str,
) -> Chunk:
    """Convert a tree-sitter node to a Chunk."""
    start = node.start_point[0]  # 0-based
    end = node.end_point[0]      # 0-based

    code = "\n".join(source_lines[start : end + 1])
    return Chunk(
        path=path,
        name=name,
        kind=kind,
        start_line=start + 1,
        end_line=end + 1,
        code=code,
        context=context,
    )


def chunk_file(path: str | Path) -> list[Chunk]:
    """Parse *path* with tree-sitter and return a list of Chunks.

    Top-level function/class definitions → one Chunk each (with their
    decorators if any).  Lines not covered by a named definition → one
    synthetic 'module' Chunk so top-level code is not skipped.
    If the file has no definitions at all, the entire file is one module Chunk.
    """
    path = Path(path)
    source = path.read_text(encoding="utf-8")
    source_lines = source.splitlines()
    total_lines = len(source_lines)

    if total_lines == 0:
        return []

    tree = _PARSER.parse(source.encode())

    if tree.root_node.has_error:
        return [Chunk(
            path=str(path),
            name="<module>",
            kind="module",
            start_line=1,
            end_line=total_lines,
            code=source,
            context="Tree-sitter reported syntax errors; reviewing as module.",
        )]

    # Collect primary review nodes. Methods are split out of classes with their
    # enclosing class header/attributes supplied as context.
    review_nodes = _top_level_review_nodes(tree.root_node, source_lines)

    if not review_nodes:
        # Whole file as a single module chunk
        return [Chunk(
            path=str(path),
            name="<module>",
            kind="module",
            start_line=1,
            end_line=total_lines,
            code=source,
        )]

    chunks: list[Chunk] = []

    # Track which lines are covered by named defs
    covered: set[int] = set()
    for target in review_nodes:
        coverage = target.coverage_node or target.node
        s, e = coverage.start_point[0], coverage.end_point[0]  # 0-based
        covered.update(range(s, e + 1))
        chunks.append(_node_to_chunk(
            target.node,
            source_lines,
            str(path),
            target.kind,
            target.name,
            target.context,
        ))

    # Collect uncovered lines → synthetic module chunk(s)
    uncovered = [i for i in range(total_lines) if i not in covered]
    if uncovered:
        # Group consecutive uncovered lines into contiguous ranges
        groups: list[list[int]] = []
        current: list[int] = [uncovered[0]]
        for ln in uncovered[1:]:
            if ln == current[-1] + 1:
                current.append(ln)
            else:
                groups.append(current)
                current = [ln]
        groups.append(current)

        for group in groups:
            code = "\n".join(source_lines[ln] for ln in group)
            if code.strip():  # skip blank-only gaps
                chunks.append(Chunk(
                    path=str(path),
                    name="<module>",
                    kind="module",
                    start_line=group[0] + 1,
                    end_line=group[-1] + 1,
                    code=code,
                ))

    # Return in source order
    chunks.sort(key=lambda c: c.start_line)
    return chunks
