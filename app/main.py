"""FastAPI application entry point: ``uvicorn app.main:app``."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse

from app import database, services, worker
from app.api import router
from app.config import settings
from app.docs import custom_openapi, swagger_ui_with_summaries

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
    docs_url=None,  # replaced by the customised page below
)
app.include_router(router)
app.openapi = custom_openapi(app)


@app.get("/docs", include_in_schema=False)
def docs() -> HTMLResponse:
    """Swagger UI, with each schema's description shown above its box."""
    return swagger_ui_with_summaries(app)


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    """Send visitors of the bare URL to the interactive API docs."""
    return RedirectResponse(url="/docs")


# @app.get("/health", tags=["meta"])
# def health() -> dict[str, str]:
#     return {"status": "ok"}