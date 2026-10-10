"""Business logic: creating jobs, processing them, and reporting progress."""

import logging
from datetime import date

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app import database, generator
from app.config import settings
from app.models import (
    TERMINAL_JOB_STATUSES,
    Certificate,
    CertificateStatus,
    Job,
    JobStatus,
    utcnow,
)
from app.schemas import JobCreate, Progress
from app.validation import validate_recipients

logger = logging.getLogger(__name__)

MAX_ERROR_LENGTH = 500


def create_job(db: Session, payload: JobCreate) -> Job:
    """Persist a job plus one certificate row per recipient (valid or not)."""
    job = Job(
        title=payload.title,
        issuer=payload.issuer,
        issue_date=payload.issue_date or date.today(),
        status=JobStatus.PENDING,
    )
    for result in validate_recipients(payload.recipients):
        job.certificates.append(
            Certificate(
                position=result.position,
                name=result.name,
                email=result.email,
                status=CertificateStatus.PENDING if result.is_valid else CertificateStatus.INVALID,
                error=result.error,
            )
        )
    db.add(job)
    db.commit()
    return job


def get_progress(db: Session, job_id: str) -> Progress:
    """Counts are computed from the certificate rows, so they can never drift."""
    rows = db.execute(
        select(Certificate.status, func.count())
        .where(Certificate.job_id == job_id)
        .group_by(Certificate.status)
    ).all()
    counts = {status: count for status, count in rows}
    total = sum(counts.values())
    pending = counts.get(CertificateStatus.PENDING, 0)
    processed = total - pending
    return Progress(
        total=total,
        pending=pending,
        generated=counts.get(CertificateStatus.GENERATED, 0),
        failed=counts.get(CertificateStatus.FAILED, 0),
        invalid=counts.get(CertificateStatus.INVALID, 0),
        processed=processed,
        percent_complete=round(100 * processed / total, 2) if total else 100.0,
    )


def _final_status(progress: Progress) -> JobStatus:
    if progress.generated == progress.total:
        return JobStatus.COMPLETED
    if progress.generated == 0:
        return JobStatus.FAILED
    return JobStatus.COMPLETED_WITH_ERRORS


def _generate_one(db: Session, job: Job, cert: Certificate) -> None:
    """Generate one certificate. Errors are recorded on the row, never raised,
    so one bad certificate cannot stop the rest of the job."""
    relative_path = f"{job.id}/{cert.id}.pdf"
    try:
        generator.render_certificate(
            generator.CertificateData(
                certificate_id=cert.id,
                recipient_name=cert.name,
                title=job.title,
                issuer=job.issuer,
                issue_date=job.issue_date,
            ),
            settings.storage_dir / relative_path,
        )
    except Exception as exc:  # noqa: BLE001 - any failure is recorded per certificate
        logger.exception("Certificate %s (job %s) failed", cert.id, job.id)
        cert.status = CertificateStatus.FAILED
        cert.error = f"{type(exc).__name__}: {exc}"[:MAX_ERROR_LENGTH]
    else:
        cert.status = CertificateStatus.GENERATED
        cert.file_path = relative_path
        cert.generated_at = utcnow()
        cert.error = None
    # Commit after every certificate so progress is visible while the job runs
    # and finished work survives a crash.
    db.commit()


def process_job(job_id: str) -> None:
    """Generate every pending certificate of a job. Safe to call again on a
    partially processed job (e.g. after a restart): only pending rows are done."""
    with database.SessionLocal() as db:
        job = db.get(Job, job_id)
        if job is None or job.status in TERMINAL_JOB_STATUSES:
            return
        job.status = JobStatus.PROCESSING
        job.started_at = job.started_at or utcnow()
        db.commit()

        try:
            pending_ids = db.scalars(
                select(Certificate.id)
                .where(Certificate.job_id == job_id, Certificate.status == CertificateStatus.PENDING)
                .order_by(Certificate.position)
            ).all()
            for cert_id in pending_ids:
                _generate_one(db, job, db.get(Certificate, cert_id))
            job.status = _final_status(get_progress(db, job_id))
        except Exception as exc:  # e.g. database unavailable - the job itself can't continue
            logger.exception("Job %s aborted", job_id)
            db.rollback()
            job.status = JobStatus.FAILED
            job.error = f"{type(exc).__name__}: {exc}"[:MAX_ERROR_LENGTH]
        job.finished_at = utcnow()
        db.commit()
        logger.info("Job %s finished with status %s", job_id, job.status)


def retry_failed(db: Session, job: Job) -> int:
    """Reset ``failed`` certificates to ``pending`` so the worker tries again.
    Invalid recipients are not retried - their input has to be fixed."""
    result = db.execute(
        update(Certificate)
        .where(Certificate.job_id == job.id, Certificate.status == CertificateStatus.FAILED)
        .values(status=CertificateStatus.PENDING, error=None)
    )
    if result.rowcount:
        job.status = JobStatus.PENDING
        job.finished_at = None
        job.error = None
    db.commit()
    return result.rowcount


def unfinished_job_ids(db: Session) -> list[str]:
    """Jobs interrupted by a shutdown/crash, to be resumed on startup."""
    return list(
        db.scalars(
            select(Job.id)
            .where(Job.status.in_([JobStatus.PENDING, JobStatus.PROCESSING]))
            .order_by(Job.created_at)
        )
    )
