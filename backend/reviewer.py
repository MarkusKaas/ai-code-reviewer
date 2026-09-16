"""
reviewer.py — Claude-powered code review engine.

Uses Claude's tool_use feature to guarantee structured JSON output
matching the CodeReview schema, with no string parsing needed.
"""

from __future__ import annotations
import json
import os

import anthropic
from dotenv import load_dotenv

from schemas import CodeReview, Issue

load_dotenv()

_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

MODEL = "claude-haiku-4-5-20251001"   # fast + cheap; swap to claude-sonnet-4-6 for deeper reviews

# ── Tool schema — Claude must call this to return a review ────────────────────

_REVIEW_TOOL = {
    "name": "submit_review",
    "description": "Submit a structured code review.",
    "input_schema": {
        "type": "object",
        "properties": {
            "language":  {"type": "string",  "description": "Detected or provided programming language."},
            "summary":   {"type": "string",  "description": "One-paragraph overall assessment of the code."},
            "score":     {"type": "integer", "description": "Overall code quality score from 0 (worst) to 10 (best).", "minimum": 0, "maximum": 10},
            "verdict":   {"type": "string",  "enum": ["ship it", "needs work", "major issues"]},
            "issues": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "category":    {"type": "string", "enum": ["bug", "security", "performance", "style", "logic"]},
                        "severity":    {"type": "string", "enum": ["critical", "warning", "suggestion"]},
                        "lines":       {"type": "string", "description": "Line or range, e.g. '12' or '12-15'. Use 'N/A' if not applicable."},
                        "description": {"type": "string"},
                        "suggestion":  {"type": "string"},
                    },
                    "required": ["category", "severity", "lines", "description", "suggestion"],
                },
            },
        },
        "required": ["language", "summary", "score", "verdict", "issues"],
    },
}

_SYSTEM = """You are an expert code reviewer with deep knowledge across languages and security practices.
Your job is to review code and return a structured analysis via the submit_review tool.

Review criteria:
- BUGS: logic errors, off-by-one, null dereferences, incorrect assumptions
- SECURITY: injection risks, hardcoded secrets, unsafe deserialization, missing auth checks
- PERFORMANCE: O(n²) in hot paths, unnecessary allocations, blocking calls, N+1 queries
- STYLE: naming, readability, dead code, overly complex expressions
- LOGIC: business logic issues, incorrect conditionals, missing edge cases

Scoring guide (0-10):
  9-10 → production-ready, minor nitpicks only
  7-8  → good code, a few improvements possible
  5-6  → works but has notable issues
  3-4  → significant problems, needs rework
  0-2  → major bugs or security issues

Be specific: always reference line numbers when possible.
Be constructive: every issue must have a concrete suggestion.
"""


def _numbered(code: str) -> str:
    """Prepend line numbers so Claude can reference them."""
    lines = code.splitlines()
    width = len(str(len(lines)))
    return "\n".join(f"{str(i + 1).rjust(width)} | {line}" for i, line in enumerate(lines))


def review_code(code: str, language: str = "auto", context: str = "") -> CodeReview:
    """Review a code snippet and return a structured CodeReview."""

    lang_hint = f"Language: {language}\n" if language != "auto" else "Detect the language automatically.\n"
    ctx_hint  = f"Context: {context}\n" if context else ""

    user_msg = f"""{lang_hint}{ctx_hint}
Code to review:
```
{_numbered(code)}
```
"""
    response = _client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=_SYSTEM,
        tools=[_REVIEW_TOOL],
        tool_choice={"type": "tool", "name": "submit_review"},
        messages=[{"role": "user", "content": user_msg}],
    )

    # Extract tool call input
    tool_use = next(b for b in response.content if b.type == "tool_use")
    data = tool_use.input

    issues = [Issue(**i) for i in data.get("issues", [])]
    return CodeReview(
        language=data["language"],
        summary=data["summary"],
        score=data["score"],
        verdict=data["verdict"],
        issues=issues,
    )
