from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest

from investing_bot.db import (
    Database,
    DatabaseError,
    JobRunRepository,
    JobStatus,
    MigrationError,
)


def open_database(path: Path) -> Database:
    database = Database(path)
    database.connect()
    assert database.migrate() == 5
    return database


def test_migrations_are_idempotent_and_persist_across_reopen(tmp_path: Path) -> None:
    path = tmp_path / "state" / "investing_bot.duckdb"
    database = open_database(path)
    assert database.is_ready()
    assert database.migrate() == 5
    database.close()

    reopened = open_database(path)
    assert reopened.is_ready()
    assert reopened.fetchone("SELECT COUNT(*) FROM schema_migrations") == (5,)
    reopened.close()


def test_modified_applied_migration_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "investing_bot.duckdb"
    database = open_database(path)
    database.execute(
        "UPDATE schema_migrations SET checksum = ? WHERE version = 1",
        ["0" * 64],
    )
    database.close()

    reopened = Database(path)
    reopened.connect()
    with pytest.raises(MigrationError, match="checksum mismatch"):
        reopened.migrate()
    reopened.close()


def test_job_run_lifecycle_is_persistent_and_guarded(tmp_path: Path) -> None:
    path = tmp_path / "investing_bot.duckdb"
    database = open_database(path)
    repository = JobRunRepository(database)
    requested = datetime(2026, 8, 14, 14, 0, tzinfo=UTC)
    config_hash = sha256(b"foundation-config").hexdigest()

    queued = repository.create(
        job_type="foundation_check",
        code_version="0.1.0",
        config_hash=config_hash,
        requested_at=requested,
    )
    running = repository.start(
        queued.run_id,
        owner="test-worker",
        started_at=requested + timedelta(seconds=1),
    )
    succeeded = repository.succeed(
        queued.run_id,
        finished_at=requested + timedelta(seconds=2),
    )

    assert queued.status is JobStatus.QUEUED
    assert running.status is JobStatus.RUNNING
    assert running.lease_owner == "test-worker"
    assert succeeded.status is JobStatus.SUCCEEDED
    assert succeeded.lease_owner is None
    with pytest.raises(DatabaseError):
        repository.succeed(queued.run_id)
    database.close()

    reopened = open_database(path)
    persisted = JobRunRepository(reopened).get(queued.run_id)
    assert persisted.status is JobStatus.SUCCEEDED
    assert persisted.finished_at == requested + timedelta(seconds=2)
    reopened.close()


def test_job_type_lease_blocks_duplicates_until_expiry(tmp_path: Path) -> None:
    database = open_database(tmp_path / "investing_bot.duckdb")
    repository = JobRunRepository(database)
    now = datetime(2026, 8, 14, 14, 0, tzinfo=UTC)

    assert repository.acquire_lease(
        job_type="daily_prices",
        owner="worker-a",
        lease_duration=timedelta(minutes=5),
        acquired_at=now,
    )
    assert not repository.acquire_lease(
        job_type="daily_prices",
        owner="worker-b",
        lease_duration=timedelta(minutes=5),
        acquired_at=now + timedelta(minutes=1),
    )
    assert repository.acquire_lease(
        job_type="daily_prices",
        owner="worker-b",
        lease_duration=timedelta(minutes=5),
        acquired_at=now + timedelta(minutes=6),
    )
    assert not repository.release_lease(job_type="daily_prices", owner="worker-a")
    assert repository.release_lease(job_type="daily_prices", owner="worker-b")
    database.close()
