from __future__ import annotations

import csv
import hashlib
import io
import re
from pathlib import Path
from typing import Any, Iterable

from .database import DatabaseConnection, DatabaseTarget, connect, initialize_schema, transaction
from .modules import detect_module, get_module, resolve_dpl_layout
from .xlsx_reader import Sheet, read_workbook, row_marker


VALUE_TYPE_CODES = {
    "online": 0,
    "handwert in acron": 1,
    "handwert": 1,
    "rechenwert in acron": 2,
    "rechenwert": 2,
    "kennzahl": 3,
}

PVAL_HANDLING_CODES = {
    "keine": 0,
    "keine wertebehandlung": 0,
    "differenz zum vorherigen wert": 1,
    "zählerdifferenz mit variablem überlauf": 2,
    "zählerdifferenz mit festem überlauf": 3,
    "verbrauch aus behälterinhalt": 4,
    "betriebsstunden aus zustandsbit": 5,
    "integral bilden": 6,
    "vorgängerwert verwenden": 30,
}

AGGREGATION_CODES = {
    "arithmetischer mittelwert": 7,
    "arithmetischer mittelwert im bereich (5-95%)": 8,
    "logarithmischer mittelwert": 9,
    "letzter wert": 10,
    "häufigkeit": 11,
    "summe": 12,
    "median": 13,
    "perzentilwert": 14,
    "gleitendes maximum (mittelwert)": 15,
    "gleitendes minimum (mittelwert)": 16,
    "gewichteter mittelwert": 24,
    "erster wert": 25,
    "verketten": 28,
    "verketten ohne duplikate": 31,
}

DBP_TYPE_CODES = {
    "BOOL": 1,
    "REAL": 4,
}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _integer(value: Any, default: int | None = None) -> int | None:
    if value in (None, "", "-"):
        return default
    try:
        return int(float(str(value).replace(",", ".")))
    except ValueError:
        return default


def _number_text(value: Any) -> str | None:
    if value in (None, "", "-"):
        return None
    if isinstance(value, (int, float)):
        return str(value)
    normalized = str(value).strip().replace(",", ".")
    try:
        float(normalized)
    except ValueError:
        return None
    return normalized


def _lookup(mapping: dict[str, int], value: Any, default: int | None = None) -> int | None:
    normalized = " ".join(_text(value).casefold().split())
    return mapping.get(normalized, default)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _normalize_prefix(value: Any) -> str:
    prefix = _text(value).replace(" ", "")
    return prefix if prefix.endswith("_") else f"{prefix}_"


def _identification(ba_number: str, vg_short: str) -> str:
    part = re.sub(r"\s+", "", vg_short)
    return part if part.startswith(ba_number) else f"{ba_number}{part}"


def _name(acron_prefix: str, long_name: str) -> str:
    return f"{acron_prefix}{long_name.strip()}"


def _export_filename(project_name: str, kind: str) -> str:
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", project_name).strip(" .")
    stem = re.sub(r"\s+", " ", stem) or "Projekt"
    return f"{stem} {kind}.csv"


def _project_name(metadata_value: Any, source_name: str) -> str:
    name = _text(metadata_value)
    if name and "�" not in name and "\ufffd" not in name and "…" not in name:
        return name
    fallback = Path(source_name).stem
    fallback = re.sub(r"^\d{4}-\d{2}-\d{2}\s*", "", fallback)
    fallback = re.sub(r"^DPL[_\s-]*", "", fallback, flags=re.IGNORECASE)
    fallback = re.sub(r"\s*\([^)]*\).*$", "", fallback)
    fallback = re.sub(r"[_\s-]+Acron(?:\s*-\s*Kopie)?$", "", fallback, flags=re.IGNORECASE)
    return fallback.strip(" _-") or name


def _project_key(ba_number: str, project_name: str) -> str:
    # Der technische Projektschlüssel bleibt für bestehende Installationen
    # kompatibel und menschenlesbar. Die BA-Nummer dient zusätzlich zur
    # Wiedererkennung neuer DPL-Versionen, ist aber nicht der sichtbare Code.
    source = project_name or ba_number
    if ";" in source:
        source = source.rsplit(";", 1)[-1].strip()
    normalized = source.casefold().replace("ß", "ss")
    normalized = normalized.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    normalized = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    return (normalized or "projekt")[:80]


def _provider_group(template_group: str, project_name: str) -> str:
    prefix, separator, _ = template_group.rpartition("|")
    return f"{prefix}|{project_name}" if separator else project_name


def _set_template_group(headers: list[str], defaults: list[str], group: str) -> None:
    if "Group" in headers:
        defaults[headers.index("Group")] = group


def _bounds(data_type: str, source_min: Any, source_max: Any) -> tuple[str, str]:
    minimum = _number_text(source_min)
    maximum = _number_text(source_max)
    if minimum is not None and maximum is not None:
        return minimum, maximum
    if data_type.upper() == "BOOL":
        return "0.00", "1.00"
    return "0.00", "10.00"


def _read_acron_template(path: Path) -> tuple[list[str], list[str], str]:
    workbook = read_workbook(path)
    sheet = next(iter(workbook.sheets.values()))
    headers_by_column = sheet.row_values(1)
    if not headers_by_column or headers_by_column.get(0) != "§Identification":
        raise ValueError("Die Acron-Vorlage besitzt keine gültige Kopfzeile.")
    max_column = max(headers_by_column)
    headers = [_text(headers_by_column.get(column)) for column in range(max_column + 1)]
    defaults_by_column = sheet.row_values(2)
    defaults = [_text(defaults_by_column.get(column)) for column in range(max_column + 1)]
    group_root = defaults[headers.index("Group")].split("|", 1)[0].strip()
    return headers, defaults, group_root


def _read_provider_template(path: Path) -> tuple[list[str], list[str], str]:
    raw = path.read_bytes()
    if raw.startswith(b"\xff\xfe"):
        text = raw[2:].decode("utf-16le")
    elif raw.startswith(b"\xfe\xff"):
        text = raw[2:].decode("utf-16be")
    else:
        text = raw.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text), delimiter="\t"))
    if len(rows) < 2 or not rows[0] or rows[0][0] != "§VarName":
        raise ValueError("Die Provider-Vorlage besitzt keine gültige Kopfzeile.")
    headers, defaults = rows[0], rows[1]
    group_root = defaults[headers.index("Group")].strip()
    return headers, defaults, group_root


def _store_template(
    connection: DatabaseConnection,
    project_id: int,
    kind: str,
    headers: Iterable[str],
    defaults: Iterable[str],
) -> None:
    cursor = connection.execute(
        "INSERT INTO export_templates(project_id, kind) VALUES (?, ?)",
        (project_id, kind),
    )
    template_id = cursor.lastrowid
    connection.executemany(
        """
        INSERT INTO export_template_fields(template_id, ordinal, field_name, default_value)
        VALUES (?, ?, ?, ?)
        """,
        [
            (template_id, ordinal, header, default)
            for ordinal, (header, default) in enumerate(zip(headers, defaults))
        ],
    )


def _column_value(values: dict[int, Any], column: int | list[int] | None) -> Any:
    if column is None:
        return None
    if isinstance(column, list):
        return next((values.get(index) for index in column if _text(values.get(index))), None)
    return values.get(column)


def _selected_rows(
    sheet: Sheet, *, first_data_row: int, marker_columns: list[int]
) -> list[tuple[int, str, dict[int, Any]]]:
    selected = []
    for row_number in sorted(sheet.rows):
        if row_number < first_data_row:
            continue
        marker = row_marker(sheet, row_number, marker_columns[0], marker_columns[1])
        if marker:
            selected.append((row_number, marker, sheet.row_values(row_number)))
    return selected


def _automatic_formulas(
    selected: list[tuple[int, str, dict[int, Any]]],
    ba_number: str,
    columns: dict[str, Any],
    rules: list[dict[str, Any]],
) -> dict[int, str]:
    entries = []
    for source_row, _, values in selected:
        vg_short = _text(_column_value(values, columns["vg_short"]))
        entries.append(
            {
                "row": source_row,
                "identification": _identification(ba_number, vg_short),
                "long_name": _text(_column_value(values, columns["long_name"])).casefold(),
                "value_type": _text(_column_value(values, columns["value_type"])).casefold(),
            }
        )

    formulas: dict[int, str] = {}
    for target in entries:
        if "rechenwert" not in target["value_type"]:
            continue
        for rule in rules:
            if any(term not in target["long_name"] for term in rule.get("target_contains", [])):
                continue
            if rule.get("target_any") and not any(
                term in target["long_name"] for term in rule["target_any"]
            ):
                continue
            pattern = re.compile(rule["source_regex"], re.IGNORECASE)
            sources = []
            for candidate in entries:
                match = pattern.search(candidate["identification"])
                if match:
                    sort_key: Any = candidate["identification"]
                    if rule.get("sort") == "capture_int" and match.groups():
                        sort_key = int(match.group(1))
                    sources.append((sort_key, candidate["row"], candidate["identification"]))
            if sources:
                formulas[target["row"]] = " + ".join(
                    identification for _, _, identification in sorted(sources)
                )
            break
    return formulas


def import_project(
    database: DatabaseTarget,
    dpl_path: Path,
    acron_template_path: Path,
    provider_template_path: Path,
    *,
    force: bool = False,
    source_filename: str | None = None,
    module_root: Path | None = None,
    module_code: str | None = None,
) -> dict[str, int | str]:
    connection = connect(database)
    initialize_schema(connection)

    try:
        workbook = read_workbook(dpl_path)
        detected_module = detect_module(workbook, module_root)
        module = get_module(module_code, module_root) if module_code else detected_module
        if module.code != detected_module.code:
            raise ValueError(
                f"Ausgewählt wurde {module.name}, die DPL entspricht jedoch {detected_module.name}. "
                "Bitte den richtigen Anlagentyp auswählen."
            )
        layout = resolve_dpl_layout(module, workbook)
        metadata = workbook.sheet(layout.metadata_sheet)
        analog_values = workbook.sheet(layout.source_sheet)
        columns = layout.columns
        selected = _selected_rows(
            analog_values,
            first_data_row=layout.first_data_row,
            marker_columns=layout.marker_columns,
        )
        if not selected:
            raise ValueError(
                f"In {layout.source_sheet} wurden keine gelb ausgewählten Zeilen oder orange geänderten Zellen gefunden."
            )

        metadata_map = layout.metadata
        project_name = _project_name(
            metadata.value(*metadata_map["project_name"]), source_filename or dpl_path.name
        )
        ba_number = _text(metadata.value(*metadata_map["ba_number"]))
        acron_prefix = _normalize_prefix(metadata.value(*metadata_map["acron_prefix"]))
        if not ba_number or not project_name:
            raise ValueError("Projektname oder BA-Nummer fehlen in der Anlagenhistorie.")
        requested_key = _project_key(ba_number, project_name)
        existing = connection.execute(
            """
            SELECT * FROM projects
            WHERE logical_key = ? OR ba_number = ?
            ORDER BY version_number DESC, id DESC
            LIMIT 1
            """,
            (requested_key, ba_number),
        ).fetchone()
        logical_key = str(existing["logical_key"] or existing["code"]) if existing else requested_key
        if existing and not force:
            stats = connection.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN provider_include_export = 1 THEN 1 ELSE 0 END) AS provider
                FROM v_datapoint_editor WHERE project_id = ?
                """,
                (existing["id"],),
            ).fetchone()
            connection.close()
            return {
                "project_id": int(existing["id"]),
                "project_code": str(existing["code"]),
                "project_version": int(existing["version_number"]),
                "selected": int(stats["total"] or 0),
                "provider": int(stats["provider"] or 0),
                "status": "existing",
            }
        version_number = int(existing["version_number"]) + 1 if existing else 1
        project_code = logical_key if version_number == 1 else f"{logical_key}-v{version_number}"

        acron_headers, acron_defaults, _ = _read_acron_template(acron_template_path)
        provider_headers, provider_defaults, template_provider_group = _read_provider_template(provider_template_path)
        acron_group_root = project_name
        provider_group_root = _provider_group(template_provider_group, project_name)
        _set_template_group(acron_headers, acron_defaults, acron_group_root)
        _set_template_group(provider_headers, provider_defaults, provider_group_root)
        automatic_formulas = _automatic_formulas(
            selected, ba_number, columns, module.config.get("automatic_formulas", [])
        )
    except Exception:
        connection.close()
        raise

    with transaction(connection):
        if existing:
            connection.execute(
                "UPDATE projects SET is_current = 0, read_only = 1 WHERE logical_key = ?",
                (logical_key,),
            )
        project_cursor = connection.execute(
            """
            INSERT INTO projects(
                code, logical_key, version_number, is_current, read_only,
                module_code, module_version, name, ba_number, acron_prefix, acron_group_root, provider_group_root,
                acron_filename, provider_filename, source_dpl, source_acron_template,
                source_provider_template
            ) VALUES (?, ?, ?, 1, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_code,
                logical_key,
                version_number,
                module.code,
                module.version,
                project_name,
                ba_number,
                acron_prefix,
                acron_group_root,
                provider_group_root,
                _export_filename(project_name, "VG"),
                _export_filename(project_name, "PV"),
                str(dpl_path.resolve()),
                str(acron_template_path.resolve()),
                str(provider_template_path.resolve()),
            ),
        )
        project_id = int(project_cursor.lastrowid)
        import_cursor = connection.execute(
            """
            INSERT INTO imports(project_id, source_filename, source_sha256, selected_rows, module_config_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (project_id, source_filename or dpl_path.name, _sha256(dpl_path), len(selected), module.snapshot()),
        )
        import_id = int(import_cursor.lastrowid)
        _store_template(connection, project_id, "acron", acron_headers, acron_defaults)
        _store_template(connection, project_id, "provider", provider_headers, provider_defaults)

        provider_count = 0
        for source_row, marker_state, values in selected:
            marker = "orange" if marker_state in {"orange", "orange_changed"} else "yellow"
            has_orange_changes = int(marker_state in {"yellow_with_orange", "orange_changed", "orange"})
            long_name = _text(_column_value(values, columns["long_name"]))
            vg_short = _text(_column_value(values, columns["vg_short"]))
            source_data_type = _text(_column_value(values, columns["data_type"])).upper()
            value_type = _text(_column_value(values, columns["value_type"]))
            variable_type = _lookup(VALUE_TYPE_CODES, value_type, 0) or 0
            source_pcs7 = _text(_column_value(values, columns["pcs7_variable"]))
            source_min, source_max = _bounds(
                source_data_type,
                _column_value(values, columns["range_min"]),
                _column_value(values, columns["range_max"]),
            )
            decimals_raw = _column_value(values, columns["decimals"])
            unit = _text(_column_value(values, columns["unit"]))
            decimals = _integer(decimals_raw, 0) or 0
            identification = _identification(ba_number, vg_short)
            acron_name = _name(acron_prefix, long_name)
            folder_level1 = _text(_column_value(values, columns["folder_level1"]))
            acron_group = f"{acron_group_root}|{folder_level1}" if folder_level1 else acron_group_root
            pval_handling = _lookup(PVAL_HANDLING_CODES, _column_value(values, columns["pval_handling"]), 0) or 0
            interval_mode = _lookup(AGGREGATION_CODES, _column_value(values, columns["interval_comp"]))
            period_mode = _lookup(AGGREGATION_CODES, _column_value(values, columns["period_comp"]))
            cycle = _integer(_column_value(values, columns["cycle_seconds"]))
            delta1 = _number_text(_column_value(values, columns["delta1"]))
            format_out = 2 if decimals_raw in (None, "", "-") and unit == "-" else 0
            include_provider = int(variable_type == 0 and bool(source_pcs7))
            provider_count += include_provider

            datapoint_cursor = connection.execute(
                """
                INSERT INTO datapoints(
                    project_id, import_id, source_sheet, source_row, source_number,
                    marker_color, has_orange_changes, folder_level1, folder_level2, class_name,
                    source_long_name, source_vg_short, source_pcs7_variable,
                    source_unit, source_decimals, source_archive, source_cycle_seconds,
                    source_delta1, source_value_type, source_data_type, source_range_min,
                    source_range_max, source_acron_long, source_acron_short
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    import_id,
                    layout.source_sheet,
                    source_row,
                    _text(_column_value(values, columns["number"])),
                    marker,
                    has_orange_changes,
                    folder_level1,
                    _text(_column_value(values, columns["folder_level2"])),
                    _text(_column_value(values, columns["class_name"])),
                    long_name,
                    vg_short,
                    source_pcs7,
                    unit,
                    _text(decimals_raw),
                    _integer(_column_value(values, columns["archive"]), 0) or 0,
                    cycle,
                    _text(_column_value(values, columns["delta1"])),
                    value_type,
                    source_data_type,
                    _number_text(_column_value(values, columns["range_min"])),
                    _number_text(_column_value(values, columns["range_max"])),
                    _text(_column_value(values, columns["acron_long"])),
                    _text(_column_value(values, columns["acron_short"])),
                ),
            )
            datapoint_id = int(datapoint_cursor.lastrowid)
            connection.execute(
                """
                INSERT INTO acron_configs(
                    datapoint_id, identification, name, group_path, variable_type,
                    format_out, format_time, calc_type, pval_from, pval_to, formula,
                    pval_handling, unit, decimals, cycle_seconds, interval_comp_mode,
                    daily_comp_mode, monthly_comp_mode, yearly_comp_mode, comment
                ) VALUES (?, ?, ?, ?, ?, ?, 2, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datapoint_id,
                    identification,
                    acron_name,
                    acron_group,
                    variable_type,
                    format_out,
                    0 if variable_type == 2 else None,
                    source_min,
                    source_max,
                    source_pcs7 if variable_type == 0 else automatic_formulas.get(source_row, ""),
                    pval_handling,
                    unit,
                    decimals,
                    cycle,
                    interval_mode,
                    period_mode,
                    period_mode,
                    period_mode,
                    _text(_column_value(values, columns["comment"])),
                ),
            )
            connection.execute(
                """
                INSERT INTO provider_configs(
                    datapoint_id, include_export, var_name, description, group_path,
                    comment, dbp_type, cycle_interval, delta1_active, delta1_change
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datapoint_id,
                    include_provider,
                    source_pcs7,
                    identification,
                    provider_group_root,
                    long_name,
                    DBP_TYPE_CODES.get(source_data_type, 4),
                    cycle or 60,
                    int(delta1 is not None),
                    delta1,
                ),
            )

        connection.execute(
            """
            INSERT INTO audit_log(
                project_id, datapoint_id, entity, field_name, old_value, new_value
            ) VALUES (?, NULL, 'project', 'import', NULL, ?)
            """,
            (project_id, source_filename or dpl_path.name),
        )

    connection.close()
    return {
        "project_id": project_id,
        "project_code": project_code,
        "project_version": version_number,
        "selected": len(selected),
        "provider": provider_count,
        "module": module.code,
        "module_name": module.name,
        "module_version": module.version,
        "status": "imported",
    }
