from __future__ import annotations

import json
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Callable

from .database import DatabaseConnection, DatabaseTarget, connect, transaction
from .pcs7 import PCS7_KINDS, endpoint_candidates, normalize_endpoint


class RevisionConflictError(Exception):
    def __init__(self, current_revision: int):
        super().__init__("Der Datensatz wurde zwischenzeitlich von einer anderen Person geändert.")
        self.current_revision = current_revision


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date, time)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _as_dict(row: Any) -> dict[str, Any]:
    return {key: _json_value(row[key]) for key in row.keys()}


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _required_int(value: Any) -> int:
    return int(value)


def _boolean(value: Any) -> int:
    if isinstance(value, str):
        return int(value.casefold() in {"1", "true", "ja", "yes", "on"})
    return int(bool(value))


def _string(value: Any) -> str:
    return "" if value is None else str(value).strip()


ACRON_FIELDS: dict[str, tuple[str, Callable[[Any], Any]]] = {
    "identification": ("identification", _string),
    "name": ("name", _string),
    "group": ("group_path", _string),
    "type": ("variable_type", _required_int),
    "formatOut": ("format_out", _required_int),
    "formatTime": ("format_time", _required_int),
    "calcType": ("calc_type", _optional_int),
    "pvalFrom": ("pval_from", _string),
    "pvalTo": ("pval_to", _string),
    "formula": ("formula", _string),
    "pvalHandling": ("pval_handling", _required_int),
    "unit": ("unit", _string),
    "decimals": ("decimals", _required_int),
    "cycleSeconds": ("cycle_seconds", _optional_int),
    "intervalCompMode": ("interval_comp_mode", _optional_int),
    "dailyCompMode": ("daily_comp_mode", _optional_int),
    "monthlyCompMode": ("monthly_comp_mode", _optional_int),
    "yearlyCompMode": ("yearly_comp_mode", _optional_int),
    "comment": ("comment", _string),
}

PROVIDER_FIELDS: dict[str, tuple[str, Callable[[Any], Any]]] = {
    "include": ("include_export", _boolean),
    "varName": ("var_name", _string),
    "description": ("description", _string),
    "group": ("group_path", _string),
    "comment": ("comment", _string),
    "dbpType": ("dbp_type", _required_int),
    "cycleInterval": ("cycle_interval", _optional_int),
    "delta1Active": ("delta1_active", _boolean),
    "delta1Change": ("delta1_change", _string),
}


def _numeric(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "."))
    except ValueError:
        return None


def validation_issues(record: dict[str, Any], duplicate_count: int = 1) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []

    def add(level: str, code: str, message: str) -> None:
        issues.append({"level": level, "code": code, "message": message})

    if not record.get("enabled"):
        return issues
    for field, label in (
        ("acron_identification", "Acron-Identifikation"),
        ("acron_name", "Acron-Name"),
        ("acron_group", "Acron-Gruppe"),
    ):
        if not record.get(field):
            add("error", f"missing_{field}", f"{label} fehlt.")
    if duplicate_count > 1:
        add("error", "duplicate_identification", "Acron-Identifikation ist mehrfach vergeben.")
    if record.get("acron_type") == 0 and not record.get("acron_formula"):
        add("error", "online_formula_missing", "Onlinewert hat keine Prozessdatenverknüpfung.")
    if record.get("acron_type") == 2 and not record.get("acron_formula"):
        add("warning", "calculation_formula_missing", "Formel des Rechenwerts muss in Acron ergänzt werden.")
    if record.get("provider_include_export") and not record.get("provider_var_name"):
        add("error", "provider_var_missing", "Provider-Variablenname fehlt.")
    if record.get("provider_include_export") and not record.get("provider_group"):
        add("error", "provider_group_missing", "Provider-Gruppe fehlt.")
    minimum = _numeric(record.get("acron_pval_from"))
    maximum = _numeric(record.get("acron_pval_to"))
    if minimum is None or maximum is None:
        add("error", "range_invalid", "Messbereich ist nicht numerisch.")
    elif minimum >= maximum:
        add("error", "range_order", "Messbereich von muss kleiner als bis sein.")
    source_short = str(record.get("source_acron_short") or "").strip()
    identification = str(record.get("acron_identification") or "").strip()
    if source_short and source_short != identification:
        add("info", "source_id_normalized", "Identifikation wurde aus BA-Nummer und VG-Short normalisiert.")
    return issues


def _pcs7_context(connection: DatabaseConnection, project_id: int) -> dict[str, Any]:
    imports = connection.execute(
        """
        SELECT id, kind, source_filename, reference_count, imported_at
        FROM pcs7_imports WHERE project_id = ? ORDER BY kind
        """,
        (project_id,),
    ).fetchall()
    imported = [_as_dict(row) for row in imports]
    if not imported:
        return {"active": False, "imports": [], "references": {}, "plans": set(), "blocks": set()}
    references = connection.execute(
        """
        SELECT r.*, i.kind
        FROM pcs7_references r
        JOIN pcs7_imports i ON i.id = r.import_id
        WHERE i.project_id = ?
        """,
        (project_id,),
    ).fetchall()
    by_key: dict[str, list[dict[str, Any]]] = {}
    plans: set[str] = set()
    blocks: set[str] = set()
    for row in references:
        item = _as_dict(row)
        key = str(item["endpoint_key"])
        by_key.setdefault(key, []).append(item)
        plan = normalize_endpoint(str(item["plan_name"]))
        block = normalize_endpoint(str(item["block_name"]))
        plans.add(plan)
        blocks.add(f"{plan}/{block}")
    # Der Parameterexport enthält den vollständigen Anschlusskatalog und aktiviert
    # deshalb die Negativprüfung. Signale/Meldungen allein liefern nur Zusatztreffer.
    return {
        "active": any(row["kind"] == "parameter" for row in imported),
        "imports": imported,
        "references": by_key,
        "plans": plans,
        "blocks": blocks,
    }


def _same_pcs7_reference(left: Any, right: Any) -> bool:
    left_candidates = set(endpoint_candidates(_string(left)))
    right_candidates = set(endpoint_candidates(_string(right)))
    return bool(left_candidates and right_candidates and left_candidates.intersection(right_candidates))


def pcs7_validation(record: dict[str, Any], context: dict[str, Any]) -> tuple[list[dict[str, str]], dict[str, Any]]:
    if not record.get("enabled") or record.get("source_value_type") != "Online":
        return [], {"status": "not_applicable"}
    source = _string(record.get("source_pcs7_variable"))
    if not context["active"]:
        return [], {"status": "not_checked"}

    def issue(code: str, message: str) -> dict[str, str]:
        return {"level": "error", "code": code, "message": message}

    def finish(
        found_issues: list[dict[str, str]], detail: dict[str, Any]
    ) -> tuple[list[dict[str, str]], dict[str, Any]]:
        if found_issues and record.get("pcs7_reviewed"):
            reviewed = [
                {**item, "level": "info", "message": f"{item['message']} Manuell geprüft."}
                for item in found_issues
            ]
            return reviewed, {
                **detail,
                "status": "reviewed",
                "reviewed_at": record.get("pcs7_reviewed_at"),
                "messages": [item["message"] for item in found_issues],
            }
        return found_issues, detail

    candidates = endpoint_candidates(source)
    if not candidates:
        return finish(
            [issue("pcs7_syntax_invalid", "Die PCS7-Variable besitzt nicht das erwartete Format Plan/Baustein.Anschluss#Wert.")],
            {"status": "mismatch"},
        )
    match = next(
        (reference for candidate in candidates for reference in context["references"].get(candidate, [])),
        None,
    )
    issues: list[dict[str, str]] = []
    if not match:
        base = source.split("#", 1)[0]
        plan, _, remainder = base.partition("/")
        block, _, _connection = remainder.partition(".")
        plan_key = normalize_endpoint(plan)
        block_key = f"{plan_key}/{normalize_endpoint(block)}"
        if plan_key not in context["plans"]:
            message = f"PCS7-Plan „{plan}“ wurde in den hochgeladenen Exporten nicht gefunden."
        elif block_key not in context["blocks"]:
            message = f"PCS7-Baustein „{block}“ wurde im Plan „{plan}“ nicht gefunden."
        else:
            message = f"PCS7-Anschluss aus „{source}“ wurde im Export nicht gefunden."
        issues.append(issue("pcs7_reference_missing", message))
    else:
        expected_type = _string(record.get("source_data_type")).upper()
        actual_type = _string(match.get("data_type")).upper()
        aliases = {"BOOLEAN": "BOOL", "FLOAT": "REAL"}
        expected_type = aliases.get(expected_type, expected_type)
        actual_type = aliases.get(actual_type, actual_type)
        if expected_type and actual_type and actual_type != "STRUCT" and expected_type != actual_type:
            issues.append(issue(
                "pcs7_datatype_mismatch",
                f"Datentyp stimmt nicht überein: DPL {expected_type}, PCS7 {actual_type} "
                f"({match['source_sheet']}, Zeile {match['source_row']}).",
            ))
    if source and not _same_pcs7_reference(source, record.get("acron_formula")):
        issues.append(issue(
            "pcs7_acron_reference_mismatch",
            "Die Acron-Prozessdatenverknüpfung verweist nicht mehr auf dieselbe PCS7-Variable wie die DPL.",
        ))
    if record.get("provider_include_export") and source and not _same_pcs7_reference(source, record.get("provider_var_name")):
        issues.append(issue(
            "pcs7_provider_reference_mismatch",
            "Der Provider-Variablenname verweist nicht mehr auf dieselbe PCS7-Variable wie die DPL.",
        ))
    detail = {
        "status": "mismatch" if issues else "confirmed",
        "matched_kind": match.get("kind") if match else None,
        "matched_sheet": match.get("source_sheet") if match else None,
        "matched_row": match.get("source_row") if match else None,
        "matched_data_type": match.get("data_type") if match else None,
    }
    return finish(issues, detail)


def _duplicate_counts(connection: DatabaseConnection, project_id: int) -> dict[str, int]:
    rows = connection.execute(
        """
        SELECT acron_identification, COUNT(*) AS amount
        FROM v_datapoint_editor
        WHERE project_id = ? AND enabled = 1
        GROUP BY acron_identification
        """,
        (project_id,),
    ).fetchall()
    return {row["acron_identification"]: row["amount"] for row in rows}


def list_datapoints(
    database: DatabaseTarget,
    *,
    search: str = "",
    value_type: str = "",
    marker: str = "",
    project_code: str = "braukgraben",
) -> list[dict[str, Any]]:
    connection = connect(database)
    project = connection.execute("SELECT id FROM projects WHERE code = ?", (project_code,)).fetchone()
    if not project:
        connection.close()
        raise ValueError("Projekt nicht gefunden.")
    where = ["project_id = ?"]
    parameters: list[Any] = [project["id"]]
    if search:
        where.append(
            """(
                source_long_name LIKE ? OR acron_identification LIKE ? OR acron_name LIKE ?
                OR source_pcs7_variable LIKE ? OR folder_level1 LIKE ?
            )"""
        )
        term = f"%{search}%"
        parameters.extend([term] * 5)
    if value_type:
        where.append("source_value_type = ?")
        parameters.append(value_type)
    if marker:
        if marker == "orange_changes":
            where.append("has_orange_changes = 1")
        else:
            where.append("marker_color = ?")
            parameters.append(marker)
    records = connection.execute(
        f"SELECT * FROM v_datapoint_editor WHERE {' AND '.join(where)} ORDER BY source_row",
        parameters,
    ).fetchall()
    duplicate_counts = _duplicate_counts(connection, project["id"])
    pcs7_context = _pcs7_context(connection, project["id"])
    result = []
    for row in records:
        record = _as_dict(row)
        issues = validation_issues(
            record,
            duplicate_counts.get(record["acron_identification"], 1),
        )
        pcs7_issues, pcs7_detail = pcs7_validation(record, pcs7_context)
        issues.extend(pcs7_issues)
        record["pcs7"] = pcs7_detail
        record["issues"] = issues
        record["issue_level"] = next(
            (level for level in ("error", "warning", "info") if any(issue["level"] == level for issue in issues)),
            "ok",
        )
        result.append(record)
    connection.close()
    return result


def project_summary(database: DatabaseTarget, project_code: str = "braukgraben") -> dict[str, Any]:
    connection = connect(database)
    project = connection.execute("SELECT * FROM projects WHERE code = ?", (project_code,)).fetchone()
    if not project:
        connection.close()
        raise ValueError("Projekt nicht gefunden.")
    counts = connection.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(enabled) AS enabled,
            SUM(CASE WHEN marker_color = 'yellow' THEN 1 ELSE 0 END) AS yellow,
            SUM(CASE WHEN marker_color = 'orange' THEN 1 ELSE 0 END) AS orange,
            SUM(CASE WHEN has_orange_changes = 1 THEN 1 ELSE 0 END) AS orange_changes,
            SUM(CASE WHEN provider_include_export = 1 AND enabled = 1 THEN 1 ELSE 0 END) AS provider,
            SUM(CASE WHEN provider_include_export = 0 AND enabled = 1 THEN 1 ELSE 0 END) AS acron_only
        FROM v_datapoint_editor WHERE project_id = ?
        """,
        (project["id"],),
    ).fetchone()
    latest_import = connection.execute(
        """
        SELECT source_filename, source_sha256, selected_rows, imported_at
        FROM imports
        WHERE project_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (project["id"],),
    ).fetchone()
    pcs7_context = _pcs7_context(connection, project["id"])
    connection.close()
    records = list_datapoints(database, project_code=project_code)
    issue_counts = {
        level: sum(1 for record in records for issue in record["issues"] if issue["level"] == level)
        for level in ("error", "warning", "info")
    }
    pcs7_records = [record for record in records if record.get("pcs7", {}).get("status") != "not_applicable"]
    pcs7_summary = {
        "active": pcs7_context["active"],
        "imports": [
            {**item, "kind_label": PCS7_KINDS.get(str(item["kind"]), str(item["kind"]))}
            for item in pcs7_context["imports"]
        ],
        "checked": sum(record.get("pcs7", {}).get("status") in {"confirmed", "mismatch", "reviewed"} for record in pcs7_records),
        "confirmed": sum(record.get("pcs7", {}).get("status") == "confirmed" for record in pcs7_records),
        "mismatches": sum(record.get("pcs7", {}).get("status") == "mismatch" for record in pcs7_records),
        "reviewed": sum(record.get("pcs7", {}).get("status") == "reviewed" for record in pcs7_records),
    }
    return {
        "project": _as_dict(project),
        "counts": _as_dict(counts),
        "issues": issue_counts,
        "latestImport": _as_dict(latest_import) if latest_import else None,
        "pcs7": pcs7_summary,
        "valueTypes": sorted({record["source_value_type"] for record in records if record["source_value_type"]}),
    }


def get_datapoint(database: DatabaseTarget, datapoint_id: int) -> dict[str, Any]:
    connection = connect(database)
    row = connection.execute("SELECT * FROM v_datapoint_editor WHERE id = ?", (datapoint_id,)).fetchone()
    if not row:
        connection.close()
        raise KeyError(datapoint_id)
    record = _as_dict(row)
    duplicate = connection.execute(
        """
        SELECT COUNT(*) AS amount FROM v_datapoint_editor
        WHERE project_id = ? AND enabled = 1 AND acron_identification = ?
        """,
        (record["project_id"], record["acron_identification"]),
    ).fetchone()["amount"]
    pcs7_context = _pcs7_context(connection, record["project_id"])
    connection.close()
    record["issues"] = validation_issues(record, duplicate)
    pcs7_issues, pcs7_detail = pcs7_validation(record, pcs7_context)
    record["issues"].extend(pcs7_issues)
    record["pcs7"] = pcs7_detail
    return record


def _audit(
    connection: DatabaseConnection,
    datapoint_id: int,
    entity: str,
    field: str,
    old_value: Any,
    new_value: Any,
) -> None:
    if old_value == new_value:
        return
    connection.execute(
        """
        INSERT INTO audit_log(
            datapoint_id, entity, field_name, old_value, new_value
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            datapoint_id,
            entity,
            field,
            json.dumps(old_value, ensure_ascii=False),
            json.dumps(new_value, ensure_ascii=False),
        ),
    )


def _update_entity(
    connection: DatabaseConnection,
    datapoint_id: int,
    table: str,
    entity: str,
    changes: dict[str, Any],
    field_map: dict[str, tuple[str, Callable[[Any], Any]]],
) -> None:
    current = connection.execute(f"SELECT * FROM {table} WHERE datapoint_id = ?", (datapoint_id,)).fetchone()
    for external_name, raw_value in changes.items():
        if external_name not in field_map:
            continue
        column, converter = field_map[external_name]
        new_value = converter(raw_value)
        old_value = current[column]
        if old_value == new_value:
            continue
        connection.execute(
            f"UPDATE {table} SET {column} = ?, updated_at = CURRENT_TIMESTAMP WHERE datapoint_id = ?",
            (new_value, datapoint_id),
        )
        _audit(connection, datapoint_id, entity, external_name, old_value, new_value)


def update_datapoint(
    database: DatabaseTarget,
    datapoint_id: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    try:
        expected_revision = int(payload["revision"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Die Bearbeitungsversion des Datensatzes fehlt oder ist ungültig.") from exc

    connection = connect(database)
    try:
        current = connection.execute(
            "SELECT enabled, pcs7_reviewed, row_version FROM datapoints WHERE id = ?",
            (datapoint_id,),
        ).fetchone()
        if not current:
            raise KeyError(datapoint_id)
        with transaction(connection):
            cursor = connection.execute(
                """
                UPDATE datapoints
                SET row_version = row_version + 1, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND row_version = ?
                """,
                (datapoint_id, expected_revision),
            )
            if cursor.rowcount != 1:
                latest = connection.execute(
                    "SELECT row_version FROM datapoints WHERE id = ?",
                    (datapoint_id,),
                ).fetchone()
                if not latest:
                    raise KeyError(datapoint_id)
                raise RevisionConflictError(int(latest["row_version"]))

            if "enabled" in payload:
                old_value = current["enabled"]
                new_value = _boolean(payload["enabled"])
                if old_value != new_value:
                    connection.execute("UPDATE datapoints SET enabled = ? WHERE id = ?", (new_value, datapoint_id))
                    _audit(
                        connection,
                        datapoint_id,
                        "datapoint",
                        "enabled",
                        old_value,
                        new_value,
                    )
            if "pcs7Reviewed" in payload:
                old_value = current["pcs7_reviewed"]
                new_value = _boolean(payload["pcs7Reviewed"])
                if old_value != new_value:
                    connection.execute(
                        """
                        UPDATE datapoints
                        SET pcs7_reviewed = ?,
                            pcs7_reviewed_at = CASE WHEN ? = 1 THEN CURRENT_TIMESTAMP ELSE NULL END
                        WHERE id = ?
                        """,
                        (new_value, new_value, datapoint_id),
                    )
                    _audit(connection, datapoint_id, "datapoint", "pcs7Reviewed", old_value, new_value)
            elif payload.get("acron") or payload.get("provider"):
                if current["pcs7_reviewed"]:
                    connection.execute(
                        "UPDATE datapoints SET pcs7_reviewed = 0, pcs7_reviewed_at = NULL WHERE id = ?",
                        (datapoint_id,),
                    )
                    _audit(connection, datapoint_id, "datapoint", "pcs7Reviewed", 1, 0)
            _update_entity(
                connection,
                datapoint_id,
                "acron_configs",
                "acron",
                payload.get("acron", {}),
                ACRON_FIELDS,
            )
            _update_entity(
                connection,
                datapoint_id,
                "provider_configs",
                "provider",
                payload.get("provider", {}),
                PROVIDER_FIELDS,
            )
    finally:
        connection.close()
    return get_datapoint(database, datapoint_id)


def bulk_review_datapoints(
    database: DatabaseTarget,
    entries: list[dict[str, Any]],
    reviewed: bool,
) -> list[dict[str, Any]]:
    if not isinstance(entries, list) or not entries or len(entries) > 500:
        raise ValueError("Für die Sammelprüfung werden 1 bis 500 Datensätze erwartet.")
    normalized: list[tuple[int, int]] = []
    seen: set[int] = set()
    for entry in entries:
        try:
            datapoint_id = int(entry["id"])
            revision = int(entry["revision"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Datensatz-ID oder Bearbeitungsversion der Sammelauswahl ist ungültig.") from exc
        if datapoint_id in seen:
            continue
        seen.add(datapoint_id)
        normalized.append((datapoint_id, revision))

    connection = connect(database)
    new_value = int(bool(reviewed))
    try:
        with transaction(connection):
            for datapoint_id, revision in normalized:
                current = connection.execute(
                    "SELECT pcs7_reviewed FROM datapoints WHERE id = ?",
                    (datapoint_id,),
                ).fetchone()
                if not current:
                    raise KeyError(datapoint_id)
                cursor = connection.execute(
                    """
                    UPDATE datapoints
                    SET pcs7_reviewed = ?,
                        pcs7_reviewed_at = CASE WHEN ? = 1 THEN CURRENT_TIMESTAMP ELSE NULL END,
                        row_version = row_version + 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND row_version = ?
                    """,
                    (new_value, new_value, datapoint_id, revision),
                )
                if cursor.rowcount != 1:
                    latest = connection.execute(
                        "SELECT row_version FROM datapoints WHERE id = ?", (datapoint_id,)
                    ).fetchone()
                    raise RevisionConflictError(int(latest["row_version"]) if latest else revision)
                _audit(
                    connection, datapoint_id, "datapoint", "pcs7Reviewed",
                    current["pcs7_reviewed"], new_value,
                )
    finally:
        connection.close()
    return [get_datapoint(database, datapoint_id) for datapoint_id, _revision in normalized]


def recent_audit(database: DatabaseTarget, limit: int = 30) -> list[dict[str, Any]]:
    connection = connect(database)
    rows = connection.execute(
        """
        SELECT a.*, d.source_long_name, c.identification
        FROM audit_log a
        LEFT JOIN datapoints d ON d.id = a.datapoint_id
        LEFT JOIN acron_configs c ON c.datapoint_id = a.datapoint_id
        ORDER BY a.id DESC LIMIT ?
        """,
        (min(max(limit, 1), 200),),
    ).fetchall()
    connection.close()
    return [_as_dict(row) for row in rows]
