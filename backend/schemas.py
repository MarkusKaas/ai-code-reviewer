"""
schemas.py — Pydantic models for review input/output.
"""

from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field


# ── Review output schema ──────────────────────────────────────────────────────

class Issue(BaseModel):
    category: Literal["bug", "security", "performance", "style", "logic"]
    severity:  Literal["critical", "warning", "suggestion"]
    lines:     str = Field(description="Line or range involved, e.g. '12' or '12-15'. Use 'N/A' if not applicable.")
    description: str = Field(description="Clear explanation of the issue.")
    suggestion:  str = Field(description="Concrete fix or improvement.")


class CodeReview(BaseModel):
    language:    str   = Field(description="Detected or provided programming language.")
    summary:     str   = Field(description="One-paragraph overall assessment.")
    score:       int   = Field(ge=0, le=10, description="Overall code quality score 0-10.")
    verdict:     Literal["ship it", "needs work", "major issues"]
    issues:      list[Issue] = Field(default_factory=list)


# ── Request models ────────────────────────────────────────────────────────────

class ReviewRequest(BaseModel):
    code:     str
    language: str = "auto"   # "auto" triggers detection
    context:  str = ""       # optional description of what the code does


class PRReviewRequest(BaseModel):
    pr_url:   str            # e.g. https://github.com/owner/repo/pull/42
    context:  str = ""
    language: str = "auto"  # override auto-detection for all files in this PR


# ── PR file review ────────────────────────────────────────────────────────────

class FileReview(BaseModel):
    filename: str
    review:   CodeReview


class PRReview(BaseModel):
    pr_title: str
    pr_url:   str
    files:    list[FileReview]
