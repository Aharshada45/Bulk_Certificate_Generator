import pytest
from fastapi.testclient import TestClient

from app import database, services, worker
from app.config import settings
from app.main import app


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Every test gets its own SQLite database and storage directory."""
    database.configure(f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(settings, "storage_dir", tmp_path / "storage")
    database.init_db()
    yield
    worker.shutdown(wait=True)
    database.engine.dispose()


@pytest.fixture
def client(monkeypatch):
    """Client whose jobs are processed synchronously inside the request,
    so tests can assert on the final state without polling."""
    monkeypatch.setattr(worker, "submit_job", services.process_job)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def paused_client(monkeypatch):
    """Client that accepts jobs but never processes them (to inspect the
    pending state)."""
    submitted: list[str] = []
    monkeypatch.setattr(worker, "submit_job", submitted.append)
    with TestClient(app) as c:
        c.submitted = submitted
        yield c


@pytest.fixture
def background_client():
    """Client using the real background thread pool."""
    with TestClient(app) as c:
        yield c


def make_payload(recipients=None, **overrides):
    payload = {
        "title": "Python for Data Science",
        "issuer": "Acme Academy",
        "issue_date": "2026-10-01",
        "recipients": recipients
        if recipients is not None
        else [
            {"name": "Asha Rao", "email": "asha@example.com"},
            {"name": "Ravi Kumar", "email": "ravi@example.com"},
            {"name": "Meera Iyer"},
        ],
    }
    payload.update(overrides)
    return payload
