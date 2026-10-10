"""Certificate generation (the PDF itself and the job processing)."""

from datetime import date

from pypdf import PdfReader

from app import database, services
from app.config import settings
from app.generator import CertificateData, render_certificate
from app.models import Certificate
from tests.conftest import make_payload


def _pdf_text(path) -> str:
    return " ".join(page.extract_text() for page in PdfReader(path).pages)


def test_render_certificate_contains_recipient_details(tmp_path):
    path = render_certificate(
        CertificateData(
            certificate_id="abc-123",
            recipient_name="Asha Rao",
            title="Python for Data Science",
            issuer="Acme Academy",
            issue_date=date(2026, 10, 1),
        ),
        tmp_path / "out" / "cert.pdf",
    )

    assert path.read_bytes().startswith(b"%PDF")
    assert len(PdfReader(path).pages) == 1
    text = _pdf_text(path)
    for expected in ["Asha Rao", "Python for Data Science", "Acme Academy", "01 October 2026", "abc-123"]:
        assert expected in text
    assert not (tmp_path / "out" / "cert.pdf.tmp").exists()


def test_render_handles_very_long_name_and_title(tmp_path):
    path = render_certificate(
        CertificateData("id", "W" * 100, "Very long course title " * 10, "Issuer", date(2026, 1, 1)),
        tmp_path / "long.pdf",
    )
    assert "W" * 100 in _pdf_text(path)


def test_processing_job_generates_a_pdf_per_valid_recipient(client):
    body = client.post("/jobs", json=make_payload()).json()

    assert body["status"] == "completed"
    assert body["progress"]["generated"] == 3
    assert body["started_at"] is not None and body["finished_at"] is not None

    with database.SessionLocal() as db:
        certs = db.query(Certificate).filter_by(job_id=body["id"]).all()
        for cert in certs:
            path = settings.storage_dir / cert.file_path
            assert path.is_file()
            assert cert.name in _pdf_text(path)
            assert cert.generated_at is not None


def test_process_job_is_idempotent(client):
    job_id = client.post("/jobs", json=make_payload()).json()["id"]
    services.process_job(job_id)  # already completed -> no-op
    assert client.get(f"/jobs/{job_id}").json()["progress"]["generated"] == 3
