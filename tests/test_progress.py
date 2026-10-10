"""Job status and progress tracking."""

from app import database, services
from app.models import Certificate, CertificateStatus, Job, JobStatus
from tests.conftest import make_payload


def test_job_is_pending_until_worker_picks_it_up(paused_client):
    job_id = paused_client.post("/jobs", json=make_payload()).json()["id"]

    body = paused_client.get(f"/jobs/{job_id}").json()
    assert body["status"] == "pending"
    assert body["started_at"] is None
    assert body["progress"]["percent_complete"] == 0.0

    services.process_job(job_id)

    body = paused_client.get(f"/jobs/{job_id}").json()
    assert body["status"] == "completed"
    assert body["progress"]["pending"] == 0
    assert body["progress"]["percent_complete"] == 100.0


def test_progress_reports_partial_completion(paused_client):
    job_id = paused_client.post("/jobs", json=make_payload()).json()["id"]
    # Simulate a worker that is part-way through the job.
    with database.SessionLocal() as db:
        db.get(Job, job_id).status = JobStatus.PROCESSING
        first = db.query(Certificate).filter_by(job_id=job_id, position=0).one()
        first.status = CertificateStatus.GENERATED
        db.commit()

    body = paused_client.get(f"/jobs/{job_id}").json()
    assert body["status"] == "processing"
    assert body["progress"] == {
        "total": 3, "pending": 2, "generated": 1, "failed": 0, "invalid": 0,
        "processed": 1, "percent_complete": 33.33,
    }


def test_certificate_list_filters_by_status_and_paginates(client):
    recipients = [{"name": f"Person {i}"} for i in range(5)] + [{"name": ""}]
    job_id = client.post("/jobs", json=make_payload(recipients)).json()["id"]

    generated = client.get(f"/jobs/{job_id}/certificates", params={"status": "generated"}).json()
    assert generated["total"] == 5
    assert all(item["download_url"] for item in generated["items"])

    invalid = client.get(f"/jobs/{job_id}/certificates", params={"status": "invalid"}).json()
    assert invalid["total"] == 1
    assert invalid["items"][0]["download_url"] is None

    page = client.get(f"/jobs/{job_id}/certificates", params={"limit": 2, "offset": 2}).json()
    assert page["total"] == 6
    assert [item["position"] for item in page["items"]] == [2, 3]


def test_certificate_list_rejects_unknown_status(client):
    job_id = client.post("/jobs", json=make_payload()).json()["id"]
    assert client.get(f"/jobs/{job_id}/certificates", params={"status": "bogus"}).status_code == 422


def test_unfinished_jobs_are_resumed_on_startup(paused_client, monkeypatch):
    """A job left 'processing' by a crash is picked up again when the app starts."""
    from fastapi.testclient import TestClient

    from app import worker
    from app.main import app

    job_id = paused_client.post("/jobs", json=make_payload()).json()["id"]
    with database.SessionLocal() as db:
        db.get(Job, job_id).status = JobStatus.PROCESSING  # interrupted mid-way
        db.commit()

    monkeypatch.setattr(worker, "submit_job", services.process_job)
    with TestClient(app) as restarted:
        assert restarted.get(f"/jobs/{job_id}").json()["status"] == "completed"


def test_real_background_worker_completes_job(background_client):
    """End-to-end with the real thread pool: POST returns immediately, then poll."""
    import time

    recipients = [{"name": f"Person {i}"} for i in range(20)]
    job_id = background_client.post("/jobs", json=make_payload(recipients)).json()["id"]

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        body = background_client.get(f"/jobs/{job_id}").json()
        if body["status"] not in ("pending", "processing"):
            break
        time.sleep(0.05)

    assert body["status"] == "completed"
    assert body["progress"]["generated"] == 20
