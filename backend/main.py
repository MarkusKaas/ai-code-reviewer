"""
main.py — FastAPI application for the AI Code Reviewer.

Endpoints:
  POST /review        — review a code snippet
  POST /review-pr     — review a GitHub pull request
  GET  /health        — health check (includes model and config info)
"""

from __future__ import annotations
import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from dotenv import load_dotenv

from schemas import ReviewRequest, PRReviewRequest, CodeReview, PRReview, FileReview
from reviewer import review_code, MODEL, MAX_CODE_CHARS
from github_pr import fetch_pr_files

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
logger = logging.getLogger(__name__)

MAX_PR_FILES    = int(os.getenv("MAX_PR_FILES", "20"))
MAX_CONTEXT_LEN = int(os.getenv("MAX_CONTEXT_LEN", "500"))

app = FastAPI(title="AI Code Reviewer", version="1.0.0")

# ── CORS — allow the frontend to call the API from any localhost port ─────────
# In production you'd lock this down to your own domain.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5500", "http://127.0.0.1:5500", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


def _sanitize_context(ctx: str) -> str:
    """Strip control characters and cap length to limit prompt injection surface."""
    safe = "".join(ch for ch in ctx if ch.isprintable() or ch in "\n\t")
    return safe[:MAX_CONTEXT_LEN]


# ── API routes ────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    """Returns service status and the active model/config so you can confirm
    what's running without having to check logs or environment variables."""
    return {
        "status": "ok",
        "model": MODEL,
        "max_code_chars": MAX_CODE_CHARS,
        "max_pr_files": MAX_PR_FILES,
        "github_token_set": bool(os.getenv("GITHUB_TOKEN")),
    }


@app.post("/review", response_model=CodeReview)
def review(req: ReviewRequest):
    """Review a code snippet. Returns a structured CodeReview."""
    if not req.code.strip():
        raise HTTPException(400, "Code cannot be empty.")

    if len(req.code) > MAX_CODE_CHARS:
        raise HTTPException(
            400,
            f"Code is {len(req.code):,} characters — limit is {MAX_CODE_CHARS:,}. "
            "Trim the snippet or raise MAX_CODE_CHARS in .env."
        )

    try:
        return review_code(req.code, req.language, _sanitize_context(req.context))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except TimeoutError:
        raise HTTPException(504, "Review service timed out — try again.")
    except Exception as e:
        logger.error("Snippet review failed: %s", e, exc_info=True)
        raise HTTPException(500, "Review failed — check server logs.")


@app.post("/review-pr", response_model=PRReview)
def review_pr(req: PRReviewRequest):
    """Fetch a GitHub PR diff and review each changed file."""
    try:
        title, files = fetch_pr_files(req.pr_url)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error("GitHub fetch failed for %s: %s", req.pr_url, e, exc_info=True)
        raise HTTPException(502, "GitHub API error — check the PR URL and your GITHUB_TOKEN.")

    if not files:
        raise HTTPException(
            400,
            "No files were changed in this PR (it may be a merge commit or all changes "
            "were in non-reviewable file types). Nothing to review."
        )

    if len(files) > MAX_PR_FILES:
        raise HTTPException(
            400,
            f"PR has {len(files)} changed files — limit is {MAX_PR_FILES}. "
            f"Set MAX_PR_FILES in .env to raise it."
        )

    language     = req.language or "auto"
    safe_context = _sanitize_context(req.context)

    file_reviews: list[FileReview] = []
    for f in files:
        try:
            cr = review_code(
                f["patch"],
                language,
                f"This is a git diff for file: {f['filename']}. {safe_context}",
            )
            file_reviews.append(FileReview(filename=f["filename"], review=cr))
        except Exception as e:
            logger.error("Failed to review %s: %s", f["filename"], e, exc_info=True)
            # Return a graceful error entry rather than silently dropping the file
            file_reviews.append(FileReview(
                filename=f["filename"],
                review=CodeReview(
                    language=language,
                    summary=f"Review failed: {type(e).__name__}",
                    score=0,
                    verdict="needs work",
                    issues=[],
                ),
            ))

    if not file_reviews:
        raise HTTPException(500, "All files failed to review — check server logs.")

    return PRReview(pr_title=title, pr_url=req.pr_url, files=file_reviews)


# ── Serve frontend ────────────────────────────────────────────────────────────

_FRONTEND = Path(os.getenv("FRONTEND_PATH", str(Path(__file__).parent.parent / "frontend")))

if _FRONTEND.exists():
    app.mount("/static", StaticFiles(directory=str(_FRONTEND)), name="static")

    @app.get("/")
    def index():
        index_file = _FRONTEND / "index.html"
        if not index_file.exists():
            raise HTTPException(404, "Frontend not found.")
        return FileResponse(str(index_file))
