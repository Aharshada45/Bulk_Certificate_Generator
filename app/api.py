"""HTTP routes."""

import csv
import io
import re
import zipfile
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import services, worker
from app.config import settings
from app.database import get_db
from app.models import TERMINAL_JOB_STATUSES, Certificate, CertificateStatus, Job
from app.schemas import CertificateList, CertificateOut, JobCreate, JobLinks, JobOut

router = APIRouter()

DbSession = Annotated[Session, Depends(get_db)]


# --------------------------------------------------------------------------- helpers


def _job_out(db: Session, job: Job) -> JobOut:
    base = f"/jobs/{job.id}"
    return JobOut(
        id=job.id,
        title=job.title,
        issuer=job.issuer,
        issue_date=job.issue_date,
        status=job.status,
        error=job.error,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        progress=services.get_progress(db, job.id),
        links=JobLinks(self=base, certificates=f"{base}/certificates", download=f"{base}/download"),
    )


def _certificate_out(cert: Certificate) -> CertificateOut:
    return CertificateOut(
        id=cert.id,
        job_id=cert.job_id,
        position=cert.position,
        name=cert.name,
        email=cert.email,
        status=cert.status,
        error=cert.error,
        generated_at=cert.generated_at,
        download_url=(
            f"/jobs/{cert.job_id}/certificates/{cert.id}/download"
            if cert.status == CertificateStatus.GENERATED
            else None
        ),
    )


def _get_job_or_404(db: Session, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return job


def _submit(db: Session, payload: JobCreate, response: Response) -> JobOut:
    job = services.create_job(db, payload)
    worker.submit_job(job.id)
    db.refresh(job)  # the worker may already have started on it
    response.headers["Location"] = f"/jobs/{job.id}"
    return _job_out(db, job)


def _safe_filename(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")[:50] or "certificate"


# --------------------------------------------------------------------------- jobs


@router.post("/jobs", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED, tags=["jobs"])
def create_job(payload: JobCreate, response: Response, db: DbSession) -> JobOut:
    """Submit a bulk generation request. Returns immediately; poll the job for progress."""
    return _submit(db, payload, response)


@router.post("/jobs/csv", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED, tags=["jobs"])
async def create_job_from_csv(
    response: Response,
    db: DbSession,
    file: Annotated[UploadFile, File(description="CSV with a 'name' column and optional 'email' column")],
    title: Annotated[str, Form()],
    issuer: Annotated[str, Form()],
    issue_date: Annotated[date | None, Form()] = None,
) -> JobOut:
    """Same as ``POST /jobs`` but recipients come from an uploaded CSV file."""
    content = await file.read(settings.max_csv_bytes + 1)
    if len(content) > settings.max_csv_bytes:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "CSV file is too large")
    try:
        text = content.decode("utf-8-sig")  # tolerate the BOM Excel adds
    except UnicodeDecodeError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "CSV must be UTF-8 encoded")

    reader = csv.DictReader(io.StringIO(text))
    headers = {(h or "").strip().lower() for h in reader.fieldnames or []}
    if "name" not in headers:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "CSV must have a 'name' column")

    recipients = []
    for row in reader:
        normalized = {(k or "").strip().lower(): v for k, v in row.items() if isinstance(v, str)}
        if not any(v.strip() for v in normalized.values()):
            continue  # skip blank lines
        recipients.append(normalized)

    try:
        payload = JobCreate(title=title, issuer=issuer, issue_date=issue_date, recipients=recipients)
    except ValidationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            jsonable_encoder(exc.errors(include_url=False, include_context=False)),
        )
    return _submit(db, payload, response)


@router.get("/jobs", response_model=list[JobOut], tags=["jobs"])
def list_jobs(
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[JobOut]:
    jobs = db.scalars(select(Job).order_by(Job.created_at.desc()).limit(limit).offset(offset))
    return [_job_out(db, job) for job in jobs]


@router.get("/jobs/{job_id}", response_model=JobOut, tags=["jobs"])
def get_job(job_id: str, db: DbSession) -> JobOut:
    """Job status and progress counts (cheap enough to poll)."""
    return _job_out(db, _get_job_or_404(db, job_id))


@router.post("/jobs/{job_id}/retry", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED, tags=["jobs"])
def retry_job(job_id: str, db: DbSession) -> JobOut:
    """Re-attempt certificates whose generation failed (invalid input is not retried)."""
    job = _get_job_or_404(db, job_id)
    if job.status not in TERMINAL_JOB_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, "Job is still running")
    if services.retry_failed(db, job):
        worker.submit_job(job.id)
        db.refresh(job)
    return _job_out(db, job)


# --------------------------------------------------------------------------- certificates


@router.get("/jobs/{job_id}/certificates", response_model=CertificateList, tags=["certificates"])
def list_certificates(
    job_id: str,
    db: DbSession,
    status_filter: Annotated[CertificateStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CertificateList:
    """Per-recipient results. Filter with ``?status=failed`` / ``invalid`` / ``generated``."""
    _get_job_or_404(db, job_id)
    query = select(Certificate).where(Certificate.job_id == job_id)
    if status_filter is not None:
        query = query.where(Certificate.status == status_filter)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    certs = db.scalars(query.order_by(Certificate.position).limit(limit).offset(offset))
    return CertificateList(
        items=[_certificate_out(c) for c in certs], total=total, limit=limit, offset=offset
    )


@router.get("/jobs/{job_id}/certificates/{certificate_id}", response_model=CertificateOut, tags=["certificates"])
def get_certificate(job_id: str, certificate_id: str, db: DbSession) -> CertificateOut:
    cert = db.get(Certificate, certificate_id)
    if cert is None or cert.job_id != job_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Certificate not found")
    return _certificate_out(cert)


@router.get(
    "/jobs/{job_id}/certificates/{certificate_id}/download",
    response_class=FileResponse,
    responses={200: {"content": {"application/pdf": {}}}},
    tags=["certificates"],
)
def download_certificate(job_id: str, certificate_id: str, db: DbSession) -> FileResponse:
    """Download a single generated certificate as PDF."""
    cert = db.get(Certificate, certificate_id)
    if cert is None or cert.job_id != job_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Certificate not found")
    if cert.status != CertificateStatus.GENERATED or not cert.file_path:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Certificate is not available (status: {cert.status})")
    path = settings.storage_dir / cert.file_path
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Certificate file is missing from storage")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=f"{_safe_filename(cert.name)}_{cert.id[:8]}.pdf",
    )


@router.get(
    "/jobs/{job_id}/download",
    response_class=StreamingResponse,
    responses={200: {"content": {"application/zip": {}}}},
    tags=["certificates"],
)
def download_job(job_id: str, db: DbSession) -> StreamingResponse:
    """Download every generated certificate of a finished job as one ZIP file."""
    job = _get_job_or_404(db, job_id)
    if job.status not in TERMINAL_JOB_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Job is not finished yet (status: {job.status})")
    certs = db.scalars(
        select(Certificate)
        .where(Certificate.job_id == job_id, Certificate.status == CertificateStatus.GENERATED)
        .order_by(Certificate.position)
    ).all()
    if not certs:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This job has no generated certificates")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for cert in certs:
            path = settings.storage_dir / cert.file_path
            if path.is_file():
                zf.write(path, f"{cert.position + 1:04d}_{_safe_filename(cert.name)}_{cert.id[:8]}.pdf")
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="certificates_{job.id}.zip"'},
    )
