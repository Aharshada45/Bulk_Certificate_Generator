"""Input validation: whole-request errors (422) vs. per-recipient errors."""

import pytest

from app.config import settings
from app.validation import validate_recipients
from tests.conftest import make_payload


# ---------------------------------------------------------------- request level


@pytest.mark.parametrize(
    "overrides",
    [
        {"title": ""},
        {"title": "   "},  # whitespace only is stripped to empty
        {"issuer": ""},
        {"issue_date": "not-a-date"},
        {"recipients": []},
        {"recipients": "Asha"},
    ],
)
def test_invalid_job_fields_reject_whole_request(paused_client, overrides):
    response = paused_client.post("/jobs", json=make_payload(**overrides))
    assert response.status_code == 422
    assert paused_client.submitted == []


def test_missing_required_fields_rejected(paused_client):
    assert paused_client.post("/jobs", json={"recipients": [{"name": "A"}]}).status_code == 422


def test_too_many_recipients_rejected(paused_client, monkeypatch):
    monkeypatch.setattr(settings, "max_recipients", 2)
    response = paused_client.post("/jobs", json=make_payload([{"name": "A"}, {"name": "B"}, {"name": "C"}]))
    assert response.status_code == 422
    assert "at most 2 recipients" in response.text


def test_csv_without_name_column_rejected(paused_client):
    response = paused_client.post(
        "/jobs/csv",
        data={"title": "T", "issuer": "I"},
        files={"file": ("r.csv", b"email\na@example.com\n", "text/csv")},
    )
    assert response.status_code == 422
    assert "name" in response.json()["detail"]


def test_csv_must_be_utf8(paused_client):
    response = paused_client.post(
        "/jobs/csv",
        data={"title": "T", "issuer": "I"},
        files={"file": ("r.csv", "name\nJosé\n".encode("utf-16"), "text/csv")},
    )
    assert response.status_code == 422


def test_csv_too_large_rejected(paused_client, monkeypatch):
    monkeypatch.setattr(settings, "max_csv_bytes", 10)
    response = paused_client.post(
        "/jobs/csv",
        data={"title": "T", "issuer": "I"},
        files={"file": ("r.csv", b"name\nAsha Rao\nRavi Kumar\n", "text/csv")},
    )
    assert response.status_code == 413


def test_csv_with_only_header_rejected(paused_client):
    response = paused_client.post(
        "/jobs/csv",
        data={"title": "T", "issuer": "I"},
        files={"file": ("r.csv", b"name,email\n", "text/csv")},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------- recipient level


def test_invalid_recipients_do_not_block_valid_ones(client):
    recipients = [
        {"name": "Asha Rao", "email": "asha@example.com"},
        {"name": "", "email": "blank@example.com"},
        {"name": "Ravi", "email": "not-an-email"},
        {"name": "Asha Again", "email": "ASHA@example.com"},
        "just a string",
        {"email": "noname@example.com"},
        {"name": "x" * 101},
        {"name": "Meera Iyer"},
    ]
    body = client.post("/jobs", json=make_payload(recipients)).json()

    assert body["status"] == "completed_with_errors"
    assert body["progress"]["generated"] == 2
    assert body["progress"]["invalid"] == 6

    items = client.get(f"/jobs/{body['id']}/certificates").json()["items"]
    errors = {item["position"]: item["error"] for item in items if item["status"] == "invalid"}
    assert "name" in errors[1]
    assert "valid email" in errors[2]
    assert "duplicate" in errors[3]
    assert "must be an object" in errors[4]
    assert "name" in errors[5] and "required" in errors[5].lower()
    assert "at most 100" in errors[6]
    # Invalid rows keep whatever was submitted so the client can identify them.
    assert items[2]["name"] == "Ravi" and items[2]["email"] == "not-an-email"


def test_all_recipients_invalid_marks_job_failed(client):
    body = client.post("/jobs", json=make_payload([{"name": ""}, {"email": "x"}])).json()
    assert body["status"] == "failed"
    assert body["progress"]["invalid"] == 2


def test_validate_recipients_normalizes_values():
    [result] = validate_recipients([{"name": "  Asha Rao  ", "email": "  ", "extra": 1}])
    assert result.is_valid
    assert result.name == "Asha Rao"
    assert result.email is None


def test_same_name_without_email_is_allowed():
    results = validate_recipients([{"name": "John Smith"}, {"name": "John Smith"}])
    assert all(r.is_valid for r in results)
