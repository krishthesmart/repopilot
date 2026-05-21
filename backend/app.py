from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from os import getenv
from threading import Lock
from typing import Any, TypedDict
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field


class Issue(BaseModel):
    number: int
    title: str
    body: str | None = None
    author: str | None = None
    labels: list[str] = []
    url: str | None = None


class RepoConnect(BaseModel):
    owner: str = "demo"
    repo: str = "repopilot"
    github_token: str | None = Field(default=None, description="PAT used in memory only")


class Triage(BaseModel):
    issue: Issue
    category: str
    priority: str
    labels: list[str]
    response: str
    rationale: str
    confidence: float


class Status(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    posted = "posted"


class Approval(BaseModel):
    id: str
    repo: str
    issue_number: int
    result: Triage
    status: Status = Status.pending
    created_at: datetime
    updated_at: datetime
    posted_url: str | None = None


class Decision(BaseModel):
    approved: bool
    labels: list[str] | None = None
    response: str | None = None


class Question(BaseModel):
    owner: str = "demo"
    repo: str = "repopilot"
    question: str
    github_token: str | None = None


class TriageState(TypedDict, total=False):
    owner: str
    repo: str
    issue: Issue
    result: Triage
    approval_id: str


class Store:
    def __init__(self) -> None:
        self.approvals: dict[str, Approval] = {}
        self.history: list[dict[str, Any]] = []
        self.lock = Lock()

    def save(self, approval: Approval) -> Approval:
        with self.lock:
            approval.updated_at = datetime.now(UTC)
            self.approvals[approval.id] = approval
        return approval


store = Store()
app = FastAPI(title="RepoPilot", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def token_from(value: str | None) -> str | None:
    return value or getenv("GITHUB_TOKEN")


def demo_issues() -> list[Issue]:
    return [
        Issue(
            number=42,
            title="Bug: CLI crashes when repo has no README",
            body="Running triage on a repo without README.md throws a 500 instead of a useful warning.",
            author="octo-maintainer",
            labels=["bug"],
            url="https://github.com/demo/repopilot/issues/42",
        ),
        Issue(
            number=41,
            title="Feature request: generate release notes by label",
            body="Draft release notes from merged PRs grouped by feature, fix, docs, and chore labels.",
            author="release-captain",
            labels=[],
            url="https://github.com/demo/repopilot/issues/41",
        ),
    ]


async def github_get_issues(owner: str, repo: str, token: str | None) -> list[Issue]:
    if not token:
        return demo_issues()
    headers = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(
            f"https://api.github.com/repos/{owner}/{repo}/issues",
            headers=headers,
            params={"state": "open", "per_page": 8, "sort": "created", "direction": "desc"},
        )
        response.raise_for_status()
    issues = [item for item in response.json() if "pull_request" not in item]
    return [
        Issue(
            number=item["number"],
            title=item["title"],
            body=item.get("body"),
            author=item.get("user", {}).get("login"),
            labels=[label["name"] for label in item.get("labels", [])],
            url=item.get("html_url"),
        )
        for item in issues
    ]


async def github_list_repos(token: str | None) -> list[dict[str, str]]:
    if not token:
        return [{"full_name": "demo/repopilot", "owner": "demo", "repo": "repopilot"}]
    headers = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(
            "https://api.github.com/user/repos",
            headers=headers,
            params={"per_page": 100, "sort": "updated", "affiliation": "owner,collaborator,organization_member"},
        )
    if response.status_code == 401:
        raise HTTPException(401, "GitHub token was rejected. Check token value and repository permissions.")
    if response.status_code == 403:
        raise HTTPException(403, "GitHub token cannot list repositories. Use a fine-grained token with Metadata read access.")
    response.raise_for_status()
    return [
        {"full_name": item["full_name"], "owner": item["owner"]["login"], "repo": item["name"]}
        for item in response.json()
    ]


async def classify_issue(issue: Issue) -> Triage:
    try:
        if not getenv("GROQ_API_KEY"):
            raise RuntimeError("GROQ_API_KEY is not set; using deterministic demo classifier")
        from langchain_core.output_parsers import JsonOutputParser
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_groq import ChatGroq

        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", "You are RepoPilot. Return JSON: category, priority, labels, response, rationale, confidence."),
                ("human", "Issue #{number}: {title}\n\n{body}\nExisting labels: {labels}"),
            ]
        )
        model = getenv("GROQ_MODEL", "llama-3.1-8b-instant")
        chain = prompt | ChatGroq(model=model, temperature=0) | JsonOutputParser()
        data = await chain.ainvoke(
            {"number": issue.number, "title": issue.title, "body": issue.body or "", "labels": issue.labels},
            config={"tags": ["repopilot", "triage"], "metadata": {"issue": issue.number}},
        )
        return Triage(issue=issue, **data)
    except Exception:
        text = f"{issue.title} {issue.body or ''}".lower()
        if any(term in text for term in ["crash", "bug", "500", "error", "fails"]):
            return Triage(
                issue=issue,
                category="bug",
                priority="high",
                labels=["bug", "priority:high"],
                response="Thanks for the report. This looks reproducible and should be prioritized. Could you share the command, version, and minimal repo shape?",
                rationale="Failure and crash keywords indicate a high-impact bug.",
                confidence=0.84,
            )
        if any(term in text for term in ["feature", "request", "generate", "add"]):
            return Triage(
                issue=issue,
                category="feature_request",
                priority="medium",
                labels=["enhancement", "needs-design"],
                response="Thanks for the suggestion. This fits the project direction; a maintainer should confirm scope before implementation.",
                rationale="The issue asks for new capability rather than broken behavior.",
                confidence=0.76,
            )
        return Triage(
            issue=issue,
            category="question",
            priority="low",
            labels=["question", "needs-triage"],
            response="Thanks for opening this. A maintainer should clarify expected behavior before assigning implementation labels.",
            rationale="The issue needs more context before action.",
            confidence=0.62,
        )


def build_graph():
    from langgraph.graph import END, StateGraph

    async def classify(state: TriageState) -> TriageState:
        return {**state, "result": await classify_issue(state["issue"])}

    async def approval_gate(state: TriageState) -> TriageState:
        now = datetime.now(UTC)
        approval = Approval(
            id=str(uuid4()),
            repo=f"{state['owner']}/{state['repo']}",
            issue_number=state["issue"].number,
            result=state["result"],
            created_at=now,
            updated_at=now,
        )
        store.save(approval)
        store.history.insert(
            0,
            {
                "run_id": approval.id,
                "repo": approval.repo,
                "issue": approval.issue_number,
                "action": "triage_issue",
                "status": "approval_pending",
                "trace_project": getenv("LANGSMITH_PROJECT", "repopilot"),
                "created_at": now.isoformat(),
            },
        )
        return {**state, "approval_id": approval.id}

    graph = StateGraph(TriageState)
    graph.add_node("classify_issue", classify)
    graph.add_node("human_approval_required", approval_gate)
    graph.set_entry_point("classify_issue")
    graph.add_edge("classify_issue", "human_approval_required")
    graph.add_edge("human_approval_required", END)
    return graph.compile()


async def run_triage(owner: str, repo: str, issue: Issue) -> Approval:
    try:
        final = await build_graph().ainvoke(
            {"owner": owner, "repo": repo, "issue": issue},
            config={"run_name": "RepoPilot issue triage", "tags": ["repopilot"], "metadata": {"repo": f"{owner}/{repo}"}},
        )
    except Exception:
        result = await classify_issue(issue)
        now = datetime.now(UTC)
        approval = Approval(id=str(uuid4()), repo=f"{owner}/{repo}", issue_number=issue.number, result=result, created_at=now, updated_at=now)
        store.save(approval)
        final = {"approval_id": approval.id}
    return store.approvals[final["approval_id"]]


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/repos/connect")
async def connect(payload: RepoConnect) -> dict[str, Any]:
    token = token_from(payload.github_token)
    issues = await github_get_issues(payload.owner, payload.repo, token)
    return {"repo": f"{payload.owner}/{payload.repo}", "demo_mode": token is None, "issues": issues}


@app.post("/api/repos/list")
async def list_repos(payload: RepoConnect) -> dict[str, Any]:
    token = token_from(payload.github_token)
    repos = await github_list_repos(token)
    return {"demo_mode": token is None, "repos": repos}


@app.post("/api/repos/{owner}/{repo}/triage")
async def triage(owner: str, repo: str, payload: RepoConnect) -> list[Approval]:
    issues = await github_get_issues(owner, repo, token_from(payload.github_token))
    return [await run_triage(owner, repo, issue) for issue in issues[:5]]


@app.get("/api/approvals")
def approvals() -> list[Approval]:
    return sorted(store.approvals.values(), key=lambda item: item.created_at, reverse=True)


@app.post("/api/approvals/{approval_id}/decision")
def decide(approval_id: str, decision: Decision) -> Approval:
    approval = store.approvals.get(approval_id)
    if not approval:
        raise HTTPException(404, "Approval not found")
    approval.status = Status.approved if decision.approved else Status.rejected
    if decision.labels is not None:
        approval.result.labels = decision.labels
    if decision.response:
        approval.result.response = decision.response
    return store.save(approval)


@app.post("/api/approvals/{approval_id}/post")
async def post(approval_id: str, token: str | None = None) -> Approval:
    approval = store.approvals.get(approval_id)
    if not approval:
        raise HTTPException(404, "Approval not found")
    if approval.status != Status.approved:
        raise HTTPException(409, "Approval must be approved before posting")
    owner, repo = approval.repo.split("/", 1)
    github_token = token_from(token)
    if github_token:
        headers = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {github_token}"}
        async with httpx.AsyncClient(timeout=20) as client:
            await client.post(f"https://api.github.com/repos/{owner}/{repo}/issues/{approval.issue_number}/labels", headers=headers, json={"labels": approval.result.labels})
            response = await client.post(f"https://api.github.com/repos/{owner}/{repo}/issues/{approval.issue_number}/comments", headers=headers, json={"body": approval.result.response})
            response.raise_for_status()
            approval.posted_url = response.json().get("html_url")
    else:
        approval.posted_url = f"https://github.com/{approval.repo}/issues/{approval.issue_number}#demo-comment"
    approval.status = Status.posted
    return store.save(approval)


@app.post("/api/qa")
async def qa(payload: Question) -> dict[str, Any]:
    answer = "RepoPilot uses LangGraph for issue triage, a human approval gate before writes, and LangSmith tracing through LANGSMITH_TRACING=true."
    return {"answer": answer, "sources": ["README.md", "backend/app.py"]}


@app.get("/api/history")
def history() -> list[dict[str, Any]]:
    return store.history[:50]
