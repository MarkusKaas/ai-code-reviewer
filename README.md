# AI Code Reviewer

Paste a code snippet or drop in a GitHub PR URL and get a structured review back — bugs, security issues, performance problems, and style notes, each with a severity rating and a concrete fix.

Built with FastAPI + Claude API (tool_use for guaranteed structured output) + Monaco Editor.

## What it does

- **Code snippet review** — paste any code, select a language, get a score (0-10), a verdict, and a categorised issue list
- **GitHub PR review** — paste a PR URL, get a per-file review of every changed file
- **Structured output** — every issue has a category (`bug` / `security` / `performance` / `style` / `logic`), a severity (`critical` / `warning` / `suggestion`), a line reference, and a specific fix
- **Keyboard shortcut** — `Cmd+Enter` / `Ctrl+Enter` to trigger review

## Stack

| Layer | Tech |
|-------|------|
| Backend | Python · FastAPI · Uvicorn |
| AI | Claude API (tool_use) · Haiku by default |
| Schemas | Pydantic v2 |
| GitHub | GitHub REST API v3 (httpx) |
| Frontend | Vanilla JS · Monaco Editor |

## Setup

```bash
# 1. Clone and enter the project
git clone https://github.com/MarkusKaas/ai-code-reviewer
cd ai-code-reviewer

# 2. Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY
# Optionally add GITHUB_TOKEN for private repos / higher rate limits

# 5. Start the server
cd backend
uvicorn main:app --host 127.0.0.1 --port 8000
```

Open [http://localhost:8000](http://localhost:8000).

## API

| Method | Endpoint | Body |
|--------|----------|------|
| `POST` | `/review` | `{code, language?, context?}` |
| `POST` | `/review-pr` | `{pr_url, context?}` |
| `GET` | `/health` | — |

## Switching to Claude Sonnet

For deeper analysis on complex code, change `MODEL` in `backend/reviewer.py`:

```python
MODEL = "claude-sonnet-4-6"   # slower, more thorough
```

## Architecture notes

See [DECISIONS.md](DECISIONS.md) for why each technology was chosen and what alternatives were considered.
