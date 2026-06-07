"""The model seam — the single swap point for the review brain.

Swap the backend (or MODEL constant) and callers are unaffected.

Week 1: Ollama backend, qwen2.5-coder:14b.
Future:  replace OllamaBackend with VllmBackend (same protocol) or change MODEL.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal, Protocol

import ollama
from pydantic import BaseModel, ValidationError

from core.schema import Finding

logger = logging.getLogger(__name__)

# ── one-line swap point ────────────────────────────────────────────────────────
MODEL = "qwen2.5-coder:14b"
# ──────────────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "review.txt").read_text(encoding="utf-8")


# ── backend protocol (the seam interface) ─────────────────────────────────────

class Backend(Protocol):
    def generate(self, system: str, user: str) -> str:
        """Call the model; return the raw text response."""
        ...


class ModelBackendError(RuntimeError):
    """Raised when the configured model backend cannot produce a response."""


class OllamaBackend:
    """Ollama backend using the ollama Python client."""

    def __init__(self, model: str, format_schema: dict[str, Any] | None = None) -> None:
        self.model = model
        self.format_schema = format_schema

    def generate(self, system: str, user: str) -> str:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if self.format_schema is not None:
            kwargs["format"] = self.format_schema
        try:
            response = ollama.chat(**kwargs)
        except (ConnectionError, ollama.RequestError, ollama.ResponseError) as exc:
            raise ModelBackendError(str(exc)) from exc
        return response["message"]["content"]


# ── transient boundary models (not Finding — snippet is dropped after relocation) ──

class _LLMFinding(BaseModel):
    line: int
    severity: Literal["info", "warning", "error"]
    category: Literal["bug", "security", "style", "perf", "test", "other"]
    comment: str
    snippet: str                  # exact quoted offending line (used to re-anchor line number)
    suggestion: str | None = None
    confidence: float = 1.0


class _LLMResponse(BaseModel):
    findings: list[_LLMFinding]


def _build_user_prompt(
    code: str,
    path: str,
    start_line: int,
    context: str,
    prior_findings: list[Finding],
) -> str:
    """Build the user turn: absolute-line-prefixed code + optional context."""
    lines = code.splitlines()
    numbered = "\n".join(f"{start_line + i:4d} | {line}" for i, line in enumerate(lines))

    parts = [f"File: {path}\n\n```python\n{numbered}\n```"]
    if context:
        parts.append(f"\nContext:\n{context}")
    if prior_findings:
        import json
        summaries = [
            {"line": f.line, "severity": f.severity, "comment": f.comment}
            for f in prior_findings
        ]
        parts.append(f"\nPrior findings (from tools):\n{json.dumps(summaries, indent=2)}")
    return "\n".join(parts)


def _relocate_line(snippet: str, reported_line: int, code: str, start_line: int) -> int | None:
    """Re-anchor the model's line number by matching the snippet text.

    The model is told absolute line numbers via the prefixed listing; this
    cross-checks against the actual chunk text.  If the snippet cannot be
    anchored to the chunk, return None so the finding can be dropped.
    """
    code_lines = code.splitlines()
    matches = [
        start_line + i
        for i, line in enumerate(code_lines)
        if line == snippet
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1 and reported_line in matches:
        return reported_line
    return None


def _parse_findings(
    raw: str,
    path: str,
    code: str,
    start_line: int,
) -> list[Finding] | None:
    """Parse and validate the model's JSON output.  Returns None on failure."""
    try:
        response = _LLMResponse.model_validate_json(raw)
    except (ValidationError, ValueError) as exc:
        logger.debug("Parse failed: %s", exc)
        return None

    findings: list[Finding] = []
    for llm_f in response.findings:
        real_line = _relocate_line(llm_f.snippet, llm_f.line, code, start_line)
        if real_line is None:
            logger.debug(
                "Dropped unanchored finding for %s (chunk @line %d): %r",
                path,
                start_line,
                llm_f.snippet,
            )
            continue

        try:
            findings.append(Finding(
                path=path,
                line=real_line,
                severity=llm_f.severity,
                category=llm_f.category,
                comment=llm_f.comment,
                suggestion=llm_f.suggestion,
                source="llm",
                confidence=llm_f.confidence,
            ))
        except ValidationError as exc:
            logger.debug("Dropped invalid finding for %s: %s", path, exc)
    return findings


# ── public entry point ────────────────────────────────────────────────────────

def review(
    code: str,
    path: str,
    start_line: int = 1,
    context: str = "",
    findings: list[Finding] | None = None,
    backend: Backend | None = None,
) -> list[Finding]:
    """Review one code chunk; return validated Finding objects.

    Args:
        code:       Source text of the chunk.
        path:       File path (used in Finding.path and the prompt).
        start_line: Absolute 1-based line number of the first line of *code*.
        context:    Optional free-text context passed to the model.
        findings:   Prior findings from static tools to anchor the model.
        backend:    Override the default OllamaBackend (for testing/swap).
    """
    if findings is None:
        findings = []

    if backend is None:
        backend = OllamaBackend(
            model=MODEL,
            format_schema=_LLMResponse.model_json_schema(),
        )

    user_prompt = _build_user_prompt(code, path, start_line, context, findings)

    # First attempt
    raw = backend.generate(_SYSTEM_PROMPT, user_prompt)
    result = _parse_findings(raw, path, code, start_line)

    if result is None:
        # Single retry with a JSON-only nudge
        logger.warning("Parse failed for %s (chunk @line %d); retrying once.", path, start_line)
        nudge = "\n\nIMPORTANT: Return ONLY the JSON object. No markdown, no explanation."
        raw2 = backend.generate(_SYSTEM_PROMPT, user_prompt + nudge)
        result = _parse_findings(raw2, path, code, start_line)

    if result is None:
        logger.warning(
            "Dropped findings for %s (chunk @line %d) after failed retry.", path, start_line
        )
        return []

    return result
