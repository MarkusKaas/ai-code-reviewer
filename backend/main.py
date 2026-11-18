"""
main.py — FastAPI application for the AI Code Reviewer.

Endpoints:
  POST /review        — review a code snippet
  POST /review-pr     — review a GitHub pull request
  GET  /health        — health check (includes model and config info)
"""

from __future__ import annotations

import hmac
import logging
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()   # before importing modules that read env vars at import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from github_pr import GitHubError, PRFile, fetch_pr_files
from reviewer import (
    MAX_CODE_CHARS,
    MODEL,
    ConfigurationError,
    ReviewError,
    is_configured,
    review_code,
)
from schemas import CodeReview, FileReview, PRReview, PRReviewRequest, ReviewRequest

__version__ = "1.2.0"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
logger = logging.getLogger(__name__)

MAX_PR_FILES       = int(os.getenv("MAX_PR_FILES", "20"))
MAX_CONTEXT_LEN    = int(os.getenv("MAX_CONTEXT_LEN", "500"))
# Files in a PR are independent, so they are reviewed in parallel. Kept modest to
# stay inside Anthropic rate limits; the SDK retries any 429s that still happen.
REVIEW_CONCURRENCY = max(1, int(os.getenv("REVIEW_CONCURRENCY", "4")))
# Optional shared secret. When set, every /review* request must send it in the
# X-API-Key header — the minimum needed before exposing this beyond localhost.
API_KEY            = os.getenv("API_KEY", "")
CORS_ORIGINS       = [o.strip() for o in os.getenv(
    "CORS_ORIGINS", "http://localhost:5500,http://127.0.0.1:5500,http://localhost:3000"
).split(",") if o.strip()]

app = FastAPI(title="AI Code Reviewer", version=__version__)

# ── CORS — only needed when the frontend is served from another origin ────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,   # no cookies are used; keeping this off is safer
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-API-Key"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    """
    Tag each request with an ID (echoed in X-Request-ID and every log line) and
    log method, path, status and latency. Also enforces the optional API key.
    """
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    started = time.perf_counter()

    if API_KEY and request.url.path.startswith("/review"):
        supplied = request.headers.get("X-API-Key", "")
        # compare_digest avoids leaking the key through response timing.
        if not hmac.compare_digest(supplied, API_KEY):
            return JSONResponse({"detail": "Missing or invalid API key."}, status_code=401,
                                headers={"X-Request-ID": request_id})

    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    if not request.url.path.startswith("/static"):
        logger.info("[%s] %s %s → %d (%.0f ms)", request_id, request.method, request.url.path,
                    response.status_code, (time.perf_counter() - started) * 1000)
    return response


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
        "status": "ok" if is_configured() else "degraded",
        "version": __version__,
        "model": MODEL,
        "anthropic_key_set": is_configured(),
        "github_token_set": bool(os.getenv("GITHUB_TOKEN")),
        "auth_required": bool(API_KEY),
        "max_code_chars": MAX_CODE_CHARS,
        "max_pr_files": MAX_PR_FILES,
        "review_concurrency": REVIEW_CONCURRENCY,
    }


@app.post("/review", response_model=CodeReview)
def review(req: ReviewRequest):
    """Review a code snippet. Returns a structured CodeReview."""
    try:
        return review_code(req.code, req.language, _sanitize_context(req.context))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except ConfigurationError as e:
        raise HTTPException(503, str(e)) from e
    except TimeoutError:
        raise HTTPException(504, "Review service timed out — try again.") from None
    except ReviewError as e:
        logger.warning("Snippet review returned unusable output: %s", e)
        raise HTTPException(502, "The model returned an unusable review — try again.") from e
    except Exception as e:
        logger.error("Snippet review failed: %s", e, exc_info=True)
        raise HTTPException(500, "Review failed — check server logs.") from e


def _review_file(f: PRFile, language: str, context: str) -> FileReview:
    """Review one PR file. Never raises, so one bad file can't sink the whole PR."""
    note = " The diff was truncated; do not flag the cut-off itself." if f.truncated else ""
    try:
        cr = review_code(f.patch, language, f"This is a git diff for file: {f.filename}.{note} {context}".strip())
        return FileReview(filename=f.filename, review=cr, truncated=f.truncated)
    except Exception as e:
        logger.error("Failed to review %s: %s", f.filename, e, exc_info=not isinstance(e, ReviewError))
        return FileReview(
            filename=f.filename,
            failed=True,
            truncated=f.truncated,
            review=CodeReview(
                language=language,
                summary=f"Review failed: {type(e).__name__}. Try again, or review this file as a snippet.",
                score=0,
                verdict="needs work",
                issues=[],
            ),
        )


@app.post("/review-pr", response_model=PRReview)
def review_pr(req: PRReviewRequest):
    """Fetch a GitHub PR diff and review each changed file concurrently."""
    if not is_configured():
        raise HTTPException(503, "ANTHROPIC_API_KEY is not set — add it to your .env file.")

    try:
        pr = fetch_pr_files(req.pr_url)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except GitHubError as e:
        logger.warning("GitHub fetch failed for %s: %s", req.pr_url, e)
        raise HTTPException(e.status_code, str(e)) from e
    except Exception as e:
        logger.error("GitHub fetch failed for %s: %s", req.pr_url, e, exc_info=True)
        raise HTTPException(502, "GitHub API error — check the PR URL and your GITHUB_TOKEN.") from e

    if not pr.files:
        raise HTTPException(
            400,
            "No reviewable files in this PR (it may be a merge commit, or all changes "
            "were in binary/generated files). Nothing to review."
        )

    if len(pr.files) > MAX_PR_FILES:
        raise HTTPException(
            400,
            f"PR has {len(pr.files)} reviewable files — limit is {MAX_PR_FILES}. "
            f"Set MAX_PR_FILES in .env to raise it."
        )

    language     = req.language or "auto"
    safe_context = _sanitize_context(req.context)

    # pool.map preserves input order, so results line up with the PR's file order.
    with ThreadPoolExecutor(max_workers=REVIEW_CONCURRENCY) as pool:
        file_reviews = list(pool.map(lambda f: _review_file(f, language, safe_context), pr.files))

    if all(fr.failed for fr in file_reviews):
        raise HTTPException(502, "All files failed to review — check server logs.")

    return PRReview(pr_title=pr.title, pr_url=req.pr_url, files=file_reviews, skipped_files=pr.skipped)


# ── Serve frontend ────────────────────────────────────────────────────────────

_FRONTEND = Path(os.getenv("FRONTEND_PATH", str(Path(__file__).parent.parent / "frontend")))

if _FRONTEND.exists():
    app.mount("/static", StaticFiles(directory=str(_FRONTEND)), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        index_file = _FRONTEND / "index.html"
        if not index_file.exists():
            raise HTTPException(404, "Frontend not found.")
        return FileResponse(str(index_file))
