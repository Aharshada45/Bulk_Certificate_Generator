"""Retrieving generated certificates (single PDF and ZIP of a whole job)."""

import io
import zipfile

from app import database
from app.config import settings
from app.models import Certificate
from tests.conftest import make_payload


def _generated_items(client, job_id):
    return client.get(f"/jobs/{job_id}/certificates", params={"status": "generated"}).json()["items"]


def test_download_single_certificate(client):
    job_id = client.post("/jobs", json=make_payload()).json()["id"]
    cert = _generated_items(client, job_id)[0]

    response = client.get(cert["download_url"])

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "Asha_Rao" in response.headers["content-disposition"]
    assert response.content.startswith(b"%PDF")


def test_get_certificate_metadata(client):
    job_id = client.post("/jobs", json=make_payload()).json()["id"]
    cert = _generated_items(client, job_id)[0]

    body = client.get(f"/jobs/{job_id}/certificates/{cert['id']}").json()
    assert body["name"] == "Asha Rao"
    assert body["status"] == "generated"


def test_download_certificate_that_was_not_generated(client):
    job_id = client.post("/jobs", json=make_payload([{"name": "Asha"}, {"name": ""}])).json()["id"]
    invalid = client.get(f"/jobs/{job_id}/certificates", params={"status": "invalid"}).json()["items"][0]

    response = client.get(f"/jobs/{job_id}/certificates/{invalid['id']}/download")
    assert response.status_code == 409


def test_download_certificate_with_wrong_job_id_is_404(client):
    job_a = client.post("/jobs", json=make_payload()).json()["id"]
    job_b = client.post("/jobs", json=make_payload()).json()["id"]
    cert = _generated_items(client, job_a)[0]

    assert client.get(f"/jobs/{job_b}/certificates/{cert['id']}/download").status_code == 404
    assert client.get(f"/jobs/{job_a}/certificates/nope/download").status_code == 404


def test_download_when_file_missing_from_storage(client):
    job_id = client.post("/jobs", json=make_payload()).json()["id"]
    cert = _generated_items(client, job_id)[0]
    with database.SessionLocal() as db:
        (settings.storage_dir / db.get(Certificate, cert["id"]).file_path).unlink()

    assert client.get(cert["download_url"]).status_code == 404


def test_download_job_as_zip(client):
    recipients = [{"name": "Asha Rao"}, {"name": ""}, {"name": "Ravi Kumar"}]
    job_id = client.post("/jobs", json=make_payload(recipients)).json()["id"]

    response = client.get(f"/jobs/{job_id}/download")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        names = sorted(zf.namelist())
        assert len(names) == 2  # the invalid recipient is not included
        assert names[0].startswith("0001_Asha_Rao_")
        assert names[1].startswith("0003_Ravi_Kumar_")
        assert all(zf.read(n).startswith(b"%PDF") for n in names)


def test_download_zip_before_job_finished_is_409(paused_client):
    job_id = paused_client.post("/jobs", json=make_payload()).json()["id"]
    assert paused_client.get(f"/jobs/{job_id}/download").status_code == 409


def test_download_zip_with_nothing_generated_is_404(client):
    job_id = client.post("/jobs", json=make_payload([{"name": ""}])).json()["id"]
    assert client.get(f"/jobs/{job_id}/download").status_code == 404
