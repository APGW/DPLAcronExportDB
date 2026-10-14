from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .database import DatabaseTarget, connect, transaction
from .coordination import synchronized
from .xlsx_reader import Sheet, read_workbook


PCS7_KINDS = {
    "parameter": "Parameter",
    "signals": "Signale",
    "alarms": "Meldungen",
}


@dataclass(frozen=True)
class PCS7Reference:
    endpoint_key: str
    plan_name: str
    block_name: str
    connection_name: str
    data_type: str
    unit: str
    comment: str
    source_sheet: str
    source_row: int


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def normalize_endpoint(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def endpoint_candidates(value: str) -> list[str]:
    raw = _text(value)
    if not raw or "/" not in raw or "." not in raw.split("/", 1)[1]:
        return []
    base, separator, member = raw.partition("#")
    base_key = normalize_endpoint(base)
    if not base_key:
        return []
    candidates = []
    if separator and member:
        candidates.append(f"{base_key}.{normalize_endpoint(member)}")
    candidates.append(base_key)
    return list(dict.fromkeys(candidates))


def _header_map(sheet: Sheet) -> tuple[int, dict[str, int]] | None:
    for row_number in sorted(sheet.rows)[:25]:
        headers = {
            _text(cell.value).casefold(): column
            for column, cell in sheet.rows[row_number].items()
            if _text(cell.value)
        }
        if {"plan", "baustein", "anschluss"}.issubset(headers):
            return row_number, headers
    return None


def _kind_from_headers(headers: dict[str, int]) -> str:
    if "signal" in headers or "absolutadresse" in headers:
        return "signals"
    if "meldetext" in headers or "subnummer" in headers:
        return "alarms"
    return "parameter"


def _parse_sheet(sheet: Sheet) -> tuple[str, list[PCS7Reference]] | None:
    detected = _header_map(sheet)
    if not detected:
        return None
    header_row, headers = detected
    kind = _kind_from_headers(headers)
    references: list[PCS7Reference] = []
    for row_number in sorted(number for number in sheet.rows if number > header_row):
        plan = _text(sheet.value(row_number, headers["plan"]))
        block = _text(sheet.value(row_number, headers["baustein"]))
        connection_name = _text(sheet.value(row_number, headers["anschluss"]))
        if not plan or not block or not connection_name:
            continue
        comment_columns = (
            "anschlusskommentar", "signalkommentar", "meldetext", "bausteinkommentar"
        )
        comment = next(
            (_text(sheet.value(row_number, headers[name])) for name in comment_columns
             if name in headers and _text(sheet.value(row_number, headers[name]))),
            "",
        )
        endpoint = f"{plan}/{block}.{connection_name}"
        references.append(PCS7Reference(
            endpoint_key=normalize_endpoint(endpoint),
            plan_name=plan,
            block_name=block,
            connection_name=connection_name,
            data_type=_text(sheet.value(row_number, headers["datentyp"])) if "datentyp" in headers else "",
            unit=_text(sheet.value(row_number, headers["einheit"])) if "einheit" in headers else "",
            comment=comment,
            source_sheet=sheet.name,
            source_row=row_number,
        ))
    return kind, references


def parse_pcs7_export(path: Path | str) -> tuple[str, list[PCS7Reference]]:
    workbook = read_workbook(path)
    parsed = [result for sheet in workbook.sheets.values() if (result := _parse_sheet(sheet))]
    if not parsed:
        raise ValueError(
            "Kein PCS7-Export erkannt. Erwartet werden die Spalten Plan, Baustein und Anschluss."
        )
    kinds = {kind for kind, references in parsed if references}
    if len(kinds) != 1:
        raise ValueError("Die Arbeitsmappe enthält mehrere oder keine eindeutig erkennbaren PCS7-Exportarten.")
    kind = kinds.pop()
    references = [reference for sheet_kind, rows in parsed if sheet_kind == kind for reference in rows]
    if not references:
        raise ValueError("Der PCS7-Export enthält keine auswertbaren Referenzen.")
    return kind, references


@synchronized
def import_pcs7_export(
    database: DatabaseTarget,
    filename: str,
    content: bytes,
    *,
    data_directory: Path,
    project_code: str | None = None,
) -> dict[str, Any]:
    from .upload import ensure_upload_capacity, validate_xlsx_upload

    safe_name = validate_xlsx_upload(filename, content, "einen PCS7-Export")
    ensure_upload_capacity(data_directory, len(content))
    source_directory = data_directory / "pcs7"
    source_directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    source_path = source_directory / f"{timestamp}-{safe_name}"
    source_path.write_bytes(content)
    try:
        kind, references = parse_pcs7_export(source_path)
        connection = connect(database)
        if project_code:
            project = connection.execute(
                "SELECT id, read_only FROM projects WHERE code = ?", (project_code,)
            ).fetchone()
        else:
            project = connection.execute(
                """
                SELECT id, read_only FROM projects
                WHERE is_current = 1 ORDER BY updated_at DESC, id DESC LIMIT 1
                """
            ).fetchone()
        if not project:
            connection.close()
            raise ValueError("Vor dem PCS7-Export muss eine DPL eingelesen werden.")
        if project["read_only"]:
            connection.close()
            raise ValueError("Diese Projektversion ist schreibgeschützt.")
        with transaction(connection):
            connection.execute(
                """
                UPDATE datapoints
                SET pcs7_reviewed = 0, pcs7_reviewed_at = NULL,
                    row_version = row_version + 1, updated_at = CURRENT_TIMESTAMP
                WHERE project_id = ? AND pcs7_reviewed = 1
                """,
                (project["id"],),
            )
            connection.execute(
                "DELETE FROM pcs7_imports WHERE project_id = ? AND kind = ?",
                (project["id"], kind),
            )
            cursor = connection.execute(
                """
                INSERT INTO pcs7_imports(project_id, kind, source_filename, source_sha256, reference_count)
                VALUES (?, ?, ?, ?, ?)
                """,
                (project["id"], kind, safe_name, hashlib.sha256(content).hexdigest(), len(references)),
            )
            import_id = int(cursor.lastrowid)
            connection.executemany(
                """
                INSERT INTO pcs7_references(
                    import_id, endpoint_key, plan_name, block_name, connection_name,
                    data_type, unit, comment, source_sheet, source_row
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        import_id, row.endpoint_key, row.plan_name, row.block_name,
                        row.connection_name, row.data_type, row.unit, row.comment,
                        row.source_sheet, row.source_row,
                    )
                    for row in references
                ],
            )
        connection.close()
    except Exception:
        source_path.unlink(missing_ok=True)
        raise
    return {
        "kind": kind,
        "kind_label": PCS7_KINDS[kind],
        "source_filename": safe_name,
        "reference_count": len(references),
        "stored_source": str(source_path.resolve()),
    }
