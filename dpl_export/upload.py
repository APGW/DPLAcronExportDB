from __future__ import annotations

import io
import re
import os
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .database import DatabaseTarget, connect, resolve_database
from .importer import import_project
from .service import list_datapoints, project_summary


MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 250 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 10_000
MAX_ARCHIVE_ENTRY_BYTES = 16 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
DEFAULT_MIN_FREE_BYTES = 1024 * 1024 * 1024
DEFAULT_MAX_DATA_BYTES = 5 * 1024 * 1024 * 1024


def _tree_size(root: Path) -> int:
    total = 0
    if not root.exists():
        return total
    for path in root.rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def ensure_upload_capacity(data_directory: Path, incoming_bytes: int) -> None:
    minimum_free = int(os.environ.get("DPL_MIN_FREE_BYTES", DEFAULT_MIN_FREE_BYTES))
    maximum_data = int(os.environ.get("DPL_MAX_DATA_BYTES", DEFAULT_MAX_DATA_BYTES))
    if incoming_bytes < 0 or minimum_free < 0 or maximum_data <= 0:
        raise ValueError("Ungültige Speichergrenzen in der Dienstkonfiguration.")
    data_directory.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(data_directory).free - incoming_bytes < minimum_free:
        raise ValueError("Zu wenig freier Speicher für einen sicheren Import.")
    if _tree_size(data_directory) + incoming_bytes > maximum_data:
        raise ValueError("Das konfigurierte Datenlimit ist erreicht. Alte Projektstände archivieren.")


def _safe_filename(filename: str) -> str:
    name = Path(filename).name.strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    if not name:
        name = "DPL.xlsx"
    stem = Path(name).stem[:100].strip(" .") or "DPL"
    return f"{stem}.xlsx"


def validate_xlsx_upload(filename: str, content: bytes, artifact: str = "DPL") -> str:
    if Path(Path(filename).name).suffix.casefold() != ".xlsx":
        raise ValueError(f"Bitte wählen Sie {artifact} im Format .xlsx aus.")
    safe_name = _safe_filename(filename)
    if not content:
        raise ValueError("Die ausgewählte Datei ist leer.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError(f"{artifact} ist größer als 25 MB.")
    if not content.startswith(b"PK"):
        raise ValueError("Die Datei ist keine gültige XLSX-Arbeitsmappe.")

    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            names = {entry.filename for entry in entries}
            unpacked_size = sum(entry.file_size for entry in entries)
            if len(entries) > MAX_ARCHIVE_ENTRIES or unpacked_size > MAX_UNCOMPRESSED_BYTES:
                raise ValueError("Die XLSX-Datei ist ungewöhnlich groß und wurde nicht verarbeitet.")
            for entry in entries:
                normalized = entry.filename.replace("\\", "/")
                if normalized.startswith("/") or ".." in normalized.split("/") or entry.flag_bits & 0x1:
                    raise ValueError("Die XLSX-Datei enthält unzulässige Archiveinträge.")
                if entry.file_size > MAX_ARCHIVE_ENTRY_BYTES:
                    raise ValueError("Die XLSX-Datei enthält einen zu großen Eintrag.")
                if entry.file_size and entry.file_size / max(entry.compress_size, 1) > MAX_COMPRESSION_RATIO:
                    raise ValueError("Die XLSX-Datei weist ein unzulässiges Kompressionsverhältnis auf.")
            required = {"[Content_Types].xml", "xl/workbook.xml"}
            if not required.issubset(names):
                raise ValueError("Die Datei besitzt keine gültige XLSX-Struktur.")
    except zipfile.BadZipFile as exc:
        raise ValueError("Die Datei ist keine gültige XLSX-Arbeitsmappe.") from exc

    return safe_name


def import_uploaded_dpl(
    database: DatabaseTarget,
    filename: str,
    content: bytes,
    acron_template_path: Path,
    provider_template_path: Path,
    *,
    data_directory: Path | None = None,
    module_code: str | None = None,
) -> dict[str, Any]:
    safe_name = validate_xlsx_upload(filename, content)
    database_config = resolve_database(database)
    if data_directory is None:
        data_directory = database_config.path.parent if database_config.path is not None else Path("data")
    import_directory = data_directory / "imports"
    ensure_upload_capacity(data_directory, len(content))
    import_directory.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    source_path = import_directory / f"{timestamp}-{safe_name}"
    source_path.write_bytes(content)
    try:
        result = import_project(
            database,
            source_path,
            acron_template_path,
            provider_template_path,
            force=True,
            source_filename=safe_name,
            module_root=data_directory / "modules",
            module_code=module_code,
        )
        summary = project_summary(database)
    except Exception:
        source_path.unlink(missing_ok=True)
        raise

    return {
        **result,
        "validated_selected": result["selected"],
        "source_filename": safe_name,
        "stored_source": str(source_path.resolve()),
        "backup_filename": None,
        "backend": database_config.backend,
        "summary": summary,
    }


def preview_uploaded_dpl(
    database: DatabaseTarget,
    filename: str,
    content: bytes,
    acron_template_path: Path,
    provider_template_path: Path,
    *,
    data_directory: Path | None = None,
    module_code: str | None = None,
) -> dict[str, Any]:
    safe_name = validate_xlsx_upload(filename, content)
    database_config = resolve_database(database)
    if data_directory is None:
        data_directory = database_config.path.parent if database_config.path is not None else Path("data")
    preview_root = data_directory / "previews"
    ensure_upload_capacity(data_directory, len(content))
    preview_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dpl-", dir=preview_root) as temporary:
        temporary_path = Path(temporary)
        source_path = temporary_path / safe_name
        preview_database = temporary_path / "preview.sqlite"
        source_path.write_bytes(content)
        result = import_project(
            preview_database,
            source_path,
            acron_template_path,
            provider_template_path,
            force=True,
            source_filename=safe_name,
            module_root=data_directory / "modules",
            module_code=module_code,
        )
        proposed = project_summary(preview_database, str(result["project_code"]))
        proposed_records = list_datapoints(preview_database, project_code=str(result["project_code"]))

    current_code = None
    connection = connect(database)
    current = connection.execute(
        """
        SELECT code FROM projects
        WHERE is_current = 1 AND (logical_key = ? OR ba_number = ?)
        ORDER BY version_number DESC, id DESC LIMIT 1
        """,
        (proposed["project"]["logical_key"], proposed["project"]["ba_number"]),
    ).fetchone()
    if current:
        current_code = str(current["code"])
    connection.close()
    current_records = list_datapoints(database, project_code=current_code) if current_code else []

    def record_key(record: dict[str, Any]) -> str:
        return str(record.get("source_number") or record.get("acron_identification") or record["source_row"])

    compare_fields = (
        "source_long_name", "source_pcs7_variable", "acron_identification", "acron_name",
        "acron_group", "acron_formula", "provider_var_name", "provider_group",
    )
    before = {record_key(record): record for record in current_records}
    after = {record_key(record): record for record in proposed_records}
    added = sorted(after.keys() - before.keys())
    removed = sorted(before.keys() - after.keys())
    changed = sorted(
        key for key in after.keys() & before.keys()
        if any(after[key].get(field) != before[key].get(field) for field in compare_fields)
    )
    return {
        "sourceFilename": safe_name,
        "project": proposed["project"],
        "module": result["module"],
        "moduleName": result["module_name"],
        "counts": proposed["counts"],
        "issues": proposed["issues"],
        "comparison": {
            "existingProject": current_code,
            "added": len(added),
            "changed": len(changed),
            "removed": len(removed),
            "unchanged": len(after.keys() & before.keys()) - len(changed),
            "examples": {
                "added": added[:10], "changed": changed[:10], "removed": removed[:10],
            },
        },
    }
