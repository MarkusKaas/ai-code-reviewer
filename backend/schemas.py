"""
schemas.py — Pydantic models for review input/output.
"""

from __future__ import annotations
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_config


# ── Review output schema ──────────────────────────────────────────────────────

class Issue(BaseModel):
    """One discrete problem or suggestion found during code review."""
    model_config = model_config(str_strip_whitespace=True)

    category:    Literal["bug", "security", "performance", "style", "logic"]
    severity:    Literal["critical", "warning", "suggestion"]
    lines:       str = Field(description="Line or range involved, e.g. '12' or '12-15'. Use 'N/A' if not applicable.")
    description: str = Field(description="Clear explanation of the issue.")
    suggestion:  str = Field(description="Concrete fix or improvement.")

    @field_validator("lines")
    @classmethod
    def validate_lines(cls, v: str) -> str:
        """Accept 'N/A', a single number, or a range like '12-15'."""
        v = v.strip()
        if v == "N/A":
            return v
        if re.fullmatch(r"\d+(-\d+)?", v):
            return v
        # Be lenient: accept anything non-empty rather than rejecting a valid
        # review because the model used a slightly different format (e.g. "L12").
        if v:
            return v
        return "N/A"

    @property
    def severity_rank(self) -> int:
        """Numeric priority for sorting: critical=0, warning=1, suggestion=2."""
        return {"critical": 0, "warning": 1, "suggestion": 2}.get(self.severity, 99)


class CodeReview(BaseModel):
    """Structured result of reviewing one code snippet."""
    model_config = model_config(str_strip_whitespace=True)

    language: str   = Field(description="Detected or provided programming language.")
    summary:  str   = Field(description="One-paragraph overall assessment.")
    score:    int   = Field(ge=0, le=10, description="Overall code quality score 0-10.")
    verdict:  Literal["ship it", "needs work", "major issues"]
    issues:   list[Issue] = Field(default_factory=list)

    @property
    def critical_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "critical")

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "warning")


# ── Request models ────────────────────────────────────────────────────────────

class ReviewRequest(BaseModel):
    """Payload for POST /review."""
    code:     str = Field(description="Source code to review.")
    language: str = Field(default="auto", description="Language hint; 'auto' triggers detection.")
    context:  str = Field(default="", description="Optional description of what the code does.")


class PRReviewRequest(BaseModel):
    """Payload for POST /review-pr."""
    pr_url:   str = Field(description="Full GitHub PR URL, e.g. https://github.com/owner/repo/pull/42")
    context:  str = Field(default="", description="Optional description of the PR's purpose.")
    language: str = Field(default="auto", description="Override auto-detection for all files in this PR.")


# ── PR file review ────────────────────────────────────────────────────────────

class FileReview(BaseModel):
    """Review result for one file in a pull request."""
    filename: str
    review:   CodeReview


class PRReview(BaseModel):
    """Aggregated review result for a full pull request."""
    pr_title: str
    pr_url:   str
    files:    list[FileReview]
