from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from os import getenv
from threading import Lock
from typing import Any, TypedDict
from uuid import uuid4
import base64

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
    scan_all: bool = False


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
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):51[0-9]{2}",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def token_from(value: str | None) -> str | None:
    return value or getenv("GITHUB_TOKEN")


def demo_issues() -> list[Issue]:
    return [
        Issue(
            number=-1,
            title="Handle missing GitHub token before remote calls",
            body="backend/app.py should validate missing or expired GitHub tokens before trying write operations, so users get a direct setup message.",
            author="RepoPilot",
            labels=["bug", "code-scan"],
            url="https://github.com/demo/repopilot/blob/main/backend/app.py",
        ),
        Issue(
            number=-2,
            title="Add tests for approval-gated GitHub writes",
            body="backend/tests/test_app.py should verify that code findings cannot be posted until a maintainer approves them.",
            author="RepoPilot",
            labels=["testing", "code-scan"],
            url="https://github.com/demo/repopilot/blob/main/backend/tests/test_app.py",
        ),
    ]


async def github_scan_code(owner: str, repo: str, token: str | None) -> list[Issue]:
    if not token:
        return demo_issues()
    headers = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=20) as client:
        repo_response = await client.get(f"https://api.github.com/repos/{owner}/{repo}", headers=headers)
    if repo_response.status_code == 401:
        raise HTTPException(401, "GitHub token was rejected. Check that the token is complete and not expired.")
    if repo_response.status_code == 403:
        raise HTTPException(403, f"GitHub token cannot read {owner}/{repo}. Give it Metadata read and Contents read access.")
    if repo_response.status_code == 404:
        raise HTTPException(404, f"Repository {owner}/{repo} was not found or the token was not granted access to it.")
    repo_response.raise_for_status()
    branch = repo_response.json().get("default_branch", "main")

    async with httpx.AsyncClient(timeout=30) as client:
        tree_response = await client.get(
            f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}",
            headers=headers,
            params={"recursive": "1"},
        )
        tree_response.raise_for_status()
        paths = [
            item["path"]
            for item in tree_response.json().get("tree", [])
            if item.get("type") == "blob" and is_scannable(item.get("path", "")) and item.get("size", 0) <= 80_000
        ][:12]
        files = []
        for path in paths:
            content_response = await client.get(f"https://api.github.com/repos/{owner}/{repo}/contents/{path}", headers=headers, params={"ref": branch})
            if content_response.status_code == 200:
                data = content_response.json()
                if data.get("encoding") == "base64":
                    text = base64.b64decode(data.get("content", "")).decode("utf-8", errors="ignore")
                    files.append({"path": path, "text": text[:12_000]})
    return await find_code_issues(owner, repo, branch, files)


def is_scannable(path: str) -> bool:
    allowed = (".py", ".js", ".jsx", ".ts", ".tsx", ".md", ".yml", ".yaml", ".json")
    blocked = ("node_modules/", "dist/", "build/", ".venv/", "package-lock.json")
    return path.endswith(allowed) and not any(part in path for part in blocked)


async def find_code_issues(owner: str, repo: str, branch: str, files: list[dict[str, str]]) -> list[Issue]:
    try:
        if not getenv("GROQ_API_KEY") or not files:
            raise RuntimeError("Using deterministic code scanner")
        from langchain_core.output_parsers import JsonOutputParser
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_groq import ChatGroq

        snippets = "\n\n".join(f"FILE: {item['path']}\n{item['text'][:4000]}" for item in files[:6])
        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", "Find up to 5 concrete code issues worth opening as GitHub issues. Return JSON list with title, body, labels, path, confidence."),
                ("human", "Repository {repo} code snippets:\n\n{snippets}"),
            ]
        )
        chain = prompt | ChatGroq(model=getenv("GROQ_MODEL", "llama-3.1-8b-instant"), temperature=0) | JsonOutputParser()
        data = await chain.ainvoke({"repo": f"{owner}/{repo}", "snippets": snippets}, config={"tags": ["repopilot", "code-scan"]})
        return findings_to_issues(owner, repo, branch, data[:5])
    except Exception:
        findings = []
        for item in files:
            text = item["text"]
            path = item["path"]
            if "TODO" in text or "FIXME" in text:
                findings.append({"title": f"Resolve TODO/FIXME markers in {path}", "body": f"`{path}` contains TODO/FIXME markers that should be converted into tracked work or resolved.", "labels": ["maintenance", "code-scan"], "path": path, "confidence": 0.72})
            if "except Exception" in text or "except:" in text:
                findings.append({"title": f"Tighten broad exception handling in {path}", "body": f"`{path}` uses broad exception handling. Narrow the exception type or preserve enough context for debugging.", "labels": ["bug", "code-quality", "code-scan"], "path": path, "confidence": 0.78})
            if "eval(" in text:
                findings.append({"title": f"Review eval usage in {path}", "body": f"`{path}` calls `eval`, which can create code execution risk if any input is user-controlled.", "labels": ["security", "code-scan"], "path": path, "confidence": 0.86})
            if "dangerouslySetInnerHTML" in text or ".innerHTML" in text:
                findings.append({"title": f"Review HTML injection surface in {path}", "body": f"`{path}` writes HTML directly. Confirm content is trusted or sanitized.", "labels": ["security", "frontend", "code-scan"], "path": path, "confidence": 0.82})
            if len(findings) >= 5:
                break
        if not findings:
            findings.append({"title": "Add automated tests around core repository workflows", "body": "RepoPilot did not find obvious code smells in the sampled files. The next useful improvement is adding or expanding tests around the main workflow.", "labels": ["testing", "code-scan"], "path": files[0]["path"] if files else "repository", "confidence": 0.58})
        return findings_to_issues(owner, repo, branch, findings[:5])


def findings_to_issues(owner: str, repo: str, branch: str, findings: list[dict[str, Any]]) -> list[Issue]:
    issues = []
    for index, finding in enumerate(findings, start=1):
        path = finding.get("path", "repository")
        issues.append(
            Issue(
                number=-index,
                title=finding.get("title", "Review code finding"),
                body=f"{finding.get('body', '')}\n\nSource: `{path}`\nConfidence: {finding.get('confidence', 0.6)}",
                author="RepoPilot",
                labels=finding.get("labels", ["code-scan"]),
                url=f"https://github.com/{owner}/{repo}/blob/{branch}/{path}",
            )
        )
    return issues


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
                ("system", "You are RepoPilot. Return JSON: category, priority, labels, response, rationale, confidence for a GitHub issue draft."),
                ("human", "Code finding: {title}\n\n{body}\nSuggested labels: {labels}"),
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
    issues = await github_scan_code(payload.owner, payload.repo, token)
    message = f"Found {len(issues)} code finding(s)."
    if len(issues) == 0:
        message = "This repository is accessible, but RepoPilot did not find code issues in the sampled files."
    return {"repo": f"{payload.owner}/{payload.repo}", "demo_mode": token is None, "issues": issues, "message": message}


@app.post("/api/repos/list")
async def list_repos(payload: RepoConnect) -> dict[str, Any]:
    token = token_from(payload.github_token)
    repos = await github_list_repos(token)
    return {"demo_mode": token is None, "repos": repos}


@app.post("/api/autonomous/run")
async def autonomous_run(payload: RepoConnect) -> dict[str, Any]:
    token = token_from(payload.github_token)
    repos = await github_list_repos(token)
    if not repos:
        raise HTTPException(404, "No accessible repositories were found for this token.")
    targets = repos[:10] if payload.scan_all else repos[:1]
    approvals = []
    findings = []
    groups = []
    for target in targets:
        repo_findings = await github_scan_code(target["owner"], target["repo"], token)
        repo_approvals = [await run_triage(target["owner"], target["repo"], issue) for issue in repo_findings[:5]]
        findings.extend(repo_findings)
        approvals.extend(repo_approvals)
        groups.append({"repo": target["full_name"], "issues": repo_findings, "approvals": repo_approvals})
    repo_label = "all accessible repositories" if payload.scan_all else targets[0]["full_name"]
    return {
        "repo": targets[0]["full_name"],
        "demo_mode": token is None,
        "issues": findings,
        "approvals": approvals,
        "groups": groups,
        "message": f"Autonomous scan checked {repo_label} and created {len(approvals)} approval item(s).",
    }


@app.post("/api/repos/{owner}/{repo}/triage")
async def triage(owner: str, repo: str, payload: RepoConnect) -> list[Approval]:
    issues = await github_scan_code(owner, repo, token_from(payload.github_token))
    if not issues:
        return []
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
            response = await client.post(
                f"https://api.github.com/repos/{owner}/{repo}/issues",
                headers=headers,
                json={"title": approval.result.issue.title, "body": approval.result.response, "labels": approval.result.labels},
            )
            if response.status_code == 422 and approval.result.labels:
                response = await client.post(
                    f"https://api.github.com/repos/{owner}/{repo}/issues",
                    headers=headers,
                    json={"title": approval.result.issue.title, "body": approval.result.response},
                )
            response.raise_for_status()
            approval.posted_url = response.json().get("html_url")
    else:
        approval.posted_url = f"https://github.com/{approval.repo}/issues/new?title={approval.result.issue.title}"
    approval.status = Status.posted
    return store.save(approval)


@app.post("/api/qa")
async def qa(payload: Question) -> dict[str, Any]:
    answer = "RepoPilot uses LangGraph for issue triage, a human approval gate before writes, and LangSmith tracing through LANGSMITH_TRACING=true."
    return {"answer": answer, "sources": ["README.md", "backend/app.py"]}


@app.get("/api/history")
def history() -> list[dict[str, Any]]:
    return store.history[:50]
