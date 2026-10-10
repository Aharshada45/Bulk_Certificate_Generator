"""FastAPI application entry point: ``uvicorn app.main:app``."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import database, services, worker
from app.api import router
from app.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    database.init_db()
    # Resume jobs that were interrupted by a previous shutdown or crash.
    with database.SessionLocal() as db:
        for job_id in services.unfinished_job_ids(db):
            logger.info("Resuming unfinished job %s", job_id)
            worker.submit_job(job_id)
    yield
    worker.shutdown(wait=True)


app = FastAPI(
    title="Bulk Certificate Generator",
    description="Submit a list of recipients, generate PDF certificates in the background, "
    "track progress, and download the results.",
    version="1.0.0",
    lifespan=lifespan,
)
app.include_router(router)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}
