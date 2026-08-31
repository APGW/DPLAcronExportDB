from __future__ import annotations

import io
import re
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
) -> dict[str, Any]:
    safe_name = validate_xlsx_upload(filename, content)
    database_config = resolve_database(database)
    if data_directory is None:
        data_directory = database_config.path.parent if database_config.path is not None else Path("data")
    import_directory = data_directory / "imports"
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
