# Bulk Certificate Generator

A FastAPI backend that accepts a list of recipients, generates a PDF certificate for
each valid one from a single predefined template, tracks progress, and lets clients
download the results individually or as a ZIP.

![Sample certificate](docs/sample_certificate.png)

**Stack:** Python 3.11+ · FastAPI · SQLAlchemy 2 · SQLite (any SQLAlchemy-supported
relational DB via `DATABASE_URL`) · ReportLab (PDF) · pytest

---

## 1. Setup

```bash
git clone <repo-url> && cd Bulk_Certificate_Generator
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Configuration (environment variables, all optional)

| Variable         | Default                         | Meaning                                         |
|------------------|---------------------------------|-------------------------------------------------|
| `DATABASE_URL`   | `sqlite:///./certificates.db`   | SQLAlchemy database URL                         |
| `STORAGE_DIR`    | `./storage`                     | Where generated PDFs are written                |
| `MAX_RECIPIENTS` | `1000`                          | Max recipients per job                          |
| `MAX_CSV_BYTES`  | `1048576` (1 MB)                | Max CSV upload size                             |
| `WORKER_THREADS` | `2`                             | Jobs processed concurrently                     |

To use PostgreSQL instead, install a driver (`pip install psycopg[binary]`) and set
`DATABASE_URL=postgresql+psycopg://user:pass@localhost/certs`. No code changes needed.

## 2. Run the application

```bash
uvicorn app.main:app --reload
```

The tables and storage directory are created automatically on startup.
Interactive API docs (Swagger UI): <http://localhost:8000/docs>.

## 3. Run the tests

```bash
pytest
```

Tests use a fresh temporary SQLite database and storage directory per test.

| Test file                     | Covers                                                                   |
|-------------------------------|--------------------------------------------------------------------------|
| `tests/test_jobs.py`          | Creating jobs (JSON and CSV), listing, 404s                              |
| `tests/test_validation.py`    | Request-level 422s, per-recipient validation, CSV validation             |
| `tests/test_generation.py`    | PDF content (text extracted with pypdf), files written for each recipient |
| `tests/test_progress.py`      | Status transitions, progress counts, filtering, resume after restart, real background worker |
| `tests/test_failures.py`      | One certificate failing while others succeed, all-failed jobs, retry, job-level errors |
| `tests/test_retrieval.py`     | Downloading single PDFs and ZIPs, plus error cases                       |

## 4. Submitting a certificate generation request

### JSON

```bash
curl -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{
        "title": "Python for Data Science",
        "issuer": "Acme Academy",
        "issue_date": "2026-10-01",
        "recipients": [
          {"name": "Asha Rao",   "email": "asha@example.com"},
          {"name": "Ravi Kumar", "email": "ravi@example.com"},
          {"name": "",           "email": "broken@example.com"}
        ]
      }'
```

- `title` (course/event) and `issuer` are required. `issue_date` defaults to today.
- Each recipient needs a `name` (1–100 chars). `email` is optional but must be valid if given.

Response: **`202 Accepted`**, with a `Location: /jobs/{id}` header:

```json
{
  "id": "6f1c…",
  "status": "pending",
  "progress": {"total": 3, "pending": 2, "generated": 0, "failed": 0,
               "invalid": 1, "processed": 1, "percent_complete": 33.33},
  "links": {"self": "/jobs/6f1c…", "certificates": "/jobs/6f1c…/certificates",
            "download": "/jobs/6f1c…/download"},
  ...
}
```

### CSV upload

```bash
# recipients.csv
# name,email
# Asha Rao,asha@example.com
# Ravi Kumar,

curl -X POST http://localhost:8000/jobs/csv \
  -F title="Python for Data Science" -F issuer="Acme Academy" -F issue_date=2026-10-01 \
  -F file=@recipients.csv
```

The CSV needs a `name` column, and an `email` column is optional. Headers are
case-insensitive, other columns are ignored, blank lines are skipped, and the BOM that
Excel adds is handled.

## 5. Tracking progress

```bash
curl http://localhost:8000/jobs/{job_id}
```

| Job status               | Meaning                                              |
|--------------------------|------------------------------------------------------|
| `pending`                | Accepted, waiting for the worker                     |
| `processing`             | Certificates are being generated                     |
| `completed`              | Every recipient got a certificate                    |
| `completed_with_errors`  | At least one generated, and at least one invalid/failed |
| `failed`                 | Nothing could be generated                           |

To see per-recipient results, including **which ones failed and why**:

```bash
curl "http://localhost:8000/jobs/{job_id}/certificates"                  # all, paginated (limit/offset)
curl "http://localhost:8000/jobs/{job_id}/certificates?status=failed"    # generation errors
curl "http://localhost:8000/jobs/{job_id}/certificates?status=invalid"   # validation errors
```

Each item has `position` (its index in the submitted list), `name`, `email`, `status`
(`pending` / `generated` / `failed` / `invalid`), an `error` message, and a `download_url`.

To retry certificates whose *generation* failed (for example after a transient problem was
fixed), run `curl -X POST http://localhost:8000/jobs/{job_id}/retry`. Invalid recipients are
never retried, because their input has to be corrected and resubmitted.

## 6. Retrieving generated certificates

```bash
# One certificate (PDF) - use the download_url from the certificate list
curl -OJ http://localhost:8000/jobs/{job_id}/certificates/{certificate_id}/download

# Every generated certificate of a finished job as one ZIP
curl -OJ http://localhost:8000/jobs/{job_id}/download
```

ZIP entries are named `0001_Asha_Rao_<id>.pdf` (submission order, name, short id).
Error responses:

| Case | Response |
|---|---|
| Certificate not generated (invalid/failed/pending) | `409` |
| ZIP requested before the job finished | `409` |
| Job finished but nothing was generated | `404` |

## API summary

| Method | Path                                                   | Purpose                              |
|--------|--------------------------------------------------------|--------------------------------------|
| POST   | `/jobs`                                                | Create a job from JSON               |
| POST   | `/jobs/csv`                                            | Create a job from a CSV upload       |
| GET    | `/jobs`                                                | List jobs (newest first)             |
| GET    | `/jobs/{job_id}`                                       | Job status and progress              |
| POST   | `/jobs/{job_id}/retry`                                 | Retry failed certificates            |
| GET    | `/jobs/{job_id}/certificates`                          | Per-recipient results (`status`, `limit`, `offset`) |
| GET    | `/jobs/{job_id}/certificates/{id}`                     | One certificate's metadata           |
| GET    | `/jobs/{job_id}/certificates/{id}/download`            | Download one PDF                     |
| GET    | `/jobs/{job_id}/download`                              | Download all generated PDFs as ZIP   |
| GET    | `/health`                                              | Health check                         |

---

## 7. Design decisions

### Project layout

```
app/
  main.py        FastAPI app, startup (create tables, resume unfinished jobs)
  api.py         HTTP routes
  schemas.py     Pydantic request/response models
  validation.py  Per-recipient validation
  services.py    Job creation, processing loop, progress calculation, retry
  worker.py      Background thread pool
  generator.py   The certificate template (ReportLab) -> PDF
  models.py      SQLAlchemy models: Job 1-N Certificate
  database.py    Engine/session setup
  config.py      Environment-based settings
tests/
```

Routes stay thin. The business logic lives in `services.py` and the PDF rendering in
`generator.py`, so each can be tested and changed on its own.

### Data model

- **`jobs`**: the shared certificate info (title, issuer, issue date), status, timestamps,
  and a job-level error.
- **`certificates`**: one row per submitted recipient, *including invalid ones*. It stores
  `position`, name, email, status, error, and the PDF path.

Every recipient gets a row, even an invalid one, so the client can see exactly what happened
to each entry it sent. **Progress counts are computed with a `GROUP BY` over the certificate
rows** instead of being stored as counters on the job, so they can never drift from reality,
even if a worker crashes mid-update.

### Bulk processing: background worker (in-process thread pool)

`POST /jobs` validates the request, stores the job and its rows in one transaction, hands the
job id to a `ThreadPoolExecutor`, and returns `202 Accepted` right away. The client then polls
`GET /jobs/{id}`.

- **Why not synchronous?** A job can hold up to 1,000 recipients. Generating them all inside
  the request would make response time grow with the job size and risk HTTP timeouts. Async
  processing also makes progress tracking meaningful.
- **Why not Celery + Redis?** It's more robust at scale, but it adds two moving parts
  (a broker and separate worker processes) that this assignment doesn't need. The design keeps
  the swap easy: `worker.submit_job()` is the only hand-off point, and
  `services.process_job(job_id)` is already a self-contained, idempotent task function that
  only takes an id. Moving to Celery/RQ means changing `submit_job` to enqueue that function.
- **Durability:** all state lives in the database, not in memory. The worker commits after
  every certificate, so progress is visible live and finished work is never lost. On startup,
  jobs still `pending`/`processing` (interrupted by a restart or crash) are re-submitted, and
  since only `pending` certificates are processed, resuming never duplicates work.
- **Threads vs. processes:** ReportLab rendering is fast (a few ms per certificate). Threads
  are enough here and keep everything in one process. For heavy CPU loads, a process pool or
  external workers would be the next step.

### Validation and failure handling (two levels)

1. **Request level → `422`, nothing stored:** missing or blank `title`/`issuer`, invalid date,
   an empty `recipients` list, more than `MAX_RECIPIENTS`, or a malformed CSV (no `name` column,
   not UTF-8, too large).
2. **Recipient level → stored as `invalid` with a reason, and the job continues:** empty or
   overly long name, bad email, a duplicate email within the same job (case-insensitive), or an
   entry that isn't an object. Duplicate *names* without emails are allowed, since two people
   can share a name.

At generation time, each certificate is rendered inside its own `try/except`. An exception is
recorded on that row as `failed` with the error message, and the loop moves on, so **one
failure never stops the rest of the job**. Only an error outside per-certificate generation
(for example, the database becoming unavailable) marks the whole job `failed`, with
`jobs.error` set.

PDFs are written to a temporary file and atomically renamed, so a crash never leaves a
half-written PDF that looks valid.

### Certificate template

A single template defined in code (`app/generator.py`): an A4 landscape page with a border,
the recipient's name, course title, issue date, issuer, and a certificate ID (the certificate's
UUID). Long names and titles are automatically shrunk (titles also wrap to two lines) so they
always fit. Using built-in PDF fonts means no font or template files to ship.

### Other choices

- **UUIDs for ids**, so job and certificate URLs can't be guessed by counting.
- **Times are UTC** and always returned with a timezone. A custom column type fixes SQLite
  dropping timezone info.
- **SQLite in WAL mode**, so API reads don't block the worker's writes.
- **ZIP downloads** are built in memory on request. This is fine for the 1,000-recipient cap
  (each PDF is about 2–3 KB). For much larger jobs, the ZIP would be streamed or pre-built
  by the worker.

### Known limitations and possible next steps

- No authentication or authorization. Anyone with a job id can read its results.
- Running several API processes (`uvicorn --workers N`) would make each one resume unfinished
  jobs on startup. Run a single process, or move to an external queue.
- The built-in Helvetica font only covers Latin characters. Names in other scripts (such as
  Devanagari or CJK) need a Unicode TTF font registered in `generator.py`.
- Possible next steps: emailing certificates, a public verification endpoint by certificate
  ID, object storage (S3) instead of the local disk, and Alembic migrations.
