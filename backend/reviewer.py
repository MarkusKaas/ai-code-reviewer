"""
reviewer.py — Claude-powered code review engine.

Uses Claude's tool_use feature to guarantee structured JSON output
matching the CodeReview schema, with no string parsing needed.

Configuration (via environment variables or .env):
  ANTHROPIC_API_KEY   — required; your Anthropic API key
  REVIEW_MODEL        — Claude model to use (default: claude-haiku-4-5-20251001)
  REVIEW_MAX_TOKENS   — max tokens for the review response (default: 2048)
  MAX_CODE_CHARS      — maximum code snippet length in characters (default: 50000)
"""

from __future__ import annotations
import os

import anthropic
from dotenv import load_dotenv

from schemas import CodeReview, Issue

load_dotenv()

_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

# Model is configurable so you can switch from the cheap/fast Haiku to the
# more thorough Sonnet without touching code — just set REVIEW_MODEL in .env.
MODEL          = os.getenv("REVIEW_MODEL", "claude-haiku-4-5-20251001")
MAX_TOKENS     = int(os.getenv("REVIEW_MAX_TOKENS", "2048"))
MAX_CODE_CHARS = int(os.getenv("MAX_CODE_CHARS", "50000"))

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
    """
    Review a code snippet and return a structured CodeReview.

    Raises:
        ValueError: if the code exceeds MAX_CODE_CHARS or the model returns
                    an unexpected response without a tool_use block.
        anthropic.APIError: on network or API-level failures.
    """
    if len(code) > MAX_CODE_CHARS:
        raise ValueError(
            f"Code is {len(code):,} characters — limit is {MAX_CODE_CHARS:,}. "
            "Trim the snippet or raise MAX_CODE_CHARS in .env."
        )

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
        max_tokens=MAX_TOKENS,
        system=_SYSTEM,
        tools=[_REVIEW_TOOL],
        tool_choice={"type": "tool", "name": "submit_review"},
        messages=[{"role": "user", "content": user_msg}],
    )

    # The tool_choice forces Claude to call submit_review, so a missing
    # tool_use block would be a model regression — surface it clearly.
    tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
    if not tool_use_blocks:
        raise ValueError(
            f"Model returned no tool_use block (stop_reason={response.stop_reason!r}). "
            "This is unexpected — the request uses tool_choice=tool, which forces a call."
        )

    data = tool_use_blocks[0].input

    issues = [Issue(**i) for i in data.get("issues", [])]
    return CodeReview(
        language=data["language"],
        summary=data["summary"],
        score=data["score"],
        verdict=data["verdict"],
        issues=issues,
    )
