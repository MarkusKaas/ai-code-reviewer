"""
github_pr.py — Fetch pull request diffs from the GitHub API.
"""

from __future__ import annotations
import os
import re

import httpx
from dotenv import load_dotenv

load_dotenv()

_TOKEN = os.getenv("GITHUB_TOKEN", "")
_HEADERS = {
    "Accept": "application/vnd.github.v3+json",
    **({"Authorization": f"Bearer {_TOKEN}"} if _TOKEN else {}),
}

# File extensions to review (skip binary/generated files)
_REVIEWABLE = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".cs", ".java", ".go",
    ".rs", ".cpp", ".c", ".h", ".swift", ".kt", ".rb", ".php",
    ".sql", ".sh", ".yaml", ".yml", ".json", ".html", ".css",
}


def _parse_pr_url(url: str) -> tuple[str, str, int]:
    """Return (owner, repo, pr_number) from a GitHub PR URL."""
    m = re.search(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)", url)
    if not m:
        raise ValueError(f"Not a valid GitHub PR URL: {url}")
    return m.group(1), m.group(2), int(m.group(3))


def _ext(filename: str) -> str:
    _, ext = os.path.splitext(filename)
    return ext.lower()


def fetch_pr_files(pr_url: str) -> tuple[str, list[dict]]:
    """
    Fetch PR metadata and changed files.
    Returns (pr_title, [{filename, patch, language}]).
    """
    owner, repo, number = _parse_pr_url(pr_url)
    base = f"https://api.github.com/repos/{owner}/{repo}"

    with httpx.Client(headers=_HEADERS, timeout=20) as client:
        # PR metadata
        pr_resp = client.get(f"{base}/pulls/{number}")
        pr_resp.raise_for_status()
        pr_data = pr_resp.json()
        title = pr_data.get("title", f"PR #{number}")

        # Changed files
        files_resp = client.get(f"{base}/pulls/{number}/files", params={"per_page": 30})
        files_resp.raise_for_status()
        raw_files = files_resp.json()

    results = []
    for f in raw_files:
        filename = f.get("filename", "")
        patch    = f.get("patch", "")
        status   = f.get("status", "")

        # Skip removed files and non-reviewable extensions
        if status == "removed" or not patch or _ext(filename) not in _REVIEWABLE:
            continue

        results.append({"filename": filename, "patch": patch})

    return title, results
