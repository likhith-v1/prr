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


ToolName = Literal["ruff", "mypy", "bandit", "eslint"]
_STATIC_TOOL_TIMEOUT_SECONDS = 60
_JS_SUFFIXES = frozenset({".js", ".jsx", ".ts", ".tsx"})

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


def _is_js_path(path: Path) -> bool:
    return path.suffix.lower() in _JS_SUFFIXES


def _is_python_path(path: Path) -> bool:
    return path.suffix == ".py"


def is_eslint_available() -> bool:
    return _resolve_executable("eslint") is not None


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


def _eslint_severity(level: int) -> Literal["info", "warning", "error"]:
    if level >= 2:
        return "error"
    if level == 1:
        return "warning"
    return "info"


def _eslint_category(rule_id: str | None) -> Literal["bug", "security", "style", "perf", "test", "other"]:
    if not rule_id:
        return "other"
    lowered = rule_id.lower()
    if lowered.startswith("security/") or "security" in lowered or "injection" in lowered:
        return "security"
    if lowered.startswith(("jest/", "vitest/", "testing-library/")):
        return "test"
    if "perf" in lowered:
        return "perf"
    if lowered.startswith(("no-undef", "no-unreachable", "@typescript-eslint/no-floating-promises")):
        return "bug"
    if lowered.startswith(("no-", "@typescript-eslint/no-")):
        return "bug"
    return "style"


def parse_eslint_json(raw: str, root: Path | None = None) -> list[Finding]:
    """Parse `eslint --format json` output."""
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError as exc:
        logger.debug("Could not parse eslint JSON: %s", exc)
        return []

    if not isinstance(data, list):
        return []

    findings: list[Finding] = []
    for file_result in data:
        if not isinstance(file_result, dict):
            continue
        messages = file_result.get("messages")
        if not isinstance(messages, list):
            continue
        file_path = str(file_result.get("filePath") or "")
        for message in messages:
            if not isinstance(message, dict):
                continue
            line = message.get("line")
            if not isinstance(line, int):
                continue
            end_line = message.get("endLine")
            if not isinstance(end_line, int) or end_line <= line:
                end_line = None
            rule_id = message.get("ruleId")
            rule_text = str(rule_id) if rule_id else "eslint"
            severity_level = message.get("severity")
            severity = _eslint_severity(severity_level if isinstance(severity_level, int) else 1)
            finding = _finding(
                path=_relative_path(file_path, root),
                line=line,
                end_line=end_line,
                severity=severity,
                category=_eslint_category(str(rule_id) if rule_id else None),
                comment=f"{rule_text}: {message.get('message') or ''}".rstrip(": "),
                source="eslint",
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


def run_eslint(
    paths: Iterable[Path],
    root: Path,
) -> tuple[list[Finding], str | None]:
    args = ["eslint", "--format", "json", *_path_args(paths, root)]
    result, warning = _run_command(args, root)
    if result is None:
        return [], warning
    raw = result.stdout or result.stderr
    return parse_eslint_json(raw, root=root), None


def run_static_tools(
    paths: Iterable[str | Path],
    root: str | Path = ".",
    tools: Iterable[ToolName] = ("ruff", "mypy", "bandit", "eslint"),
) -> StaticToolsResult:
    """Run configured static tools and return normalized findings plus skip warnings."""
    root_path = Path(root)
    path_list = [Path(path) for path in paths]
    if not path_list:
        return StaticToolsResult(findings=[])

    python_paths = [path for path in path_list if _is_python_path(path)]
    js_paths = [path for path in path_list if _is_js_path(path)]

    findings: list[Finding] = []
    warnings: list[str] = []
    requested = set(tools)
    if "ruff" in requested and python_paths:
        tool_findings, warning = run_ruff(python_paths, root_path)
        findings.extend(tool_findings)
        if warning is not None:
            warnings.append(warning)
    if "mypy" in requested and python_paths:
        tool_findings, warning = run_mypy(python_paths, root_path)
        findings.extend(tool_findings)
        if warning is not None:
            warnings.append(warning)
    if "bandit" in requested and python_paths:
        tool_findings, warning = run_bandit(python_paths, root_path)
        findings.extend(tool_findings)
        if warning is not None:
            warnings.append(warning)
    if "eslint" in requested and js_paths:
        tool_findings, warning = run_eslint(js_paths, root_path)
        findings.extend(tool_findings)
        if warning is not None:
            warnings.append(warning)
    return StaticToolsResult(findings=findings, warnings=tuple(warnings))
