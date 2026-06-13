"""Trust gate for all findings before they reach users or GitHub."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Iterable, Mapping

from pydantic import ValidationError

from core.config import PrrConfig
from core.schema import Finding


_SEVERITY_ORDER = {"info": 0, "warning": 1, "error": 2}
_SOURCE_ORDER = {"ruff": 0, "mypy": 1, "bandit": 2, "llm": 3}


def _resolve_path(path: str, root: Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return root / candidate


def _display_path(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _line_count(path: Path, cache: dict[Path, int]) -> int | None:
    resolved = path.resolve()
    if resolved in cache:
        return cache[resolved]
    try:
        count = len(path.read_text(encoding="utf-8").splitlines())
    except (OSError, UnicodeDecodeError):
        return None
    cache[resolved] = count
    return count


def _passes_thresholds(finding: Finding, config: PrrConfig) -> bool:
    if _SEVERITY_ORDER[finding.severity] < _SEVERITY_ORDER[config.severity_threshold]:
        return False
    if finding.source == "llm" and finding.confidence < config.min_confidence:
        return False
    return True


def _validated_finding(finding: Finding, root: Path, line_cache: dict[Path, int]) -> Finding | None:
    path = _resolve_path(finding.path, root)
    count = _line_count(path, line_cache)
    if count is None:
        return None
    if finding.line > count:
        return None
    if finding.end_line is not None and finding.end_line > count:
        return None

    try:
        return finding.model_copy(update={"path": _display_path(path, root)})
    except ValidationError:
        return None


def _sort_key(finding: Finding) -> tuple[int, int, int, int, float]:
    return (
        -_SEVERITY_ORDER[finding.severity],
        finding.line,
        finding.end_line or finding.line,
        _SOURCE_ORDER[finding.source],
        -finding.confidence,
    )


def _joined_comments(findings: list[Finding]) -> str:
    comments: list[str] = []
    for finding in findings:
        if finding.comment not in comments:
            comments.append(finding.comment)
    return " ".join(comments)


def _merge_duplicate_group(findings: list[Finding]) -> Finding:
    if len(findings) == 1:
        return findings[0]

    static = sorted((f for f in findings if f.source != "llm"), key=_sort_key)
    llm = sorted((f for f in findings if f.source == "llm"), key=_sort_key)
    severity = max((f.severity for f in findings), key=lambda s: _SEVERITY_ORDER[s])

    if static and llm:
        tool_finding = static[0]
        llm_finding = llm[0]
        return tool_finding.model_copy(update={
            "severity": severity,
            "comment": _joined_comments([*static, llm_finding]),
            "suggestion": llm_finding.suggestion or tool_finding.suggestion,
            "confidence": max(f.confidence for f in findings),
        })

    if static:
        # Distinct tool diagnostics on one line are all real; keep every comment.
        return static[0].model_copy(update={
            "severity": severity,
            "comment": _joined_comments(static),
            "suggestion": next((f.suggestion for f in static if f.suggestion), None),
            "confidence": max(f.confidence for f in findings),
        })

    return llm[0].model_copy(update={
        "severity": severity,
        "confidence": max(f.confidence for f in findings),
    })


def _restrict_to_allowed_lines(
    finding: Finding,
    allowed_lines: Mapping[str, set[int]],
) -> Finding | None:
    """Keep only findings that are allowed to be emitted in PR mode.

    In this repo we currently restrict PR comments to *added* lines (not unchanged
    context lines). If a range extends beyond the allowed set, fall back to a
    single-line anchor and drop any suggestion.
    """
    allowed = allowed_lines.get(finding.path)
    if allowed is None or finding.line not in allowed:
        return None
    if finding.end_line is not None:
        if not all(line in allowed for line in range(finding.line, finding.end_line + 1)):
            return finding.model_copy(update={"end_line": None, "suggestion": None})
    return finding


def filter_findings(
    findings: Iterable[Finding],
    config: PrrConfig,
    root: str | Path = ".",
    allowed_lines: Mapping[str, set[int]] | None = None,
) -> list[Finding]:
    """Validate, deduplicate, threshold, cap, and sort findings.

    When *allowed_lines* is given (PR mode), it maps root-relative posix paths
    to the set of diff-commentable line numbers; findings outside it are dropped.
    """
    root_path = Path(root)
    line_cache: dict[Path, int] = {}
    validated: list[Finding] = []

    for finding in findings:
        if not _passes_thresholds(finding, config):
            continue
        checked = _validated_finding(finding, root_path, line_cache)
        if checked is not None and allowed_lines is not None:
            checked = _restrict_to_allowed_lines(checked, allowed_lines)
        if checked is not None:
            validated.append(checked)

    by_location: dict[tuple[str, int], list[Finding]] = defaultdict(list)
    for finding in validated:
        by_location[(finding.path, finding.line)].append(finding)

    deduped = [_merge_duplicate_group(group) for group in by_location.values()]
    deduped.sort(key=_sort_key)

    by_file: dict[str, list[Finding]] = defaultdict(list)
    for finding in deduped:
        by_file[finding.path].append(finding)

    capped: list[Finding] = []
    for path in sorted(by_file):
        capped.extend(by_file[path][: config.max_comments_per_file])

    capped.sort(key=lambda f: (f.path, *_sort_key(f)))
    return capped
