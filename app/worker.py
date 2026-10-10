"""In-process background worker.

Jobs are handed to a small thread pool so the API can answer immediately with
``202 Accepted`` while certificates are generated in the background. Job state
lives in the database, not in memory, so jobs interrupted by a restart are
picked up again on startup (see ``app.main.lifespan``).

To scale beyond one process, ``submit_job`` is the single place to change: it
could enqueue to Celery/RQ instead, with ``services.process_job`` as the task.
"""

import logging
from concurrent.futures import ThreadPoolExecutor

from app import services
from app.config import settings

logger = logging.getLogger(__name__)

_executor: ThreadPoolExecutor | None = None


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(
            max_workers=settings.worker_threads, thread_name_prefix="cert-worker"
        )
    return _executor


def _run(job_id: str) -> None:
    try:
        services.process_job(job_id)
    except Exception:  # never let an exception vanish silently inside the pool
        logger.exception("Unhandled error while processing job %s", job_id)


def submit_job(job_id: str) -> None:
    _get_executor().submit(_run, job_id)


def shutdown(wait: bool = True) -> None:
    global _executor
    if _executor is not None:
        _executor.shutdown(wait=wait, cancel_futures=True)
        _executor = None
