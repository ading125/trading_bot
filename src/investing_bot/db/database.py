"""Thread-safe DuckDB connection ownership and migration execution."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from importlib.resources import files
from pathlib import Path
import re
from threading import RLock
from typing import Any

import duckdb
from duckdb import DuckDBPyConnection


_MIGRATION_PATTERN = re.compile(r"^(?P<version>[0-9]{4})_(?P<name>[a-z0-9_]+)\.sql$")
_MIGRATION_PACKAGE = "investing_bot.db.migrations"


class DatabaseError(RuntimeError):
    """Base error for persistence failures safe to classify at service edges."""


class MigrationError(DatabaseError):
    """Raised when migration history is incompatible or cannot be applied."""


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    checksum: str
    sql: str


class Database:
    """Own one serialized DuckDB connection for the local application process."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._connection: DuckDBPyConnection | None = None
        self._lock = RLock()
        self._latest_migration = 0

    def connect(self) -> None:
        """Open the database after ensuring its parent volume exists."""

        with self._lock:
            if self._connection is not None:
                return
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            try:
                self._connection = duckdb.connect(str(self.path))
            except duckdb.Error as exc:
                raise DatabaseError("unable to open application database") from exc

    def close(self) -> None:
        """Close the owned connection."""

        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def migrate(self) -> int:
        """Apply bundled migrations transactionally and verify prior checksums."""

        migrations = _load_migrations()
        with self._lock:
            connection = self._require_connection()
            try:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version INTEGER PRIMARY KEY,
                        name VARCHAR NOT NULL,
                        checksum VARCHAR NOT NULL,
                        applied_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
                    )
                    """
                )
                applied = {
                    int(row[0]): (str(row[1]), str(row[2]))
                    for row in connection.execute(
                        "SELECT version, name, checksum FROM schema_migrations"
                    ).fetchall()
                }

                known_versions = {migration.version for migration in migrations}
                unknown_versions = set(applied) - known_versions
                if unknown_versions:
                    raise MigrationError(
                        "database contains migrations newer than this application"
                    )

                for migration in migrations:
                    prior = applied.get(migration.version)
                    if prior is not None:
                        if prior != (migration.name, migration.checksum):
                            raise MigrationError(
                                f"migration {migration.version:04d} checksum mismatch"
                            )
                        continue
                    self._apply_migration(connection, migration)
            except MigrationError:
                raise
            except duckdb.Error as exc:
                raise MigrationError("database migration failed") from exc

            self._latest_migration = migrations[-1].version if migrations else 0
            return self._latest_migration

    def is_ready(self) -> bool:
        """Return whether the database responds and has the expected schema."""

        try:
            row = self.fetchone("SELECT COALESCE(MAX(version), 0) FROM schema_migrations")
            return row is not None and int(row[0]) == self._latest_migration
        except (DatabaseError, duckdb.Error):
            return False

    @property
    def latest_migration(self) -> int:
        return self._latest_migration

    def execute(self, sql: str, parameters: Sequence[Any] = ()) -> None:
        """Execute a statement through the serialized connection."""

        with self._lock:
            try:
                self._require_connection().execute(sql, parameters)
            except duckdb.Error as exc:
                raise DatabaseError("database statement failed") from exc

    def fetchone(
        self, sql: str, parameters: Sequence[Any] = ()
    ) -> tuple[Any, ...] | None:
        """Execute a query and return one row."""

        with self._lock:
            try:
                return self._require_connection().execute(sql, parameters).fetchone()
            except duckdb.Error as exc:
                raise DatabaseError("database query failed") from exc

    def fetchall(
        self, sql: str, parameters: Sequence[Any] = ()
    ) -> list[tuple[Any, ...]]:
        """Execute a query and return all rows."""

        with self._lock:
            try:
                return self._require_connection().execute(sql, parameters).fetchall()
            except duckdb.Error as exc:
                raise DatabaseError("database query failed") from exc

    @contextmanager
    def transaction(self) -> Iterator[DuckDBPyConnection]:
        """Yield the connection inside one serialized transaction."""

        with self._lock:
            connection = self._require_connection()
            connection.execute("BEGIN TRANSACTION")
            try:
                yield connection
            except Exception:
                connection.execute("ROLLBACK")
                raise
            else:
                connection.execute("COMMIT")

    def _require_connection(self) -> DuckDBPyConnection:
        if self._connection is None:
            raise DatabaseError("database is not connected")
        return self._connection

    @staticmethod
    def _apply_migration(
        connection: DuckDBPyConnection, migration: Migration
    ) -> None:
        connection.execute("BEGIN TRANSACTION")
        try:
            connection.execute(migration.sql)
            connection.execute(
                "INSERT INTO schema_migrations (version, name, checksum) VALUES (?, ?, ?)",
                [migration.version, migration.name, migration.checksum],
            )
        except Exception:
            connection.execute("ROLLBACK")
            raise
        else:
            connection.execute("COMMIT")


def _load_migrations() -> list[Migration]:
    migrations: list[Migration] = []
    for resource in files(_MIGRATION_PACKAGE).iterdir():
        match = _MIGRATION_PATTERN.fullmatch(resource.name)
        if match is None:
            continue
        sql = resource.read_text(encoding="utf-8")
        migrations.append(
            Migration(
                version=int(match.group("version")),
                name=match.group("name"),
                checksum=sha256(sql.encode("utf-8")).hexdigest(),
                sql=sql,
            )
        )
    migrations.sort(key=lambda migration: migration.version)
    if len({migration.version for migration in migrations}) != len(migrations):
        raise MigrationError("duplicate migration version")
    return migrations
