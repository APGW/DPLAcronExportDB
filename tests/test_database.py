from __future__ import annotations

import os
import sqlite3
import sys
import types
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from dpl_export.database import DatabaseConfig, connect, database_from_environment
from dpl_export.importer import import_project


ROOT = Path(__file__).resolve().parents[1]
DPL = ROOT / "2026-06-12 DPL_PWK Lippestraße; Braukgraben.xlsx"
ACRON_TEMPLATE = ROOT / "brauckgraben_neu.csv.xlsx"
PROVIDER_TEMPLATE = ROOT / "shhamm_provider1_WINCC60.csv"


class FakeCursor:
    def __init__(self) -> None:
        self.executed = None

    def execute(self, statement, parameters) -> None:
        self.executed = (statement, parameters)


class FakeMariaConnection:
    def __init__(self) -> None:
        self.cursor_options = None
        self.cursor_instance = FakeCursor()

    def cursor(self, **options):
        self.cursor_options = options
        return self.cursor_instance

    def close(self) -> None:
        pass


class DatabaseConfigurationTest(unittest.TestCase):
    def test_mariadb_environment_is_parsed_without_exposing_password(self) -> None:
        environment = {
            "DPL_DB_DRIVER": "mariadb",
            "DPL_DB_HOST": "localhost",
            "DPL_DB_PORT": "3307",
            "DPL_DB_NAME": "dpl_test",
            "DPL_DB_USER": "service",
            "DPL_DB_PASSWORD": "streng-geheim",
            "DPL_DB_UNIX_SOCKET": "/run/mysqld/mysqld.sock",
        }
        with patch.dict(os.environ, environment, clear=True):
            config = database_from_environment(ROOT / "data" / "fallback.sqlite")
        self.assertEqual("mariadb", config.backend)
        self.assertEqual(3307, config.port)
        self.assertEqual("dpl_test", config.name)
        self.assertNotIn("streng-geheim", repr(config))

    def test_connector_uses_dictionary_rows_and_explicit_transactions(self) -> None:
        raw = FakeMariaConnection()
        captured = {}

        def fake_connect(**settings):
            captured.update(settings)
            return raw

        module = types.SimpleNamespace(connect=fake_connect)
        config = DatabaseConfig(
            backend="mariadb",
            user="service",
            password="passwort",
            name="dpl_test",
        )
        with patch.dict(sys.modules, {"mariadb": module}):
            connection = connect(config)
            connection.execute("SELECT ? AS value", [1])
        self.assertIs(True, captured["autocommit"])
        self.assertEqual({"dictionary": True}, raw.cursor_options)
        self.assertEqual(("SELECT ? AS value", (1,)), raw.cursor_instance.executed)

    def test_mariadb_schema_is_transaction_safe_without_login_tables(self) -> None:
        schema = (ROOT / "dpl_export" / "schema_mariadb.sql").read_text(encoding="utf-8")
        self.assertIn("ENGINE=InnoDB", schema)
        self.assertNotIn("CREATE TABLE IF NOT EXISTS users", schema)
        self.assertNotIn("CREATE TABLE IF NOT EXISTS sessions", schema)
        self.assertIn("row_version BIGINT UNSIGNED NOT NULL", schema)
        self.assertNotIn("changed_by_user_id", schema)

    def test_sqlite_upgrade_removes_legacy_login_tables_and_keeps_audit(self) -> None:
        database = ROOT / ".tmp" / f"legacy-login-{uuid.uuid4().hex}.sqlite"
        database.parent.mkdir(parents=True, exist_ok=True)
        try:
            import_project(database, DPL, ACRON_TEMPLATE, PROVIDER_TEMPLATE)
            raw = sqlite3.connect(database)
            raw.executescript(
                """
                CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT);
                CREATE TABLE sessions (id INTEGER PRIMARY KEY, user_id INTEGER);
                ALTER TABLE audit_log ADD COLUMN changed_by_user_id INTEGER REFERENCES users(id);
                INSERT INTO users(id, username) VALUES (1, 'alt');
                UPDATE audit_log SET changed_by_user_id = 1;
                """
            )
            raw.close()

            import_project(database, DPL, ACRON_TEMPLATE, PROVIDER_TEMPLATE)
            raw = sqlite3.connect(database)
            tables = {row[0] for row in raw.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            columns = {row[1] for row in raw.execute("PRAGMA table_info(audit_log)")}
            audit_count = raw.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
            raw.close()

            self.assertNotIn("users", tables)
            self.assertNotIn("sessions", tables)
            self.assertNotIn("changed_by_user_id", columns)
            self.assertGreater(audit_count, 0)
        finally:
            for suffix in ("", "-wal", "-shm"):
                Path(f"{database}{suffix}").unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
