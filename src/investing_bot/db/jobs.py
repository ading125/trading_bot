"""Persistent lifecycle records and leases for application jobs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from investing_bot.db.database import Database, DatabaseError


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class JobRun(BaseModel):
    """Immutable view of a persisted job execution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    job_type: str
    requested_at: AwareDatetime
    started_at: AwareDatetime | None
    finished_at: AwareDatetime | None
    status: JobStatus
    code_version: str
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    error_summary: str | None
    lease_owner: str | None
    lease_expires_at: AwareDatetime | None


_JOB_COLUMNS = """
run_id, job_type, requested_at, started_at, finished_at, status,
code_version, config_hash, error_summary, lease_owner, lease_expires_at
"""


class JobRunRepository:
    """Apply guarded state transitions to job records and job-type leases."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def create(
        self,
        *,
        job_type: str,
        code_version: str,
        config_hash: str,
        requested_at: datetime | None = None,
    ) -> JobRun:
        run_id = str(uuid4())
        timestamp = _as_utc(requested_at or datetime.now(UTC))
        self._database.execute(
            """
            INSERT INTO job_runs (
                run_id, job_type, requested_at, status, code_version, config_hash
            ) VALUES (?, ?, ?, 'queued', ?, ?)
            """,
            [run_id, job_type, timestamp, code_version, config_hash],
        )
        return self.get(run_id)

    def start(
        self,
        run_id: str,
        *,
        owner: str,
        lease_duration: timedelta = timedelta(minutes=15),
        started_at: datetime | None = None,
    ) -> JobRun:
        timestamp = _as_utc(started_at or datetime.now(UTC))
        expires_at = timestamp + lease_duration
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        with self._database.transaction() as connection:
            changed = connection.execute(
                """
                UPDATE job_runs
                SET status = 'running', started_at = ?, lease_owner = ?,
                    lease_expires_at = ?
                WHERE run_id = ? AND status = 'queued'
                RETURNING run_id
                """,
                [timestamp, owner, expires_at, run_id],
            ).fetchone()
            if changed is None:
                raise DatabaseError("job can start only from queued state")
        return self.get(run_id)

    def succeed(
        self, run_id: str, *, finished_at: datetime | None = None
    ) -> JobRun:
        return self._finish(run_id, JobStatus.SUCCEEDED, None, finished_at)

    def fail(
        self,
        run_id: str,
        *,
        error_summary: str,
        finished_at: datetime | None = None,
    ) -> JobRun:
        summary = " ".join(error_summary.split())[:500]
        if not summary:
            raise ValueError("error_summary must not be empty")
        return self._finish(run_id, JobStatus.FAILED, summary, finished_at)

    def interrupt(
        self,
        run_id: str,
        *,
        error_summary: str = "application stopped before job completed",
        finished_at: datetime | None = None,
    ) -> JobRun:
        return self._finish(
            run_id,
            JobStatus.INTERRUPTED,
            " ".join(error_summary.split())[:500],
            finished_at,
        )

    def get(self, run_id: str) -> JobRun:
        row = self._database.fetchone(
            f"SELECT {_JOB_COLUMNS} FROM job_runs WHERE run_id = ?", [run_id]
        )
        if row is None:
            raise DatabaseError("job run not found")
        return _row_to_job(row)

    def list_recent(self, *, limit: int = 50) -> list[JobRun]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        rows = self._database.fetchall(
            f"SELECT {_JOB_COLUMNS} FROM job_runs ORDER BY requested_at DESC LIMIT ?",
            [limit],
        )
        return [_row_to_job(row) for row in rows]

    def latest_for_type(self, job_type: str) -> JobRun | None:
        row = self._database.fetchone(
            f"""
            SELECT {_JOB_COLUMNS} FROM job_runs
            WHERE job_type=? ORDER BY requested_at DESC LIMIT 1
            """,
            [job_type],
        )
        return None if row is None else _row_to_job(row)

    def acquire_lease(
        self,
        *,
        job_type: str,
        owner: str,
        lease_duration: timedelta,
        acquired_at: datetime | None = None,
    ) -> bool:
        """Acquire one job-type lease, replacing only an expired lease."""

        timestamp = _as_utc(acquired_at or datetime.now(UTC))
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        expires_at = timestamp + lease_duration
        with self._database.transaction() as connection:
            connection.execute(
                "DELETE FROM job_leases WHERE job_type = ? AND expires_at <= ?",
                [job_type, timestamp],
            )
            existing = connection.execute(
                "SELECT 1 FROM job_leases WHERE job_type = ?", [job_type]
            ).fetchone()
            if existing is not None:
                return False
            connection.execute(
                """
                INSERT INTO job_leases (job_type, lease_owner, acquired_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                [job_type, owner, timestamp, expires_at],
            )
        return True

    def release_lease(self, *, job_type: str, owner: str) -> bool:
        with self._database.transaction() as connection:
            deleted = connection.execute(
                """
                DELETE FROM job_leases
                WHERE job_type = ? AND lease_owner = ?
                RETURNING job_type
                """,
                [job_type, owner],
            ).fetchone()
        return deleted is not None

    def mark_running_jobs_interrupted(
        self, *, finished_at: datetime | None = None
    ) -> int:
        """Make shutdown/restart state explicit instead of leaving ghost runs."""

        timestamp = _as_utc(finished_at or datetime.now(UTC))
        with self._database.transaction() as connection:
            rows = connection.execute(
                """
                UPDATE job_runs
                SET status = 'interrupted', finished_at = ?,
                    error_summary = 'application stopped before job completed',
                    lease_owner = NULL, lease_expires_at = NULL
                WHERE status = 'running'
                RETURNING run_id
                """,
                [timestamp],
            ).fetchall()
            connection.execute("DELETE FROM job_leases")
        return len(rows)

    def _finish(
        self,
        run_id: str,
        status: JobStatus,
        error_summary: str | None,
        finished_at: datetime | None,
    ) -> JobRun:
        timestamp = _as_utc(finished_at or datetime.now(UTC))
        with self._database.transaction() as connection:
            changed = connection.execute(
                """
                UPDATE job_runs
                SET status = ?, finished_at = ?, error_summary = ?,
                    lease_owner = NULL, lease_expires_at = NULL
                WHERE run_id = ? AND status = 'running'
                RETURNING run_id
                """,
                [status.value, timestamp, error_summary, run_id],
            ).fetchone()
            if changed is None:
                raise DatabaseError("job can finish only from running state")
        return self.get(run_id)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _row_to_job(row: tuple[object, ...]) -> JobRun:
    return JobRun(
        run_id=str(row[0]),
        job_type=str(row[1]),
        requested_at=row[2],
        started_at=row[3],
        finished_at=row[4],
        status=JobStatus(str(row[5])),
        code_version=str(row[6]),
        config_hash=str(row[7]),
        error_summary=None if row[8] is None else str(row[8]),
        lease_owner=None if row[9] is None else str(row[9]),
        lease_expires_at=row[10],
    )
