"""
schemas.py — Pydantic models for review input/output.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

Category = Literal["bug", "security", "performance", "style", "logic"]
Severity = Literal["critical", "warning", "suggestion"]
Verdict  = Literal["ship it", "needs work", "major issues"]

SEVERITY_ORDER: dict[str, int] = {"critical": 0, "warning": 1, "suggestion": 2}

# Accepts https://github.com/<owner>/<repo>/pull/<n>, with an optional trailing
# slash, sub-page (/files, /commits) or query/fragment.
PR_URL_PATTERN = re.compile(
    r"^https://github\.com/[\w.-]+/[\w.-]+/pull/\d+(?:/[\w-]*)?/?(?:[?#].*)?$"
)


# ── Review output schema ──────────────────────────────────────────────────────

class Issue(BaseModel):
    """One discrete problem or suggestion found during code review."""
    # NB: the previous `from pydantic import model_config` does not exist and made
    # the whole backend fail on import. ConfigDict is the Pydantic v2 API.
    model_config = ConfigDict(str_strip_whitespace=True)

    category:    Category
    severity:    Severity
    lines:       str = Field(description="Line or range involved, e.g. '12' or '12-15'. Use 'N/A' if not applicable.")
    description: str = Field(min_length=1, description="Clear explanation of the issue.")
    suggestion:  str = Field(min_length=1, description="Concrete fix or improvement.")

    @field_validator("lines", mode="before")
    @classmethod
    def normalise_lines(cls, v: object) -> str:
        """
        Normalise the line reference. Models sometimes answer with an int, 'L12',
        'line 12' or '12–15' (en dash); all of those are mapped to '12' / '12-15'.
        Anything unrecognisable is kept as-is rather than rejecting a valid review.
        """
        s = str(v).strip() if v is not None else ""
        if not s or s.upper() in {"N/A", "NA", "NONE", "-"}:
            return "N/A"
        m = re.fullmatch(r"(?:lines?\s*|L)?(\d+)\s*(?:[-–—]|to)\s*(?:L)?(\d+)", s, re.IGNORECASE)
        if m:
            start, end = sorted((int(m.group(1)), int(m.group(2))))
            return f"{start}-{end}" if start != end else str(start)
        m = re.fullmatch(r"(?:lines?\s*|L)?(\d+)", s, re.IGNORECASE)
        if m:
            return m.group(1)
        return s

    @property
    def severity_rank(self) -> int:
        """Numeric priority for sorting: critical=0, warning=1, suggestion=2."""
        return SEVERITY_ORDER.get(self.severity, 99)


class CodeReview(BaseModel):
    """Structured result of reviewing one code snippet."""
    model_config = ConfigDict(str_strip_whitespace=True)

    language: str     = Field(description="Detected or provided programming language.")
    summary:  str     = Field(description="One-paragraph overall assessment.")
    score:    int     = Field(ge=0, le=10, description="Overall code quality score 0-10.")
    verdict:  Verdict
    issues:   list[Issue] = Field(default_factory=list)

    @field_validator("issues")
    @classmethod
    def sort_issues(cls, v: list[Issue]) -> list[Issue]:
        """Most severe first, then by first referenced line — the order a reviewer triages in."""
        def first_line(issue: Issue) -> int:
            m = re.match(r"\d+", issue.lines)
            return int(m.group()) if m else 10**9
        return sorted(v, key=lambda i: (i.severity_rank, first_line(i)))

    # computed_field puts these in the JSON response, so the UI and API
    # clients get the counts without re-deriving them.
    @computed_field  # type: ignore[prop-decorator]
    @property
    def critical_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "critical")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "warning")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def suggestion_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "suggestion")


# ── Request models ────────────────────────────────────────────────────────────
# Length caps reject oversized payloads at the edge with a 422, before they cost
# API tokens. The finer-grained MAX_CODE_CHARS limit is enforced in reviewer.py.

class ReviewRequest(BaseModel):
    """Payload for POST /review."""
    code:     str = Field(max_length=200_000, description="Source code to review.")
    language: str = Field(default="auto", max_length=40, description="Language hint; 'auto' triggers detection.")
    context:  str = Field(default="", max_length=2_000, description="Optional description of what the code does.")


class PRReviewRequest(BaseModel):
    """Payload for POST /review-pr."""
    pr_url:   str = Field(max_length=300, description="Full GitHub PR URL, e.g. https://github.com/owner/repo/pull/42")
    context:  str = Field(default="", max_length=2_000, description="Optional description of the PR's purpose.")
    language: str = Field(default="auto", max_length=40, description="Override auto-detection for all files in this PR.")

    @field_validator("pr_url")
    @classmethod
    def validate_pr_url(cls, v: str) -> str:
        v = v.strip()
        if not PR_URL_PATTERN.match(v):
            raise ValueError("Expected a GitHub PR URL like https://github.com/owner/repo/pull/42")
        return v


# ── PR file review ────────────────────────────────────────────────────────────

class FileReview(BaseModel):
    """Review result for one file in a pull request."""
    filename:  str
    review:    CodeReview
    failed:    bool = False   # True when the review errored and `review` is a placeholder
    truncated: bool = False   # True when the diff was cut to MAX_PATCH_CHARS before review


class PRReview(BaseModel):
    """Aggregated review result for a full pull request."""
    pr_title:      str
    pr_url:        str
    files:         list[FileReview]
    skipped_files: list[str] = Field(default_factory=list)   # binary/generated/removed files

    @computed_field  # type: ignore[prop-decorator]
    @property
    def average_score(self) -> float | None:
        """Mean score across successfully reviewed files (failed placeholders excluded)."""
        scores = [f.review.score for f in self.files if not f.failed]
        return round(sum(scores) / len(scores), 1) if scores else None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_critical(self) -> int:
        return sum(f.review.critical_count for f in self.files)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def overall_verdict(self) -> Verdict | None:
        """The worst verdict of any successfully reviewed file."""
        order = ["ship it", "needs work", "major issues"]
        verdicts = [f.review.verdict for f in self.files if not f.failed]
        return max(verdicts, key=order.index) if verdicts else None
