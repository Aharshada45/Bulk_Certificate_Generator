"""Creating generation jobs."""

from app import database
from app.models import Certificate, Job
from tests.conftest import make_payload


def test_create_job_returns_202_with_job_summary(paused_client):
    response = paused_client.post("/jobs", json=make_payload())

    assert response.status_code == 202
    body = response.json()
    assert response.headers["location"] == f"/jobs/{body['id']}"
    assert body["status"] == "pending"
    assert body["title"] == "Python for Data Science"
    assert body["issuer"] == "Acme Academy"
    assert body["issue_date"] == "2026-10-01"
    assert body["progress"]["total"] == 3
    assert body["progress"]["pending"] == 3
    assert body["links"]["download"] == f"/jobs/{body['id']}/download"
    # The job was handed to the worker exactly once.
    assert paused_client.submitted == [body["id"]]


def test_create_job_persists_one_row_per_recipient(paused_client):
    job_id = paused_client.post("/jobs", json=make_payload()).json()["id"]

    with database.SessionLocal() as db:
        job = db.get(Job, job_id)
        assert [c.name for c in job.certificates] == ["Asha Rao", "Ravi Kumar", "Meera Iyer"]
        assert [c.position for c in job.certificates] == [0, 1, 2]
        assert db.query(Certificate).count() == 3


def test_issue_date_defaults_to_today(paused_client):
    from datetime import date

    payload = make_payload()
    del payload["issue_date"]
    body = paused_client.post("/jobs", json=payload).json()
    assert body["issue_date"] == date.today().isoformat()


def test_get_unknown_job_returns_404(client):
    assert client.get("/jobs/does-not-exist").status_code == 404


def test_list_jobs_newest_first(client):
    first = client.post("/jobs", json=make_payload(title="First")).json()["id"]
    second = client.post("/jobs", json=make_payload(title="Second")).json()["id"]

    ids = [job["id"] for job in client.get("/jobs").json()]
    assert ids == [second, first]


def test_create_job_from_csv(client):
    csv_content = "Name,Email\nAsha Rao,asha@example.com\n\nRavi Kumar,\nBad Email,nope\n"
    response = client.post(
        "/jobs/csv",
        data={"title": "Workshop", "issuer": "Acme", "issue_date": "2026-10-01"},
        files={"file": ("recipients.csv", csv_content.encode(), "text/csv")},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "completed_with_errors"
    assert body["progress"] == {
        "total": 3, "pending": 0, "generated": 2, "failed": 0, "invalid": 1,
        "processed": 3, "percent_complete": 100.0,
    }


def test_csv_with_excel_bom_is_accepted(client):
    csv_content = "﻿name,email\nAsha Rao,asha@example.com\n".encode()
    response = client.post(
        "/jobs/csv",
        data={"title": "Workshop", "issuer": "Acme"},
        files={"file": ("r.csv", csv_content, "text/csv")},
    )
    assert response.status_code == 202
    assert response.json()["progress"]["generated"] == 1
