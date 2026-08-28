from __future__ import annotations

import csv
import hashlib
import io
import re
from pathlib import Path
from typing import Any, Iterable

from .database import DatabaseConnection, DatabaseTarget, connect, initialize_schema, transaction
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


def _selected_rows(sheet: Sheet) -> list[tuple[int, str, dict[int, Any]]]:
    selected = []
    for row_number in sorted(sheet.rows):
        if row_number < 5:
            continue
        marker = row_marker(sheet, row_number)
        if marker:
            selected.append((row_number, marker, sheet.row_values(row_number)))
    return selected


def _automatic_formulas(
    selected: list[tuple[int, str, dict[int, Any]]], ba_number: str
) -> dict[int, str]:
    entries = []
    for source_row, _, values in selected:
        vg_short = _text(values.get(8)) or _text(values.get(5))
        entries.append(
            {
                "row": source_row,
                "identification": _identification(ba_number, vg_short),
                "long_name": _text(values.get(4)).casefold(),
                "value_type": _text(values.get(40)).casefold(),
            }
        )

    formulas: dict[int, str] = {}
    rules = (
        ("fördermenge", re.compile(r"_M(\d+)_.*BER_VOL$", re.IGNORECASE)),
        ("förderleistung", re.compile(r"_M(\d+)_.*BER_QIST$", re.IGNORECASE)),
    )
    for target in entries:
        if "rechenwert" not in target["value_type"]:
            continue
        for label, pattern in rules:
            machine_total = "aller maschinen" in target["long_name"]
            single_system = "fördersystem" in target["long_name"]
            if label not in target["long_name"] or not (machine_total or single_system):
                continue
            sources = []
            for candidate in entries:
                match = pattern.search(candidate["identification"])
                if match:
                    sources.append((int(match.group(1)), candidate["row"], candidate["identification"]))
            if sources:
                formulas[target["row"]] = " + ".join(
                    identification for _, _, identification in sorted(sources)
                )
            break
        if "stromverbrauch" in target["long_name"]:
            energy_sources = sorted(
                candidate["identification"]
                for candidate in entries
                if re.search(r"_WEL_(?:HT|NT)_HAND$", candidate["identification"], re.IGNORECASE)
            )
            if energy_sources:
                formulas[target["row"]] = " + ".join(energy_sources)
    return formulas


def import_project(
    database: DatabaseTarget,
    dpl_path: Path,
    acron_template_path: Path,
    provider_template_path: Path,
    *,
    force: bool = False,
    source_filename: str | None = None,
) -> dict[str, int | str]:
    connection = connect(database)
    initialize_schema(connection)
    existing = connection.execute("SELECT id FROM projects WHERE code = 'braukgraben'").fetchone()
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
            "selected": int(stats["total"] or 0),
            "provider": int(stats["provider"] or 0),
            "status": "existing",
        }

    try:
        workbook = read_workbook(dpl_path)
        try:
            metadata = workbook.sheet("Info und Historie AnlagenDPL")
            analog_values = workbook.sheet("PWK_Analogwerte")
        except ValueError as exc:
            raise ValueError(
                "Die DPL benötigt die Tabellenblätter „Info und Historie AnlagenDPL“ und „PWK_Analogwerte“."
            ) from exc
        selected = _selected_rows(analog_values)
        if not selected:
            raise ValueError(
                "In PWK_Analogwerte wurden keine gelb ausgewählten Zeilen oder orange geänderten Zellen gefunden."
            )

        project_name = _project_name(metadata.value(3, 2), source_filename or dpl_path.name)
        ba_number = _text(metadata.value(5, 2))
        acron_prefix = _normalize_prefix(metadata.value(6, 2))
        if not ba_number or not project_name:
            raise ValueError("Projektname oder BA-Nummer fehlen in der Anlagenhistorie.")

        acron_headers, acron_defaults, _ = _read_acron_template(acron_template_path)
        provider_headers, provider_defaults, template_provider_group = _read_provider_template(provider_template_path)
        acron_group_root = project_name
        provider_group_root = _provider_group(template_provider_group, project_name)
        _set_template_group(acron_headers, acron_defaults, acron_group_root)
        _set_template_group(provider_headers, provider_defaults, provider_group_root)
        automatic_formulas = _automatic_formulas(selected, ba_number)
    except Exception:
        connection.close()
        raise

    with transaction(connection):
        if existing:
            connection.execute("DELETE FROM projects WHERE id = ?", (existing["id"],))
        project_cursor = connection.execute(
            """
            INSERT INTO projects(
                code, name, ba_number, acron_prefix, acron_group_root, provider_group_root,
                acron_filename, provider_filename, source_dpl, source_acron_template,
                source_provider_template
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "braukgraben",
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
            INSERT INTO imports(project_id, source_filename, source_sha256, selected_rows)
            VALUES (?, ?, ?, ?)
            """,
            (project_id, source_filename or dpl_path.name, _sha256(dpl_path), len(selected)),
        )
        import_id = int(import_cursor.lastrowid)
        _store_template(connection, project_id, "acron", acron_headers, acron_defaults)
        _store_template(connection, project_id, "provider", provider_headers, provider_defaults)

        provider_count = 0
        for source_row, marker_state, values in selected:
            marker = "orange" if marker_state in {"orange", "orange_changed"} else "yellow"
            has_orange_changes = int(marker_state in {"yellow_with_orange", "orange_changed", "orange"})
            long_name = _text(values.get(4))
            vg_short = _text(values.get(8)) or _text(values.get(5))
            source_data_type = _text(values.get(44)).upper()
            value_type = _text(values.get(40))
            variable_type = _lookup(VALUE_TYPE_CODES, value_type, 0) or 0
            source_pcs7 = _text(values.get(29))
            source_min, source_max = _bounds(source_data_type, values.get(48), values.get(49))
            decimals = _integer(values.get(11), 0) or 0
            identification = _identification(ba_number, vg_short)
            acron_name = _name(acron_prefix, long_name)
            folder_level1 = _text(values.get(0))
            acron_group = f"{acron_group_root}|{folder_level1}" if folder_level1 else acron_group_root
            pval_handling = _lookup(PVAL_HANDLING_CODES, values.get(41), 0) or 0
            interval_mode = _lookup(AGGREGATION_CODES, values.get(42))
            period_mode = _lookup(AGGREGATION_CODES, values.get(43))
            cycle = _integer(values.get(32))
            delta1 = _number_text(values.get(34))
            format_out = 2 if values.get(11) in (None, "", "-") and _text(values.get(10)) == "-" else 0
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
                ) VALUES (?, ?, 'PWK_Analogwerte', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    import_id,
                    source_row,
                    _text(values.get(2)),
                    marker,
                    has_orange_changes,
                    folder_level1,
                    _text(values.get(1)),
                    _text(values.get(3)),
                    long_name,
                    vg_short,
                    source_pcs7,
                    _text(values.get(10)),
                    _text(values.get(11)),
                    _integer(values.get(31), 0) or 0,
                    cycle,
                    _text(values.get(34)),
                    value_type,
                    source_data_type,
                    _number_text(values.get(48)),
                    _number_text(values.get(49)),
                    _text(values.get(38)),
                    _text(values.get(39)),
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
                    _text(values.get(10)),
                    decimals,
                    cycle,
                    interval_mode,
                    period_mode,
                    period_mode,
                    period_mode,
                    _text(values.get(50)),
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
                datapoint_id, entity, field_name, old_value, new_value
            ) VALUES (NULL, 'project', 'import', NULL, ?)
            """,
            (source_filename or dpl_path.name,),
        )

    connection.close()
    return {
        "project_id": project_id,
        "selected": len(selected),
        "provider": provider_count,
        "status": "imported",
    }
