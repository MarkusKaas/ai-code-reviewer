"""
github_pr.py — Fetch pull request diffs from the GitHub API.

Configuration (via environment variables or .env):
  GITHUB_TOKEN       — optional; unauthenticated requests hit rate limits
                       faster (60 req/hr vs 5000 req/hr with a token)
  MAX_PATCH_CHARS    — maximum characters per file patch before truncation
                       (default: 20000). Prevents a single large file from
                       blowing up the review prompt.
  GITHUB_MAX_PAGES   — how many pages of 100 changed files to fetch (default: 5)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import httpx
from dotenv import load_dotenv

load_dotenv()

_API = "https://api.github.com"
_PER_PAGE = 100

# Per-file patch is truncated to this many characters before being sent
# to the reviewer. A diff of 20 000 chars is already ~5 000 tokens, which
# is well within the model's context but enough to cover most changed files.
MAX_PATCH_CHARS = int(os.getenv("MAX_PATCH_CHARS", "20000"))
MAX_PAGES       = int(os.getenv("GITHUB_MAX_PAGES", "5"))

# File extensions to review (skip binary/generated files)
_REVIEWABLE = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs", ".cs", ".java", ".go",
    ".rs", ".cpp", ".cc", ".c", ".h", ".hpp", ".swift", ".kt", ".rb", ".php",
    ".sql", ".sh", ".ps1", ".yaml", ".yml", ".json", ".html", ".css", ".scss",
    ".vue", ".svelte", ".scala", ".dart", ".tf", ".razor", ".cshtml",
}

# Generated files that match a reviewable extension but aren't worth tokens.
_SKIP_NAMES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
    "composer.lock", "Cargo.lock", "go.sum", "packages.lock.json",
}
_SKIP_SUFFIXES = (".min.js", ".min.css", ".map", ".designer.cs", ".g.cs")
_SKIP_DIRS = ("node_modules/", "dist/", "build/", "vendor/", "/Migrations/")


class GitHubError(RuntimeError):
    """A GitHub API failure with a user-presentable message and HTTP status."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class PRFile:
    filename: str
    patch: str
    truncated: bool = False


@dataclass
class PRFiles:
    title: str
    files: list[PRFile] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)   # filenames not sent for review


def _headers() -> dict[str, str]:
    # Read the token per call, so tests (and a .env edit + reload) see the current value.
    token = os.getenv("GITHUB_TOKEN", "")
    return {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ai-code-reviewer",
        **({"Authorization": f"Bearer {token}"} if token else {}),
    }


def parse_pr_url(url: str) -> tuple[str, str, int]:
    """Return (owner, repo, pr_number) from a GitHub PR URL."""
    m = re.search(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)", url)
    if not m:
        raise ValueError(f"Not a valid GitHub PR URL: {url}")
    return m.group(1), m.group(2), int(m.group(3))


def is_reviewable(filename: str) -> bool:
    name = os.path.basename(filename)
    if name in _SKIP_NAMES or name.lower().endswith(_SKIP_SUFFIXES):
        return False
    if any(d in f"/{filename}" for d in _SKIP_DIRS):
        return False
    _, ext = os.path.splitext(filename)
    return ext.lower() in _REVIEWABLE


def truncate_patch(patch: str, limit: int = MAX_PATCH_CHARS) -> tuple[str, bool]:
    """
    Cut a patch to `limit` chars on a line boundary, appending a notice so the
    reviewer knows it is seeing a partial diff.
    """
    if len(patch) <= limit:
        return patch, False
    cut = patch.rfind("\n", 0, limit)
    cut = cut if cut > 0 else limit
    omitted = len(patch) - cut
    return patch[:cut] + f"\n\n[... patch truncated — {omitted:,} chars omitted ...]", True


def _raise_for_github(resp: httpx.Response) -> None:
    """Translate GitHub error responses into actionable messages."""
    if resp.is_success:
        return
    if resp.status_code == 404:
        raise GitHubError("PR not found — check the URL, or set GITHUB_TOKEN for private repos.", 404)
    if resp.status_code in (403, 429) and resp.headers.get("x-ratelimit-remaining") == "0":
        raise GitHubError("GitHub rate limit reached — set GITHUB_TOKEN in .env to raise it.", 429)
    if resp.status_code == 401:
        raise GitHubError("GitHub rejected the token — check GITHUB_TOKEN in .env.", 502)
    raise GitHubError(f"GitHub API error ({resp.status_code}).", 502)


def fetch_pr_files(pr_url: str) -> PRFiles:
    """Fetch PR metadata and every changed file (paginated)."""
    owner, repo, number = parse_pr_url(pr_url)
    base = f"{_API}/repos/{owner}/{repo}/pulls/{number}"

    try:
        with httpx.Client(headers=_headers(), timeout=20) as client:
            pr_resp = client.get(base)
            _raise_for_github(pr_resp)
            result = PRFiles(title=pr_resp.json().get("title", f"PR #{number}"))

            # The previous version requested only the first 30 files, so larger PRs
            # were silently reviewed in part. Follow pages until a short page.
            raw_files: list[dict] = []
            for page in range(1, MAX_PAGES + 1):
                files_resp = client.get(f"{base}/files", params={"per_page": _PER_PAGE, "page": page})
                _raise_for_github(files_resp)
                batch = files_resp.json()
                raw_files.extend(batch)
                if len(batch) < _PER_PAGE:
                    break
    except httpx.TimeoutException as e:
        raise GitHubError("GitHub didn't respond in time — try again.", 504) from e
    except httpx.TransportError as e:
        raise GitHubError("Couldn't reach GitHub — check your network connection.", 502) from e

    for f in raw_files:
        filename = f.get("filename", "")
        patch    = f.get("patch", "")   # absent for binary files and very large diffs
        status   = f.get("status", "")

        if status == "removed" or not patch or not is_reviewable(filename):
            result.skipped.append(filename)
            continue

        patch, truncated = truncate_patch(patch)
        result.files.append(PRFile(filename=filename, patch=patch, truncated=truncated))

    return result
