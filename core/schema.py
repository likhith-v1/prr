"""Frozen Finding schema — the contract everything speaks.

Do not add, rename, or remove fields without updating all 5 weeks.
Defined in README.md; reproduced here verbatim.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class Finding(BaseModel):
    path: str
    line: int = Field(ge=1)
    end_line: int | None = Field(default=None, ge=1)
    severity: Literal["info", "warning", "error"]   # cat mood rendered from this
    category: Literal["bug", "security", "style", "perf", "test", "other"]
    comment: str                 # terse human explanation
    suggestion: str | None = None  # replacement code for the line(s)
    source: Literal["llm", "ruff", "mypy", "bandit"]
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)  # used for thresholding LLM findings

    @field_validator("path", "comment")
    @classmethod
    def _non_empty_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value

    @model_validator(mode="after")
    def _valid_range(self) -> "Finding":
        if self.end_line is not None and self.end_line < self.line:
            raise ValueError("end_line must be greater than or equal to line")
        return self


# Severity → cat mood (presentation only, not stored)
MOOD = {
    "error":   "😾 swat",
    "warning": "🙀 hiss",
    "info":    "😸 chirp",
}
