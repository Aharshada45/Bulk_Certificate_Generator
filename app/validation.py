"""Per-recipient validation."""

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.schemas import RecipientIn


@dataclass
class RecipientResult:
    position: int
    name: str
    email: str | None
    error: str | None  # None means the recipient is valid

    @property
    def is_valid(self) -> bool:
        return self.error is None


def _format_errors(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        field = ".".join(str(p) for p in err["loc"]) or "recipient"
        parts.append(f"{field}: {err['msg']}")
    return "; ".join(parts)


def _raw_str(raw: Any, key: str) -> str | None:
    """Best-effort copy of a raw field, so invalid rows can still be identified."""
    if isinstance(raw, dict) and raw.get(key) is not None:
        return str(raw[key]).strip()[:255]
    return None


def validate_recipients(raw_recipients: list[Any]) -> list[RecipientResult]:
    results: list[RecipientResult] = []
    seen_emails: set[str] = set()

    for position, raw in enumerate(raw_recipients):
        if not isinstance(raw, dict):
            results.append(RecipientResult(position, "", None, "recipient must be an object"))
            continue
        try:
            recipient = RecipientIn.model_validate(raw)
        except ValidationError as exc:
            results.append(
                RecipientResult(
                    position, _raw_str(raw, "name") or "", _raw_str(raw, "email"), _format_errors(exc)
                )
            )
            continue

        email = str(recipient.email) if recipient.email else None
        if email:
            key = email.lower()
            if key in seen_emails:
                results.append(
                    RecipientResult(position, recipient.name, email, "email: duplicate of an earlier recipient")
                )
                continue
            seen_emails.add(key)

        results.append(RecipientResult(position, recipient.name, email, None))
    return results
