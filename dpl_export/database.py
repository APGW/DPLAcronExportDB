from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator


SQLITE_SCHEMA_PATH = Path(__file__).with_name("schema.sql")
MARIADB_SCHEMA_PATH = Path(__file__).with_name("schema_mariadb.sql")
VIEW_PATH = Path(__file__).with_name("view.sql")


@dataclass(frozen=True)
class DatabaseConfig:
    backend: str
    path: Path | None = None
    host: str = "127.0.0.1"
    port: int = 3306
    name: str = "dpl_acron"
    user: str = "dpl_acron"
    password: str = field(default="", repr=False)
    unix_socket: str | None = None

    @classmethod
    def sqlite(cls, path: Path | str) -> "DatabaseConfig":
        return cls(backend="sqlite", path=Path(path))


DatabaseTarget = DatabaseConfig | Path | str


def database_from_environment(default_sqlite_path: Path) -> DatabaseConfig:
    backend = os.environ.get("DPL_DB_DRIVER", "sqlite").strip().casefold()
    if backend == "sqlite":
        configured_path = os.environ.get("DPL_SQLITE_PATH")
        return DatabaseConfig.sqlite(configured_path or default_sqlite_path)
    if backend != "mariadb":
        raise ValueError("DPL_DB_DRIVER muss 'sqlite' oder 'mariadb' sein.")
    try:
        port = int(os.environ.get("DPL_DB_PORT", "3306"))
    except ValueError as exc:
        raise ValueError("DPL_DB_PORT muss eine Zahl sein.") from exc
    password = os.environ.get("DPL_DB_PASSWORD", "")
    if not password:
        raise ValueError("DPL_DB_PASSWORD ist für MariaDB nicht gesetzt.")
    return DatabaseConfig(
        backend="mariadb",
        host=os.environ.get("DPL_DB_HOST", "127.0.0.1"),
        port=port,
        name=os.environ.get("DPL_DB_NAME", "dpl_acron"),
        user=os.environ.get("DPL_DB_USER", "dpl_acron"),
        password=password,
        unix_socket=os.environ.get("DPL_DB_UNIX_SOCKET") or None,
    )


def resolve_database(target: DatabaseTarget) -> DatabaseConfig:
    if isinstance(target, DatabaseConfig):
        return target
    return DatabaseConfig.sqlite(target)


class DatabaseConnection:
    def __init__(self, backend: str, raw: Any):
        self.backend = backend
        self.raw = raw

    def execute(self, statement: str, parameters: Any = ()):
        if self.backend == "sqlite":
            return self.raw.execute(statement, parameters)
        cursor = self.raw.cursor(dictionary=True)
        cursor.execute(statement, tuple(parameters))
        return cursor

    def executemany(self, statement: str, parameters: Any):
        if self.backend == "sqlite":
            return self.raw.executemany(statement, parameters)
        cursor = self.raw.cursor(dictionary=True)
        cursor.executemany(statement, parameters)
        return cursor

    def begin(self) -> None:
        if self.backend == "sqlite":
            self.raw.execute("BEGIN")
        else:
            self.raw.begin()

    def commit(self) -> None:
        self.raw.commit()

    def rollback(self) -> None:
        self.raw.rollback()

    def close(self) -> None:
        self.raw.close()


def connect(target: DatabaseTarget) -> DatabaseConnection:
    config = resolve_database(target)
    if config.backend == "sqlite":
        assert config.path is not None
        config.path.parent.mkdir(parents=True, exist_ok=True)
        raw = sqlite3.connect(config.path, timeout=10)
        raw.row_factory = sqlite3.Row
        raw.execute("PRAGMA foreign_keys = ON")
        raw.execute("PRAGMA journal_mode = WAL")
        raw.execute("PRAGMA busy_timeout = 10000")
        return DatabaseConnection("sqlite", raw)

    try:
        import mariadb
    except ImportError as exc:
        raise RuntimeError(
            "Der MariaDB-Python-Connector fehlt. Unter Debian 13: "
            "sudo apt install python3-mariadb-connector"
        ) from exc

    settings: dict[str, Any] = {
        "host": config.host,
        "port": config.port,
        "user": config.user,
        "password": config.password,
        "database": config.name,
        "connect_timeout": 5,
        # Lesezugriffe bleiben außerhalb einer Transaktion; schreibende Abläufe
        # öffnen über transaction() ausdrücklich eine atomare Transaktion.
        "autocommit": True,
    }
    if config.unix_socket:
        settings["unix_socket"] = config.unix_socket
    return DatabaseConnection("mariadb", mariadb.connect(**settings))


def _split_sql_script(script: str) -> list[str]:
    return [statement.strip() for statement in script.split(";") if statement.strip()]


def _sqlite_columns(connection: DatabaseConnection, table: str) -> set[str]:
    return {row["name"] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}


def _sqlite_migrations(connection: DatabaseConnection) -> None:
    if "has_orange_changes" not in _sqlite_columns(connection, "datapoints"):
        connection.execute(
            "ALTER TABLE datapoints ADD COLUMN has_orange_changes INTEGER NOT NULL DEFAULT 0"
        )
    if "row_version" not in _sqlite_columns(connection, "datapoints"):
        connection.execute("ALTER TABLE datapoints ADD COLUMN row_version INTEGER NOT NULL DEFAULT 1")
    if "pcs7_reviewed" not in _sqlite_columns(connection, "datapoints"):
        connection.execute("ALTER TABLE datapoints ADD COLUMN pcs7_reviewed INTEGER NOT NULL DEFAULT 0")
    if "pcs7_reviewed_at" not in _sqlite_columns(connection, "datapoints"):
        connection.execute("ALTER TABLE datapoints ADD COLUMN pcs7_reviewed_at TEXT")
    if "changed_by_user_id" in _sqlite_columns(connection, "audit_log"):
        connection.commit()
        connection.raw.execute("PRAGMA foreign_keys = OFF")
        connection.raw.executescript(
            """
            BEGIN;
            CREATE TABLE audit_log_without_login (
                id INTEGER PRIMARY KEY,
                datapoint_id INTEGER REFERENCES datapoints(id) ON DELETE SET NULL,
                entity TEXT NOT NULL,
                field_name TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT,
                changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO audit_log_without_login(
                id, datapoint_id, entity, field_name, old_value, new_value, changed_at
            )
            SELECT id, datapoint_id, entity, field_name, old_value, new_value, changed_at
            FROM audit_log;
            DROP TABLE audit_log;
            ALTER TABLE audit_log_without_login RENAME TO audit_log;
            DROP TABLE IF EXISTS sessions;
            DROP TABLE IF EXISTS users;
            COMMIT;
            """
        )
        connection.raw.execute("PRAGMA foreign_keys = ON")
    else:
        connection.execute("DROP TABLE IF EXISTS sessions")
        connection.execute("DROP TABLE IF EXISTS users")
    connection.execute("DROP VIEW IF EXISTS v_datapoint_editor")
    connection.execute("CREATE VIEW v_datapoint_editor AS " + VIEW_PATH.read_text(encoding="utf-8"))
    connection.commit()


def _mariadb_migrations(connection: DatabaseConnection) -> None:
    connection.execute(
        "ALTER TABLE datapoints ADD COLUMN IF NOT EXISTS "
        "has_orange_changes TINYINT(1) NOT NULL DEFAULT 0"
    )
    connection.execute(
        "ALTER TABLE datapoints ADD COLUMN IF NOT EXISTS row_version BIGINT UNSIGNED NOT NULL DEFAULT 1"
    )
    connection.execute(
        "ALTER TABLE datapoints ADD COLUMN IF NOT EXISTS pcs7_reviewed TINYINT(1) NOT NULL DEFAULT 0"
    )
    connection.execute(
        "ALTER TABLE datapoints ADD COLUMN IF NOT EXISTS pcs7_reviewed_at TIMESTAMP NULL"
    )
    constraints = connection.execute(
        """
        SELECT CONSTRAINT_NAME
        FROM information_schema.KEY_COLUMN_USAGE
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'audit_log'
          AND COLUMN_NAME = 'changed_by_user_id'
          AND REFERENCED_TABLE_NAME IS NOT NULL
        """
    ).fetchall()
    for row in constraints:
        constraint = str(row["CONSTRAINT_NAME"]).replace("`", "``")
        connection.execute(f"ALTER TABLE audit_log DROP FOREIGN KEY `{constraint}`")
    columns = connection.execute(
        """
        SELECT COLUMN_NAME
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'audit_log'
          AND COLUMN_NAME = 'changed_by_user_id'
        """
    ).fetchall()
    if columns:
        connection.execute("ALTER TABLE audit_log DROP COLUMN changed_by_user_id")
    connection.execute("DROP TABLE IF EXISTS sessions")
    connection.execute("DROP TABLE IF EXISTS users")
    connection.execute("CREATE OR REPLACE VIEW v_datapoint_editor AS " + VIEW_PATH.read_text(encoding="utf-8"))
    connection.commit()


def initialize_schema(connection: DatabaseConnection) -> None:
    if connection.backend == "sqlite":
        connection.raw.executescript(SQLITE_SCHEMA_PATH.read_text(encoding="utf-8"))
        _sqlite_migrations(connection)
        return
    for statement in _split_sql_script(MARIADB_SCHEMA_PATH.read_text(encoding="utf-8")):
        connection.execute(statement)
    _mariadb_migrations(connection)


def database_backend(target: DatabaseTarget) -> str:
    return resolve_database(target).backend


def database_health(target: DatabaseTarget) -> dict[str, str]:
    connection = connect(target)
    try:
        row = connection.execute("SELECT 1 AS healthy").fetchone()
        if not row or int(row["healthy"]) != 1:
            raise RuntimeError("Datenbankprüfung fehlgeschlagen.")
        return {"status": "ok", "backend": connection.backend}
    finally:
        connection.close()


@contextmanager
def transaction(connection: DatabaseConnection) -> Iterator[DatabaseConnection]:
    try:
        connection.begin()
        yield connection
    except Exception:
        connection.rollback()
        raise
    else:
        connection.commit()
