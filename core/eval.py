"""Seeded evaluation runner for prr model swaps."""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Literal, Protocol

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from core.config import PrrConfig
from core.context import build_context, findings_for_chunk
from core.detect_static import run_static_tools
from core.filter import filter_findings
from core.ingest import chunk_file
from core.model import ModelBackendError, review
from core.schema import Finding


Severity = Literal["info", "warning", "error"]
Category = Literal["bug", "security", "style", "perf", "test", "other"]
_SEVERITY_ORDER = {"info": 0, "warning": 1, "error": 2}
_FALSE_POSITIVE_MIN_SEVERITY: Severity = "warning"


class EvalError(RuntimeError):
    """Raised when eval cases cannot be loaded or executed."""


class EvalModelError(EvalError):
    """Raised when the model backend fails while running an eval case."""


class TraversableText(Protocol):
    def joinpath(self, *descendants: str) -> "TraversableText":
        ...

    def read_text(self, encoding: str = "utf-8") -> str:
        ...


class ExpectedIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line: int = Field(ge=1)
    category: Category
    min_severity: Severity = "warning"


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    path: str
    source: str
    expected: list[ExpectedIssue] = Field(default_factory=list)

    @field_validator("id", "path", "source")
    @classmethod
    def _non_empty_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value

    @field_validator("id", "path", "source")
    @classmethod
    def _relative_safe_path(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("must be a relative path below the cases directory")
        return path.as_posix()


class EvalManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cases: list[EvalCase] = Field(min_length=1)


@dataclass(frozen=True)
class MissedIssue:
    case_id: str
    expected: ExpectedIssue


@dataclass(frozen=True)
class FalsePositive:
    case_id: str
    finding: Finding


@dataclass(frozen=True)
class CaseEvalResult:
    case: EvalCase
    findings: list[Finding]
    caught: list[ExpectedIssue]
    missed: list[MissedIssue]
    false_positives: list[FalsePositive]


@dataclass(frozen=True)
class EvalReport:
    cases: list[CaseEvalResult]

    @property
    def expected_count(self) -> int:
        return sum(len(result.case.expected) for result in self.cases)

    @property
    def caught_count(self) -> int:
        return sum(len(result.caught) for result in self.cases)

    @property
    def missed_count(self) -> int:
        return sum(len(result.missed) for result in self.cases)

    @property
    def false_positive_count(self) -> int:
        return sum(len(result.false_positives) for result in self.cases)

    @property
    def ok(self) -> bool:
        return self.missed_count == 0 and self.false_positive_count == 0


ReviewFunc = Callable[..., list[Finding]]
StaticFunc = Callable[..., list[Finding]]


def _default_cases_root() -> TraversableText:
    return files("core.eval_cases")


def _load_manifest_text(cases_path: str | Path | None) -> tuple[str, TraversableText | Path]:
    if cases_path is None:
        root = _default_cases_root()
        return root.joinpath("cases.yaml").read_text(encoding="utf-8"), root

    path = Path(cases_path)
    try:
        return path.read_text(encoding="utf-8"), path.parent
    except OSError as exc:
        raise EvalError(f"Could not read eval cases manifest {path}: {exc}") from exc


def load_eval_cases(cases_path: str | Path | None = None) -> tuple[list[EvalCase], TraversableText | Path]:
    """Load an eval case manifest and return cases plus its source root."""
    raw, root = _load_manifest_text(cases_path)
    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        raise EvalError(f"Could not parse eval cases manifest: {exc}") from exc
    try:
        manifest = EvalManifest.model_validate(data)
    except ValidationError as exc:
        raise EvalError(f"Invalid eval cases manifest: {exc}") from exc
    return manifest.cases, root


def _read_case_source(case: EvalCase, root: TraversableText | Path) -> str:
    try:
        return root.joinpath(case.source).read_text(encoding="utf-8")
    except OSError as exc:
        raise EvalError(f"Could not read eval source for {case.id}: {exc}") from exc


def _finding_matches_expected(finding: Finding, expected: ExpectedIssue) -> bool:
    return (
        finding.line == expected.line
        and finding.category == expected.category
        and _SEVERITY_ORDER[finding.severity] >= _SEVERITY_ORDER[expected.min_severity]
    )


def _compare_case(case: EvalCase, findings: list[Finding]) -> CaseEvalResult:
    unused_findings = findings.copy()
    caught: list[ExpectedIssue] = []
    missed: list[MissedIssue] = []

    for expected in case.expected:
        match_index = next(
            (
                index
                for index, finding in enumerate(unused_findings)
                if _finding_matches_expected(finding, expected)
            ),
            None,
        )
        if match_index is None:
            missed.append(MissedIssue(case.id, expected))
            continue
        caught.append(expected)
        unused_findings.pop(match_index)

    false_positives = [
        FalsePositive(case.id, finding)
        for finding in unused_findings
        if _SEVERITY_ORDER[finding.severity]
        >= _SEVERITY_ORDER[_FALSE_POSITIVE_MIN_SEVERITY]
    ]
    return CaseEvalResult(
        case=case,
        findings=findings,
        caught=caught,
        missed=missed,
        false_positives=false_positives,
    )


def _run_case(
    case: EvalCase,
    source: str,
    workspace: Path,
    config: PrrConfig,
    review_func: ReviewFunc,
    static_func: StaticFunc,
) -> CaseEvalResult:
    case_root = workspace / case.id
    target = case_root / case.path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")

    try:
        chunks = chunk_file(target)
    except (OSError, UnicodeDecodeError) as exc:
        raise EvalError(f"Could not chunk eval case {case.id}: {exc}") from exc

    static_findings = static_func([target], root=case_root)

    llm_findings: list[Finding] = []
    for chunk in chunks:
        prior = findings_for_chunk(chunk, static_findings, root=case_root)
        try:
            llm_findings.extend(
                review_func(
                    code=chunk.code,
                    path=str(target),
                    start_line=chunk.start_line,
                    context=build_context(chunk),
                    findings=prior,
                    model=config.model,
                    ollama_host=config.ollama_host,
                )
            )
        except ModelBackendError as exc:
            raise EvalModelError(f"Model backend failed while evaluating {case.id}: {exc}") from exc

    findings = filter_findings(
        [*static_findings, *llm_findings],
        config=config,
        root=case_root,
    )
    return _compare_case(case, findings)


def run_eval(
    config: PrrConfig,
    cases_path: str | Path | None = None,
    review_func: ReviewFunc | None = None,
    static_func: StaticFunc | None = None,
) -> EvalReport:
    """Run seeded cases through the normal prr pipeline."""
    if review_func is None:
        review_func = review
    if static_func is None:
        static_func = run_static_tools

    cases, root = load_eval_cases(cases_path)
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        results = [
            _run_case(
                case,
                _read_case_source(case, root),
                workspace,
                config,
                review_func,
                static_func,
            )
            for case in cases
        ]
    return EvalReport(results)
