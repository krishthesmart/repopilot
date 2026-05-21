import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import app


client = TestClient(app)


def test_connect_repo_works_in_demo_mode():
    response = client.post("/api/repos/connect", json={"owner": "demo", "repo": "repopilot"})
    assert response.status_code == 200
    assert response.json()["demo_mode"] is True
    assert response.json()["issues"][0]["number"] == 42


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
