"""Context assembly for model review chunks."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from core.ingest import Chunk
from core.schema import Finding


def _same_path(left: str, right: str) -> bool:
    if left == right:
        return True
    left_path = Path(left)
    right_path = Path(right)
    if not left_path.is_absolute() and right_path.match(left_path.as_posix()):
        return True
    if not right_path.is_absolute() and left_path.match(right_path.as_posix()):
        return True
    try:
        return left_path.resolve() == right_path.resolve()
    except OSError:
        return left_path.name == right_path.name


def findings_for_chunk(chunk: Chunk, findings: Iterable[Finding]) -> list[Finding]:
    """Return findings from the same file whose primary line lands in the chunk."""
    return [
        finding
        for finding in findings
        if _same_path(finding.path, chunk.path)
        and chunk.start_line <= finding.line <= chunk.end_line
    ]


def build_context(chunk: Chunk, prior_findings: Iterable[Finding]) -> str:
    """Build free-text context passed alongside the chunk code."""
    parts: list[str] = []
    if chunk.context:
        parts.append(chunk.context)

    local_findings = findings_for_chunk(chunk, prior_findings)
    if local_findings:
        rendered = [
            f"- line {finding.line} [{finding.source}/{finding.severity}]: {finding.comment}"
            for finding in local_findings
        ]
        parts.append("Static findings in this chunk:\n" + "\n".join(rendered))

    return "\n\n".join(parts)
