# DECISIONS.md — AI Code Reviewer

Architectural decisions, alternatives considered, and trade-offs made.

---

## 1. Claude API via tool_use, not prompt + JSON parse

**Decision:** Use Claude's `tool_use` feature with `tool_choice: {type: "tool", name: "submit_review"}` to guarantee structured output.

**Alternatives considered:**
- Prompt the model to return JSON and parse it with `json.loads()` — fragile, breaks on markdown fences or verbose preamble.
- Use a regex to extract a JSON block — hacky and maintenance-prone.

**Why tool_use:** Forces Claude to populate a defined schema before it can respond. No parsing, no fallbacks, no surprises. The API rejects malformed tool calls before they reach our code.

---

## 2. scikit-free, no vector DB for code review

**Decision:** No RAG, no embeddings, no vector store. The entire review is done in a single Claude call with the code inline.

**Why:** Code review is a generation task, not a retrieval task. The context fits in a single prompt. Adding RAG would add latency and complexity with no quality benefit for snippets under ~500 lines.

**Trade-off:** Very long files (>1000 lines) exceed the token budget. Mitigation: the frontend editor gives users a natural chunking point; PR reviews process one file at a time.

---

## 3. Haiku as default, Sonnet available

**Decision:** Default to `claude-haiku-4-5-20251001` for speed and cost. One environment variable (`MODEL` in `reviewer.py`) swaps to Sonnet for deeper analysis.

**Why Haiku by default:** A code review round-trip should feel instant (<3s). Haiku delivers review quality that is good enough for most snippets. Sonnet adds ~2s latency and costs ~5× more per token.

**When to switch to Sonnet:** Security-sensitive code, large functions with complex logic, or when users report missed issues.

---

## 4. Issue structured by category + severity, not by line number

**Decision:** Output is `{category, severity, lines, description, suggestion}` per issue, grouped in the UI by severity.

**Alternatives considered:**
- Annotate issues inline in the code (diff-style) — requires Monaco decoration API, adds UI complexity.
- Group by category — logical but makes severity harder to scan.

**Why severity-first grouping:** A reviewer scanning results wants to triage `critical` issues immediately, regardless of whether they're bugs or security issues. Category is secondary metadata shown as a tag.

---

## 5. GitHub PR integration fetches diffs, not full file content

**Decision:** Pull the git diff (`patch`) from the GitHub files API rather than fetching the full file content for each changed file.

**Why:** Diffs are smaller (fewer tokens), focus Claude on the *changed* code rather than stable background code, and work without checking out the branch. The trade-off is that Claude has less context about surrounding code — mitigated by the `context` field where users can describe the PR's intent.

---

## 6. Single-file frontend, Monaco from CDN

**Decision:** `frontend/index.html` is self-contained with all CSS and JS inline. Monaco editor is loaded from the Cloudflare CDN.

**Why not React/Vite:** The project already uses FastAPI to serve the frontend. A build step adds toolchain complexity without meaningful UX benefit for a single-page tool.

**Why Monaco over textarea:** Syntax highlighting, line numbers, and the familar editor experience make the code review feel native. Monaco is 450KB gzipped from CDN — acceptable for a dev tool.

**Risk:** CDN dependency for Monaco. Acceptable because this is a developer tool (devs have internet access) and Monaco has no self-hostable alternative without a build step.

---

## 7. No authentication on the API

**Decision:** The API is open — no auth token required.

**Why:** This is a local-first developer tool. Running `uvicorn main:app` binds to `127.0.0.1` by default, so the API is only reachable from the same machine.

**If deploying publicly:** Add an `API_KEY` header check as FastAPI middleware, or put it behind a reverse proxy with basic auth.
