"""Static analysis runners normalized to the Finding schema."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal

from pydantic import ValidationError

from core.schema import Finding

logger = logging.getLogger(__name__)


ToolName = Literal["ruff", "mypy", "bandit"]
_STATIC_TOOL_TIMEOUT_SECONDS = 60

_MYPY_LINE = re.compile(
    r"^(?P<path>.*?):(?P<line>\d+)(?::(?P<column>\d+))?: "
    r"(?P<level>error|note|warning): (?P<message>.*)$"
)

_RUFF_FAMILY = re.compile(r"^([A-Z]+)")


def _ruff_family(code: str) -> str:
    """Letter prefix of a ruff rule code, e.g. 'SIM101' -> 'SIM', 'S608' -> 'S'."""
    match = _RUFF_FAMILY.match(code)
    return match.group(1) if match else ""


def _relative_path(path: str, root: Path | None) -> str:
    candidate = Path(path)
    if not candidate.is_absolute():
        return str(candidate)
    if root is None:
        return str(candidate)
    try:
        return str(candidate.resolve().relative_to(root.resolve()))
    except (OSError, ValueError):
        return str(candidate)


def _path_args(paths: Iterable[Path], root: Path) -> list[str]:
    args: list[str] = []
    for path in paths:
        try:
            args.append(str(path.resolve().relative_to(root.resolve())))
        except ValueError:
            args.append(str(path))
    return args


def _finding(**data: object) -> Finding | None:
    try:
        return Finding.model_validate(data)
    except ValidationError as exc:
        logger.debug("Dropped invalid static finding: %s", exc)
        return None


def _ruff_category(code: str) -> Literal["bug", "security", "style", "perf", "test", "other"]:
    family = _ruff_family(code)
    if family == "S":
        return "security"
    if family == "PERF":
        return "perf"
    if family == "PT":
        return "test"
    if family == "B" or code.startswith(("F821", "F823", "E9")):
        return "bug"
    if code:
        return "style"
    return "other"


def _ruff_severity(code: str) -> Literal["info", "warning", "error"]:
    if _ruff_family(code) == "S" or code.startswith(("F821", "F823", "E9")):
        return "error"
    return "warning"


def parse_ruff_json(raw: str, root: Path | None = None) -> list[Finding]:
    """Parse `ruff check --output-format json` output."""
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError as exc:
        logger.debug("Could not parse ruff JSON: %s", exc)
        return []

    if not isinstance(data, list):
        return []

    findings: list[Finding] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        location = item.get("location") or {}
        end_location = item.get("end_location") or {}
        code = str(item.get("code") or "")
        line = location.get("row")
        if not isinstance(line, int):
            continue
        end_line = end_location.get("row")
        if not isinstance(end_line, int) or end_line <= line:
            end_line = None

        finding = _finding(
            path=_relative_path(str(item.get("filename") or ""), root),
            line=line,
            end_line=end_line,
            severity=_ruff_severity(code),
            category=_ruff_category(code),
            comment=f"{code}: {item.get('message')}" if code else str(item.get("message") or ""),
            source="ruff",
        )
        if finding is not None:
            findings.append(finding)
    return findings


def parse_mypy_text(raw: str, root: Path | None = None) -> list[Finding]:
    """Parse mypy's standard text output."""
    findings: list[Finding] = []
    for line_text in raw.splitlines():
        match = _MYPY_LINE.match(line_text)
        if match is None:
            continue
        level = match.group("level")
        message = match.group("message")
        finding = _finding(
            path=_relative_path(match.group("path"), root),
            line=int(match.group("line")),
            severity="info" if level == "note" else ("warning" if level == "warning" else "error"),
            category="bug",
            comment=f"mypy {level}: {message}",
            source="mypy",
        )
        if finding is not None:
            findings.append(finding)
    return findings


def _bandit_severity(severity: str) -> Literal["info", "warning", "error"]:
    match severity.upper():
        case "HIGH":
            return "error"
        case "MEDIUM":
            return "warning"
        case _:
            return "info"


def _bandit_confidence(confidence: str) -> float:
    match confidence.upper():
        case "HIGH":
            return 0.9
        case "MEDIUM":
            return 0.7
        case _:
            return 0.4


def parse_bandit_json(raw: str, root: Path | None = None) -> list[Finding]:
    """Parse `bandit -f json` output."""
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        logger.debug("Could not parse bandit JSON: %s", exc)
        return []

    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        return []

    findings: list[Finding] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        line = item.get("line_number")
        if not isinstance(line, int):
            continue
        confidence = str(item.get("issue_confidence") or "")
        finding = _finding(
            path=_relative_path(str(item.get("filename") or ""), root),
            line=line,
            severity=_bandit_severity(str(item.get("issue_severity") or "")),
            category="security",
            comment=f"{item.get('test_id')}: {item.get('issue_text')}",
            source="bandit",
            confidence=_bandit_confidence(confidence),
        )
        if finding is not None:
            findings.append(finding)
    return findings


def _resolve_executable(name: str) -> str | None:
    """Resolve a static tool on PATH or in the active environment's bin directory."""
    found = shutil.which(name)
    if found is not None:
        return found
    for directory in (Path(sys.executable).parent, Path(sys.prefix) / "bin"):
        candidate = directory / name
        if candidate.is_file():
            return str(candidate)
    return None


def _run_command(
    args: list[str],
    root: Path,
) -> tuple[subprocess.CompletedProcess[str] | None, str | None]:
    executable = args[0]
    resolved = _resolve_executable(executable)
    if resolved is None:
        logger.info("Static tool not found: %s", executable)
        return None, (
            f"{executable} not found on PATH or next to the current Python interpreter"
        )
    if resolved != executable:
        args = [resolved, *args[1:]]
    try:
        return subprocess.run(
            args,
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
            timeout=_STATIC_TOOL_TIMEOUT_SECONDS,
        ), None
    except subprocess.TimeoutExpired:
        logger.info(
            "Static tool timed out after %d seconds: %s",
            _STATIC_TOOL_TIMEOUT_SECONDS,
            executable,
        )
        return None, (
            f"{executable} timed out after {_STATIC_TOOL_TIMEOUT_SECONDS} seconds"
        )
    except OSError as exc:
        logger.info("Could not run static tool %s: %s", executable, exc)
        return None, f"could not run {executable}: {exc}"


@dataclass(frozen=True)
class StaticToolsResult:
    findings: list[Finding]
    warnings: tuple[str, ...] = ()


def run_ruff(
    paths: Iterable[Path],
    root: Path,
) -> tuple[list[Finding], str | None]:
    args = ["ruff", "check", "--output-format", "json", *_path_args(paths, root)]
    result, warning = _run_command(args, root)
    if result is None:
        return [], warning
    return parse_ruff_json(result.stdout, root=root), None


def run_mypy(
    paths: Iterable[Path],
    root: Path,
) -> tuple[list[Finding], str | None]:
    args = [
        "mypy",
        "--hide-error-context",
        "--no-error-summary",
        "--show-error-codes",
        *_path_args(paths, root),
    ]
    result, warning = _run_command(args, root)
    if result is None:
        return [], warning
    return parse_mypy_text(result.stdout, root=root), None


def run_bandit(
    paths: Iterable[Path],
    root: Path,
) -> tuple[list[Finding], str | None]:
    args = ["bandit", "-f", "json", "--quiet", *_path_args(paths, root)]
    result, warning = _run_command(args, root)
    if result is None:
        return [], warning
    raw = result.stdout or result.stderr
    return parse_bandit_json(raw, root=root), None


def run_static_tools(
    paths: Iterable[str | Path],
    root: str | Path = ".",
    tools: Iterable[ToolName] = ("ruff", "mypy", "bandit"),
) -> StaticToolsResult:
    """Run configured static tools and return normalized findings plus skip warnings."""
    root_path = Path(root)
    path_list = [Path(path) for path in paths]
    if not path_list:
        return StaticToolsResult(findings=[])

    findings: list[Finding] = []
    warnings: list[str] = []
    requested = set(tools)
    if "ruff" in requested:
        tool_findings, warning = run_ruff(path_list, root_path)
        findings.extend(tool_findings)
        if warning is not None:
            warnings.append(warning)
    if "mypy" in requested:
        tool_findings, warning = run_mypy(path_list, root_path)
        findings.extend(tool_findings)
        if warning is not None:
            warnings.append(warning)
    if "bandit" in requested:
        tool_findings, warning = run_bandit(path_list, root_path)
        findings.extend(tool_findings)
        if warning is not None:
            warnings.append(warning)
    return StaticToolsResult(findings=findings, warnings=tuple(warnings))
