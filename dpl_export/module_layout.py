from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .xlsx_reader import WorkbookData


REQUIRED_DPL_COLUMNS = frozenset({
    "folder_level1", "folder_level2", "number", "class_name", "long_name", "vg_short",
    "pcs7_variable", "unit", "decimals", "archive", "cycle_seconds", "delta1",
    "value_type", "pval_handling", "interval_comp", "period_comp", "data_type",
    "range_min", "range_max", "acron_long", "acron_short", "comment",
})
LAYOUT_FIELDS = ("header_row", "first_data_row", "marker_columns", "columns")
METADATA_FIELDS = ("project_name", "ba_number", "acron_prefix")


@dataclass(frozen=True)
class DPLLayout:
    """Validated workbook layout selected for one concrete DPL import."""

    name: str
    metadata_sheet: str
    source_sheet: str
    metadata: dict[str, tuple[int, int]]
    header_row: int
    first_data_row: int
    marker_columns: tuple[int, int]
    columns: dict[str, int | list[int] | None]


def _layout_definitions(dpl: dict[str, Any]) -> list[dict[str, Any]]:
    base = {field: dpl[field] for field in (*LAYOUT_FIELDS, "header_matches", "name") if field in dpl}
    layouts = [base]
    extra_layouts = dpl.get("layouts", [])
    if not isinstance(extra_layouts, list) or any(not isinstance(item, dict) for item in extra_layouts):
        raise ValueError("dpl.layouts muss eine Liste aus Layout-Objekten sein.")
    layouts.extend(extra_layouts)
    return layouts


def layout_count(dpl: dict[str, Any]) -> int:
    return len(_layout_definitions(dpl))


def _validate_cell_position(code: str, label: str, value: Any) -> tuple[int, int]:
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(not isinstance(item, int) for item in value)
        or value[0] < 1
        or value[1] < 0
    ):
        raise ValueError(f"Das Anlagenmodul {code} enthält eine ungültige Metadatenposition für {label}.")
    return value[0], value[1]


def _validate_layout(code: str, layout: dict[str, Any], *, require_signature: bool) -> None:
    if any(field not in layout for field in LAYOUT_FIELDS):
        raise ValueError(f"Die DPL-Layoutkonfiguration des Anlagenmoduls {code} ist unvollständig.")
    if not isinstance(layout["header_row"], int) or layout["header_row"] < 1:
        raise ValueError(f"Das Anlagenmodul {code} enthält eine ungültige Kopfzeile.")
    if not isinstance(layout["first_data_row"], int) or layout["first_data_row"] <= layout["header_row"]:
        raise ValueError(f"Das Anlagenmodul {code} enthält eine ungültige erste Datenzeile.")
    marker_columns = layout["marker_columns"]
    if (
        not isinstance(marker_columns, list)
        or len(marker_columns) != 2
        or any(not isinstance(item, int) for item in marker_columns)
        or marker_columns[0] < 0
        or marker_columns[1] < marker_columns[0]
    ):
        raise ValueError(f"Das Anlagenmodul {code} enthält einen ungültigen Markierungsbereich.")
    columns = layout["columns"]
    if not isinstance(columns, dict) or set(columns) != REQUIRED_DPL_COLUMNS:
        raise ValueError(f"Das Anlagenmodul {code} bildet nicht alle normalisierten Datenpunktfelder ab.")
    for field, value in columns.items():
        valid = value is None or isinstance(value, int) and value >= 0 or (
            isinstance(value, list) and value and all(isinstance(item, int) and item >= 0 for item in value)
        )
        if not valid:
            raise ValueError(f"Das Anlagenmodul {code} enthält eine ungültige Spaltenzuordnung für {field}.")
    header_matches = layout.get("header_matches", {})
    if not isinstance(header_matches, dict) or any(
        not str(column).isdigit() or not isinstance(expected, str) or not expected.strip()
        for column, expected in header_matches.items()
    ):
        raise ValueError(f"Das Anlagenmodul {code} enthält ungültige Layout-Erkennungsmerkmale.")
    if require_signature and not header_matches:
        raise ValueError(f"Mehrere Layouts im Anlagenmodul {code} benötigen eindeutige header_matches.")


def validate_dpl_config(code: str, dpl: Any) -> None:
    if not isinstance(dpl, dict):
        raise ValueError(f"Die DPL-Konfiguration des Anlagenmoduls {code} ist ungültig.")
    required = ("metadata_sheet", "sheet_names", "metadata", *LAYOUT_FIELDS)
    if any(field not in dpl for field in required):
        raise ValueError(f"Die DPL-Konfiguration des Anlagenmoduls {code} ist unvollständig.")
    if not isinstance(dpl["metadata_sheet"], str) or not dpl["metadata_sheet"].strip():
        raise ValueError(f"Das Anlagenmodul {code} enthält kein gültiges Metadatenblatt.")
    sheet_names = dpl["sheet_names"]
    if not isinstance(sheet_names, list) or not sheet_names or any(
        not isinstance(name, str) or not name.strip() for name in sheet_names
    ):
        raise ValueError(f"Das Anlagenmodul {code} enthält keine gültigen Analogwertblätter.")
    metadata = dpl["metadata"]
    if not isinstance(metadata, dict) or set(metadata) != set(METADATA_FIELDS):
        raise ValueError(f"Die Metadatenzuordnung des Anlagenmoduls {code} ist unvollständig.")
    for field in METADATA_FIELDS:
        _validate_cell_position(code, field, metadata[field])
    layouts = _layout_definitions(dpl)
    for layout in layouts:
        _validate_layout(code, layout, require_signature=len(layouts) > 1)


def _normalized_header(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def resolve_layout(module_name: str, dpl: dict[str, Any], workbook: WorkbookData) -> DPLLayout:
    source_sheet = next((name for name in dpl["sheet_names"] if name in workbook.sheets), None)
    if source_sheet is None:
        raise ValueError(f"Kein Analogwertblatt für {module_name} gefunden.")
    sheet = workbook.sheet(source_sheet)
    candidates = []
    for layout in _layout_definitions(dpl):
        header_row = int(layout["header_row"])
        if all(
            _normalized_header(expected) in _normalized_header(sheet.value(header_row, int(column)))
            for column, expected in layout.get("header_matches", {}).items()
        ):
            candidates.append(layout)
    if not candidates:
        raise ValueError(
            f"Die Spaltenstruktur in {source_sheet} passt zu keinem unterstützten Layout von {module_name}."
        )
    if len(candidates) > 1:
        raise ValueError(f"Die Spaltenstruktur in {source_sheet} passt zu mehreren Layouts von {module_name}.")
    selected = candidates[0]
    return DPLLayout(
        name=str(selected.get("name") or dpl.get("name") or "Standard"),
        metadata_sheet=dpl["metadata_sheet"],
        source_sheet=source_sheet,
        metadata={field: tuple(dpl["metadata"][field]) for field in METADATA_FIELDS},
        header_row=int(selected["header_row"]),
        first_data_row=int(selected["first_data_row"]),
        marker_columns=tuple(selected["marker_columns"]),
        columns=dict(selected["columns"]),
    )
