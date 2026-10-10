"""Application settings, read from environment variables with sensible defaults."""

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    database_url: str = field(
        default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///./certificates.db")
    )
    # Directory where generated PDF files are written.
    storage_dir: Path = field(
        default_factory=lambda: Path(os.getenv("STORAGE_DIR", "./storage")).resolve()
    )
    # Upper bound on recipients in a single job, to protect the server.
    max_recipients: int = field(default_factory=lambda: int(os.getenv("MAX_RECIPIENTS", "1000")))
    # Maximum accepted size of an uploaded CSV file, in bytes.
    max_csv_bytes: int = field(default_factory=lambda: int(os.getenv("MAX_CSV_BYTES", str(1024 * 1024))))
    # Number of jobs that may be processed concurrently by the background worker.
    worker_threads: int = field(default_factory=lambda: int(os.getenv("WORKER_THREADS", "2")))


settings = Settings()
