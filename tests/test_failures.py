"""A failure generating one certificate must not affect the others."""

import pytest

from app import generator
from tests.conftest import make_payload


@pytest.fixture
def flaky_generator(monkeypatch):
    """Make rendering fail for any recipient called 'Broken'."""
    real_render = generator.render_certificate
    state = {"broken": True}

    def render(data, output_path):
        if state["broken"] and data.recipient_name.startswith("Broken"):
            raise RuntimeError("simulated rendering failure")
        return real_render(data, output_path)

    monkeypatch.setattr(generator, "render_certificate", render)
    return state


def test_one_failure_does_not_stop_other_certificates(client, flaky_generator):
    recipients = [{"name": "Asha Rao"}, {"name": "Broken One"}, {"name": "Meera Iyer"}]
    body = client.post("/jobs", json=make_payload(recipients)).json()

    assert body["status"] == "completed_with_errors"
    assert body["progress"]["generated"] == 2
    assert body["progress"]["failed"] == 1

    items = client.get(f"/jobs/{body['id']}/certificates").json()["items"]
    assert [i["status"] for i in items] == ["generated", "failed", "generated"]
    assert items[1]["error"] == "RuntimeError: simulated rendering failure"
    assert items[1]["download_url"] is None


def test_job_failed_when_every_certificate_fails(client, flaky_generator):
    body = client.post("/jobs", json=make_payload([{"name": "Broken A"}, {"name": "Broken B"}])).json()
    assert body["status"] == "failed"
    assert body["progress"]["failed"] == 2


def test_retry_regenerates_only_failed_certificates(client, flaky_generator):
    recipients = [{"name": "Asha Rao"}, {"name": "Broken One"}, {"name": ""}]
    job_id = client.post("/jobs", json=make_payload(recipients)).json()["id"]
    flaky_generator["broken"] = False  # the underlying problem is fixed

    body = client.post(f"/jobs/{job_id}/retry").json()

    assert body["progress"]["generated"] == 2
    assert body["progress"]["failed"] == 0
    assert body["progress"]["invalid"] == 1  # invalid input is never retried
    assert body["status"] == "completed_with_errors"


def test_retry_rejected_while_job_running(paused_client):
    job_id = paused_client.post("/jobs", json=make_payload()).json()["id"]
    assert paused_client.post(f"/jobs/{job_id}/retry").status_code == 409


def test_unexpected_job_level_error_marks_job_failed(client, monkeypatch):
    from app import services

    def explode(*args, **kwargs):
        raise RuntimeError("database went away")

    monkeypatch.setattr(services, "_generate_one", explode)
    body = client.post("/jobs", json=make_payload()).json()
    assert body["status"] == "failed"
    assert body["error"] == "RuntimeError: database went away"
