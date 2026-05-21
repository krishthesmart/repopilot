import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import Issue, app, classify_issue


client = TestClient(app)


def test_scan_repo_works_in_demo_mode():
    response = client.post("/api/repos/connect", json={"owner": "demo", "repo": "repopilot"})
    assert response.status_code == 200
    assert response.json()["demo_mode"] is True
    assert response.json()["issues"][0]["number"] == -1
    assert response.json()["message"] == "Found 2 code finding(s)."


def test_list_repos_works_in_demo_mode():
    response = client.post("/api/repos/list", json={})
    assert response.status_code == 200
    assert response.json()["repos"][0]["full_name"] == "demo/repopilot"


def test_autonomous_run_scans_first_repo():
    response = client.post("/api/autonomous/run", json={})
    assert response.status_code == 200
    assert response.json()["repo"] == "demo/repopilot"
    assert response.json()["approvals"]


def test_autonomous_run_can_scan_all_repos():
    response = client.post("/api/autonomous/run", json={"scan_all": True})
    assert response.status_code == 200
    assert response.json()["groups"][0]["repo"] == "demo/repopilot"
    assert response.json()["approvals"]


def test_code_finding_body_is_preserved():
    issue = Issue(
        number=-1,
        title="Review eval usage in generate.py",
        body="`generate.py` calls `eval`, which can create code execution risk.\n\nSource: `generate.py`\nConfidence: 0.86",
        labels=["security", "code-scan"],
    )

    import asyncio

    result = asyncio.run(classify_issue(issue))

    assert result.category == "security_review"
    assert result.priority == "high"
    assert "`generate.py` calls `eval`" in result.response
    assert "security" in result.labels


def test_write_action_requires_approval():
    triage = client.post("/api/repos/demo/repopilot/triage", json={"owner": "demo", "repo": "repopilot"})
    approval_id = triage.json()[0]["id"]

    response = client.post(f"/api/approvals/{approval_id}/post")
    assert response.status_code == 409


def test_approval_then_post_demo_action():
    triage = client.post("/api/repos/demo/repopilot/triage", json={"owner": "demo", "repo": "repopilot"})
    approval_id = triage.json()[0]["id"]

    decision = client.post(f"/api/approvals/{approval_id}/decision", json={"approved": True})
    posted = client.post(f"/api/approvals/{approval_id}/post")

    assert decision.json()["status"] == "approved"
    assert posted.json()["status"] == "posted"
