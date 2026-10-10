"""Request/response models.

Validation happens at two levels:

* Job level (``JobCreate``): problems with the request as a whole - missing
  title/issuer, empty or oversized recipient list. These reject the whole
  request with HTTP 422.
* Recipient level (``RecipientIn``): problems with a single recipient. These do
  NOT reject the request; the recipient is stored as ``invalid`` with a reason
  and the remaining recipients are still generated.
"""

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.config import settings
from app.models import CertificateStatus, JobStatus

NAME_MAX_LENGTH = 100


class RecipientIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    name: str = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    email: EmailStr | None = None

    @field_validator("email", mode="before")
    @classmethod
    def blank_email_is_none(cls, value: Any) -> Any:
        # CSV files produce "" for an empty cell; treat it as "not provided".
        if isinstance(value, str) and not value.strip():
            return None
        return value


class JobCreate(BaseModel):
    """The data needed to start a certificate job: course details plus the list of recipients."""
    model_config = ConfigDict(
        str_strip_whitespace=True,
        json_schema_extra={
            "example": {
                "title": "Python for Data Science",
                "issuer": "Acme Academy",
                "issue_date": "2026-10-10",
                "recipients": [
                    {"name": "Asha Rao", "email": "asha@example.com"},
                    {"name": "Ravi Kumar", "email": "ravi@example.com"},
                ],
            }
        },
    )

    title: str = Field(min_length=1, max_length=200, description="Course or event name")
    issuer: str = Field(min_length=1, max_length=200, description="Issuing organization")
    issue_date: date | None = Field(default=None, description="Defaults to today")
    # Items are deliberately untyped here: each one is validated individually by
    # RecipientIn so that one bad entry doesn't reject the whole batch.
    recipients: list[Any] = Field(min_length=1)

    @field_validator("recipients")
    @classmethod
    def limit_batch_size(cls, value: list[Any]) -> list[Any]:
        if len(value) > settings.max_recipients:
            raise ValueError(f"a job may contain at most {settings.max_recipients} recipients")
        return value


class Progress(BaseModel):
    """Counts of how many certificates are pending, generated, failed or invalid, and the percent complete."""
    total: int
    pending: int
    generated: int
    failed: int
    invalid: int
    processed: int
    percent_complete: float


class JobLinks(BaseModel):
    """Ready-made URLs for a job's details, its certificate list and its ZIP download."""
    self: str
    certificates: str
    download: str


class JobOut(BaseModel):
    """A job's summary: its details, current status and progress."""
    id: str
    title: str
    issuer: str
    issue_date: date
    status: JobStatus
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    progress: Progress
    links: JobLinks


class CertificateOut(BaseModel):
    """The result of one recipient's certificate: its status, error and download link."""
    id: str
    job_id: str
    position: int
    name: str
    email: str | None
    status: CertificateStatus
    error: str | None
    generated_at: datetime | None
    download_url: str | None


class CertificateList(BaseModel):
    """A page of recipient results for a job, with the total count."""
    items: list[CertificateOut]
    total: int
    limit: int
    offset: int
