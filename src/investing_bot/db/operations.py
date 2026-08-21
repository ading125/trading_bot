"""Persistence for idempotent operational schedule dispatch."""

from __future__ import annotations

from datetime import UTC, datetime

from investing_bot.db.database import Database
from investing_bot.models.operations import OperationalScheduleRun, OperationalTask


class OperationsRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def claim(
        self,
        task: OperationalTask,
        scheduled_for: datetime,
        *,
        started_at: datetime | None = None,
    ) -> bool:
        timestamp = _utc(started_at or datetime.now(UTC))
        scheduled = _utc(scheduled_for)
        with self._database.transaction() as connection:
            existing = connection.execute(
                """
                SELECT 1 FROM operational_schedule_runs
                WHERE task=? AND scheduled_for=?
                """,
                [task.value, scheduled],
            ).fetchone()
            if existing is not None:
                return False
            connection.execute(
                """
                INSERT INTO operational_schedule_runs
                    (task, scheduled_for, status, started_at)
                VALUES (?, ?, 'running', ?)
                """,
                [task.value, scheduled, timestamp],
            )
        return True

    def finish(
        self,
        task: OperationalTask,
        scheduled_for: datetime,
        *,
        error_summary: str | None = None,
        finished_at: datetime | None = None,
    ) -> None:
        summary = None
        if error_summary:
            summary = " ".join(error_summary.split())[:500]
        self._database.execute(
            """
            UPDATE operational_schedule_runs
            SET status=?, finished_at=?, error_summary=?
            WHERE task=? AND scheduled_for=? AND status='running'
            """,
            [
                "failed" if summary else "succeeded",
                _utc(finished_at or datetime.now(UTC)),
                summary,
                task.value,
                _utc(scheduled_for),
            ],
        )

    def list_recent(self, *, limit: int = 50) -> list[OperationalScheduleRun]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        rows = self._database.fetchall(
            """
            SELECT task, scheduled_for, status, started_at, finished_at, error_summary
            FROM operational_schedule_runs
            ORDER BY scheduled_for DESC LIMIT ?
            """,
            [limit],
        )
        fields = tuple(OperationalScheduleRun.model_fields)
        return [
            OperationalScheduleRun(**dict(zip(fields, row, strict=True)))
            for row in rows
        ]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC)
