from __future__ import annotations

import io
import re
import shutil
import sqlite3
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .database import DatabaseTarget, resolve_database
from .importer import import_project
from .service import project_summary


MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 250 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 10_000


def _safe_filename(filename: str) -> str:
    name = Path(filename).name.strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    if not name:
        name = "DPL.xlsx"
    stem = Path(name).stem[:100].strip(" .") or "DPL"
    return f"{stem}.xlsx"


def validate_xlsx_upload(filename: str, content: bytes) -> str:
    if Path(Path(filename).name).suffix.casefold() != ".xlsx":
        raise ValueError("Bitte wählen Sie eine DPL im Format .xlsx aus.")
    safe_name = _safe_filename(filename)
    if not content:
        raise ValueError("Die ausgewählte Datei ist leer.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("Die DPL ist größer als 25 MB.")
    if not content.startswith(b"PK"):
        raise ValueError("Die Datei ist keine gültige XLSX-Arbeitsmappe.")

    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            names = {entry.filename for entry in entries}
            unpacked_size = sum(entry.file_size for entry in entries)
            if len(entries) > MAX_ARCHIVE_ENTRIES or unpacked_size > MAX_UNCOMPRESSED_BYTES:
                raise ValueError("Die XLSX-Datei ist ungewöhnlich groß und wurde nicht verarbeitet.")
            required = {"[Content_Types].xml", "xl/workbook.xml"}
            if not required.issubset(names):
                raise ValueError("Die Datei besitzt keine gültige XLSX-Struktur.")
    except zipfile.BadZipFile as exc:
        raise ValueError("Die Datei ist keine gültige XLSX-Arbeitsmappe.") from exc

    return safe_name


def _switch_to_delete_journal(database_path: Path) -> None:
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("PRAGMA journal_mode = DELETE")
    finally:
        connection.close()


def _database_integrity(database_path: Path) -> None:
    connection = sqlite3.connect(database_path)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != "ok" or foreign_keys:
            raise ValueError("Die erzeugte Projektdatenbank hat die Integritätsprüfung nicht bestanden.")
    finally:
        connection.close()


def import_uploaded_dpl(
    database: DatabaseTarget,
    filename: str,
    content: bytes,
    acron_template_path: Path,
    provider_template_path: Path,
    *,
    data_directory: Path | None = None,
) -> dict[str, Any]:
    safe_name = validate_xlsx_upload(filename, content)
    database_config = resolve_database(database)
    if data_directory is None:
        if database_config.backend == "sqlite":
            assert database_config.path is not None
            data_directory = database_config.path.parent
        else:
            data_directory = Path("data")
    import_directory = data_directory / "imports"
    backup_directory = data_directory / "backups"
    import_directory.mkdir(parents=True, exist_ok=True)
    backup_directory.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    source_path = import_directory / f"{timestamp}-{safe_name}"
    incoming_database = data_directory / f".incoming-{uuid.uuid4().hex}.sqlite"
    backup_path: Path | None = None

    source_path.write_bytes(content)
    try:
        validation_result = import_project(
            incoming_database,
            source_path,
            acron_template_path,
            provider_template_path,
            source_filename=safe_name,
        )
        summary = project_summary(incoming_database)
        _database_integrity(incoming_database)
        _switch_to_delete_journal(incoming_database)

        if (
            database_config.backend == "sqlite"
            and database_config.path is not None
            and database_config.path.exists()
        ):
            _switch_to_delete_journal(database_config.path)
            backup_path = backup_directory / f"dpl-{timestamp}.sqlite"
            shutil.copy2(database_config.path, backup_path)

        result = import_project(
            database,
            source_path,
            acron_template_path,
            provider_template_path,
            force=True,
            source_filename=safe_name,
        )
        summary = project_summary(database)
    except Exception:
        source_path.unlink(missing_ok=True)
        raise
    finally:
        for suffix in ("", "-wal", "-shm"):
            Path(f"{incoming_database}{suffix}").unlink(missing_ok=True)

    return {
        **result,
        "validated_selected": validation_result["selected"],
        "source_filename": safe_name,
        "stored_source": str(source_path.resolve()),
        "backup_filename": backup_path.name if backup_path else None,
        "backend": database_config.backend,
        "summary": summary,
    }
