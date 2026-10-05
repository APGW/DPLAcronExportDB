from __future__ import annotations

import codecs
import csv
import hashlib
import io
from pathlib import Path
from typing import Any

from .database import DatabaseTarget, connect


def _project(connection, project_code: str | None = None):
    if project_code:
        project = connection.execute("SELECT * FROM projects WHERE code = ?", (project_code,)).fetchone()
    else:
        project = connection.execute(
            "SELECT * FROM projects WHERE is_current = 1 ORDER BY updated_at DESC, id DESC LIMIT 1"
        ).fetchone()
    if not project:
        raise ValueError(f"Projekt nicht gefunden: {project_code or 'aktuell'}")
    return project


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _six_decimals(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        return f"{float(str(value).replace(',', '.')):.6f}"
    except ValueError:
        return _text(value)


def _encode_tsv(headers: list[str], rows: list[list[str]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter="\t", lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(headers)
    writer.writerows(rows)
    return codecs.BOM_UTF16_LE + output.getvalue().encode("utf-16le")


def _template(connection, project_id: int, kind: str) -> tuple[list[str], dict[str, str]]:
    fields = connection.execute(
        """
        SELECT f.field_name, f.default_value
        FROM export_template_fields f
        JOIN export_templates t ON t.id = f.template_id
        WHERE t.project_id = ? AND t.kind = ?
        ORDER BY f.ordinal
        """,
        (project_id, kind),
    ).fetchall()
    if not fields:
        raise ValueError(f"Exportvorlage fehlt: {kind}")
    headers = [field["field_name"] for field in fields]
    defaults = {field["field_name"]: field["default_value"] or "" for field in fields}
    return headers, defaults


def _range_overrides(record) -> dict[str, str]:
    result = {}
    for prefix in ("PValLim", "IValLim", "DValLim", "MValLim", "YValLim"):
        result[f"{prefix}From"] = _text(record["acron_pval_from"])
        result[f"{prefix}To"] = _text(record["acron_pval_to"])
    return result


def acron_export(database: DatabaseTarget, project_code: str | None = None) -> tuple[str, bytes]:
    connection = connect(database)
    project = _project(connection, project_code)
    headers, defaults = _template(connection, project["id"], "acron")
    records = connection.execute(
        """
        SELECT * FROM v_datapoint_editor
        WHERE project_id = ? AND enabled = 1
        ORDER BY source_row
        """,
        (project["id"],),
    ).fetchall()
    rows: list[list[str]] = []
    for record in records:
        values = dict(defaults)
        values.update(
            {
                "§Identification": _text(record["acron_identification"]),
                "Name": _text(record["acron_name"]),
                "Group": _text(record["acron_group"]),
                "Type": _text(record["acron_type"]),
                "FormatOut": _text(record["acron_format_out"]),
                "FormatTime": _text(record["acron_format_time"]),
                "CalcType": _text(record["acron_calc_type"]),
                "PValFrom": _text(record["acron_pval_from"]),
                "PValTo": _text(record["acron_pval_to"]),
                "Formula": _text(record["acron_formula"]),
                "PValHandling": _text(record["acron_pval_handling"]),
                "Comment": _text(record["acron_comment"]),
                "Unit": _text(record["acron_unit"]),
                "Decimals": _text(record["acron_decimals"]),
                "IUnit": _text(record["acron_unit"]),
                "IDecimals": _text(record["acron_decimals"]),
                "DUnit": _text(record["acron_unit"]),
                "DDecimals": _text(record["acron_decimals"]),
                "MUnit": _text(record["acron_unit"]),
                "MDecimal": _text(record["acron_decimals"]),
                "YUnit": _text(record["acron_unit"]),
                "YDecimal": _text(record["acron_decimals"]),
                "ICompMode": _text(record["acron_interval_comp_mode"]),
                "DCompMode": _text(record["acron_daily_comp_mode"]),
                "MCompMode": _text(record["acron_monthly_comp_mode"]),
                "YCompMode": _text(record["acron_yearly_comp_mode"]),
            }
        )
        values.update(_range_overrides(record))
        interval_active = "1" if record["acron_interval_comp_mode"] is not None else "0"
        values["IComp"] = interval_active
        for index in range(1, 7):
            values[f"IComp{index}"] = interval_active
        values["IComp7"] = "0"
        values["IComp8"] = "0"
        values["DComp"] = "1" if record["acron_daily_comp_mode"] is not None else "0"
        values["MComp"] = "1" if record["acron_monthly_comp_mode"] is not None else "0"
        values["YComp"] = "1" if record["acron_yearly_comp_mode"] is not None else "0"
        rows.append([values.get(header, "") for header in headers])
    connection.close()
    return project["acron_filename"], _encode_tsv(headers, rows)


def provider_export(database: DatabaseTarget, project_code: str | None = None) -> tuple[str, bytes]:
    connection = connect(database)
    project = _project(connection, project_code)
    headers, defaults = _template(connection, project["id"], "provider")
    records = connection.execute(
        """
        SELECT * FROM v_datapoint_editor
        WHERE project_id = ? AND enabled = 1 AND provider_include_export = 1
        ORDER BY source_row
        """,
        (project["id"],),
    ).fetchall()
    rows: list[list[str]] = []
    for record in records:
        values = dict(defaults)
        values.update(
            {
                "§VarName": _text(record["provider_var_name"]),
                "Description": _text(record["provider_description"]),
                "Group": _text(record["provider_group"]),
                "Comment": _text(record["provider_comment"]),
                "DBPType": _text(record["provider_dbp_type"]),
                "CycInterval": _text(record["provider_cycle_interval"]),
                "Dea1": _text(record["provider_delta1_active"]),
                "Dea1ValCh": _six_decimals(record["provider_delta1_change"]),
            }
        )
        rows.append([values.get(header, "") for header in headers])
    connection.close()
    return project["provider_filename"], _encode_tsv(headers, rows)


def write_exports(
    database: DatabaseTarget,
    output_directory: Path,
    project_code: str | None = None,
) -> list[Path]:
    output_directory.mkdir(parents=True, exist_ok=True)
    outputs = []
    for export_function in (acron_export, provider_export):
        filename, content = export_function(database, project_code)
        path = output_directory / filename
        path.write_bytes(content)
        outputs.append(path)
    return outputs


def record_export_run(
    database: DatabaseTarget,
    project_code: str | None,
    kind: str,
    filename: str,
    content: bytes,
) -> dict[str, Any]:
    from .service import project_summary

    summary = project_summary(database, project_code)
    project = summary["project"]
    count_key = "enabled" if kind == "acron" else "provider"
    connection = connect(database)
    latest_import = connection.execute(
        "SELECT id FROM imports WHERE project_id = ? ORDER BY id DESC LIMIT 1", (project["id"],)
    ).fetchone()
    cursor = connection.execute(
        """
        INSERT INTO export_runs(
            project_id, import_id, kind, filename, sha256, record_count,
            open_errors, open_warnings, module_code, module_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            project["id"],
            latest_import["id"] if latest_import else None,
            kind,
            filename,
            hashlib.sha256(content).hexdigest(),
            int(summary["counts"][count_key] or 0),
            int(summary["issues"]["error"] or 0),
            int(summary["issues"]["warning"] or 0),
            project["module_code"],
            project["module_version"],
        ),
    )
    connection.commit()
    run_id = int(cursor.lastrowid)
    connection.close()
    return {
        "id": run_id,
        "sha256": hashlib.sha256(content).hexdigest(),
        "recordCount": int(summary["counts"][count_key] or 0),
    }


def export_history(
    database: DatabaseTarget, project_code: str | None = None, limit: int = 20
) -> list[dict[str, Any]]:
    connection = connect(database)
    project = _project(connection, project_code)
    rows = connection.execute(
        """
        SELECT id, kind, filename, sha256, record_count, open_errors, open_warnings,
               module_code, module_version, exported_at
        FROM export_runs WHERE project_id = ? ORDER BY id DESC LIMIT ?
        """,
        (project["id"], min(max(int(limit), 1), 100)),
    ).fetchall()
    result = [{key: row[key] for key in row.keys()} for row in rows]
    connection.close()
    return result
