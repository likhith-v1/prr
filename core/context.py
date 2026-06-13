"""Context assembly for model review chunks."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from core.ingest import Chunk
from core.schema import Finding


def _resolve(path_text: str, root: Path) -> Path:
    candidate = Path(path_text)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        return candidate.resolve()
    except OSError:
        return candidate


def _same_path(left: str, right: str, root: Path) -> bool:
    return _resolve(left, root) == _resolve(right, root)


def findings_for_chunk(
    chunk: Chunk,
    findings: Iterable[Finding],
    root: str | Path | None = None,
) -> list[Finding]:
    """Return findings from the same file whose primary line lands in the chunk.

    Relative paths on either side are resolved against *root* (default: cwd),
    so a root-relative tool path and an absolute chunk path compare equal only
    when they point at the same file.
    """
    root_path = Path(root) if root is not None else Path.cwd()
    return [
        finding
        for finding in findings
        if _same_path(finding.path, chunk.path, root_path)
        and chunk.start_line <= finding.line <= chunk.end_line
    ]


def build_context(chunk: Chunk) -> str:
    """Build free-text context passed alongside the chunk code.

    Prior findings are passed to the model separately (structured) via
    ``core.model.review(findings=...)``; they are deliberately not rendered
    here to avoid duplicating them in the prompt.
    """
    return chunk.context
