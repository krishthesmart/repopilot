# RepoPilot

RepoPilot is a small AI maintainer copilot for GitHub repositories. It connects to a repo, fetches open issues, classifies them, suggests labels and a maintainer response, then requires explicit human approval before anything is posted back to GitHub.

The project is intentionally compact: fewer than 20 source/config files, a working FastAPI backend, a React/Vite UI, tests, and Docker support.

## What is included

- LangChain for LLM classification and repo-aware Q&A hooks.
- LangGraph for the issue triage workflow and the human approval gate.
- LangSmith tracing through normal `LANGSMITH_*` environment variables.
- GitHub token access with in-memory token handling.
- Approval-gated write actions for labels and issue comments.
- Demo mode when no GitHub token or Groq key is present.
- Tests for the core API and approval workflow.

## Architecture

```text
React UI
  -> FastAPI API
      -> GitHub issue fetch
      -> LangGraph workflow
          -> classify_issue via LangChain/Groq or deterministic fallback
          -> human_approval_required
      -> approved post to GitHub labels/comments
      -> LangSmith traces from LangChain/LangGraph config
```

All production write actions are behind `/api/approvals/{id}/post`, and that endpoint rejects anything not already approved.

## Setup

Copy the env file:

```bash
cp .env.example .env
```

Backend:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --reload
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173`.

## Environment

```bash
GITHUB_TOKEN=github_pat_or_classic_pat
GROQ_API_KEY=your_groq_key
GROQ_MODEL=llama-3.1-8b-instant
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=your_langsmith_key
LANGSMITH_PROJECT=repopilot
```

Without keys, RepoPilot runs in demo mode with seeded issues and heuristic classification. With keys, LangChain calls Groq and LangSmith receives traces from LangChain/LangGraph runs.

## Usage

1. Enter `owner`, `repo`, and optionally a GitHub token.
2. Click **Connect repo** to fetch issues.
3. Click **Fetch and triage** to run the LangGraph workflow.
4. Review labels and the draft response.
5. Click **Approve** or **Reject**.
6. Click **Post** only after approval.

## Tests

```bash
cd backend
pytest
```

The tests verify demo repo connection, the approval gate, and the approve-then-post path.

## Docker

```bash
docker compose up --build
```

Backend runs on `http://127.0.0.1:8000`; frontend runs on `http://127.0.0.1:5173`.

## Extension points

- Add PR review by creating another LangGraph workflow with nodes for diff loading, risk classification, review drafting, and approval.
- Expand repo Q&A by replacing the simple demo answer with a persistent vector index over docs and selected code.
- Add release notes by fetching merged PRs since the last tag and grouping them through a LangChain structured output chain.
- Replace personal access token entry with a GitHub App or OAuth flow when deploying for multiple maintainers.
