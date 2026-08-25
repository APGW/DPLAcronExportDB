from __future__ import annotations

import colorsys
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS = {"x": MAIN_NS}
REL_NS = {"r": "http://schemas.openxmlformats.org/package/2006/relationships"}
DOC_REL_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"

# Values used by the legacy indexed palette in the supplied workbook.
INDEXED_COLORS = {
    5: "FFFF00",
    6: "FFFF00",
    40: "FFCC99",
    44: "FF9900",
    45: "FFCC00",
}


@dataclass(frozen=True)
class Cell:
    value: Any
    formula: str | None
    style_id: int
    fill_rgb: str | None


@dataclass
class Sheet:
    name: str
    rows: dict[int, dict[int, Cell]]

    def cell(self, row: int, column: int) -> Cell | None:
        return self.rows.get(row, {}).get(column)

    def value(self, row: int, column: int, default: Any = None) -> Any:
        cell = self.cell(row, column)
        return default if cell is None or cell.value is None else cell.value

    def row_values(self, row: int) -> dict[int, Any]:
        return {column: cell.value for column, cell in self.rows.get(row, {}).items()}


@dataclass
class WorkbookData:
    sheets: dict[str, Sheet]

    def sheet(self, name: str) -> Sheet:
        try:
            return self.sheets[name]
        except KeyError as exc:
            raise ValueError(f"Arbeitsblatt fehlt: {name}") from exc


def column_index(cell_reference: str) -> int:
    match = re.match(r"[A-Z]+", cell_reference)
    if not match:
        raise ValueError(f"Ungültige Zelladresse: {cell_reference}")
    value = 0
    for character in match.group(0):
        value = value * 26 + ord(character) - 64
    return value - 1


def marker_from_rgb(rgb: str | None) -> str | None:
    if not rgb:
        return None
    rgb = rgb[-6:].upper()
    try:
        red, green, blue = (int(rgb[index : index + 2], 16) / 255 for index in (0, 2, 4))
    except ValueError:
        return None
    hue, saturation, value = colorsys.rgb_to_hsv(red, green, blue)
    degrees = hue * 360
    if saturation < 0.55 or value < 0.65:
        return None
    if 50 <= degrees <= 72:
        return "yellow"
    if 15 <= degrees < 50:
        return "orange"
    return None


def row_marker(sheet: Sheet, row_number: int, start_column: int = 0, end_column: int = 57) -> str | None:
    row = sheet.rows.get(row_number, {})
    leading = row.get(start_column)
    if leading:
        marker = marker_from_rgb(leading.fill_rgb)
        if marker:
            return marker

    markers = [
        marker_from_rgb(cell.fill_rgb)
        for column, cell in row.items()
        if start_column <= column <= end_column
    ]
    markers = [marker for marker in markers if marker]
    row_width = end_column - start_column + 1
    for marker in ("yellow", "orange"):
        if markers.count(marker) >= int(row_width * 0.80):
            return marker
    return None


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    return [
        "".join(node.text or "" for node in item.findall(".//x:t", NS))
        for item in root.findall("x:si", NS)
    ]


def _style_fills(archive: zipfile.ZipFile) -> list[str | None]:
    root = ET.fromstring(archive.read("xl/styles.xml"))
    fills: list[str | None] = []
    for fill in root.findall("x:fills/x:fill", NS):
        pattern = fill.find("x:patternFill", NS)
        color: str | None = None
        if pattern is not None and pattern.attrib.get("patternType") == "solid":
            foreground = pattern.find("x:fgColor", NS)
            if foreground is not None:
                if "rgb" in foreground.attrib:
                    color = foreground.attrib["rgb"][-6:].upper()
                elif "indexed" in foreground.attrib:
                    color = INDEXED_COLORS.get(int(foreground.attrib["indexed"]))
        fills.append(color)

    style_fills: list[str | None] = []
    for cell_format in root.findall("x:cellXfs/x:xf", NS):
        fill_id = int(cell_format.attrib.get("fillId", 0))
        style_fills.append(fills[fill_id] if fill_id < len(fills) else None)
    return style_fills


def _sheet_paths(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {
        relationship.attrib["Id"]: relationship.attrib["Target"]
        for relationship in relationships.findall("r:Relationship", REL_NS)
    }
    result = []
    for sheet in workbook.findall("x:sheets/x:sheet", NS):
        target = targets[sheet.attrib[DOC_REL_ID]].replace("\\", "/")
        if target.startswith("/"):
            target = target.lstrip("/")
        elif not target.startswith("xl/"):
            target = posixpath.normpath(posixpath.join("xl", target))
        result.append((sheet.attrib["name"], target))
    return result


def _cell_value(cell: ET.Element, shared_strings: list[str]) -> Any:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(".//x:t", NS))
    value_node = cell.find("x:v", NS)
    if value_node is None:
        return None
    value = value_node.text or ""
    if cell_type == "s":
        return shared_strings[int(value)]
    if cell_type == "b":
        return value == "1"
    if cell_type in {"str", "e"}:
        return value
    try:
        number = float(value)
        return int(number) if number.is_integer() else number
    except ValueError:
        return value


def read_workbook(path: Path | str) -> WorkbookData:
    with zipfile.ZipFile(path) as archive:
        shared_strings = _shared_strings(archive)
        style_fills = _style_fills(archive)
        sheets: dict[str, Sheet] = {}
        for name, target in _sheet_paths(archive):
            root = ET.fromstring(archive.read(target))
            rows: dict[int, dict[int, Cell]] = {}
            for row_node in root.findall("x:sheetData/x:row", NS):
                row_number = int(row_node.attrib["r"])
                row: dict[int, Cell] = {}
                for cell_node in row_node.findall("x:c", NS):
                    column = column_index(cell_node.attrib["r"])
                    style_id = int(cell_node.attrib.get("s", 0))
                    formula_node = cell_node.find("x:f", NS)
                    row[column] = Cell(
                        value=_cell_value(cell_node, shared_strings),
                        formula=formula_node.text if formula_node is not None else None,
                        style_id=style_id,
                        fill_rgb=style_fills[style_id] if style_id < len(style_fills) else None,
                    )
                rows[row_number] = row
            sheets[name] = Sheet(name=name, rows=rows)
    return WorkbookData(sheets=sheets)
