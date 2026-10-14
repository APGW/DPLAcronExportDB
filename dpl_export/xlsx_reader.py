from __future__ import annotations

import colorsys
import posixpath
import re
import threading
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

THEME_COLOR_NAMES = (
    "lt1",
    "dk1",
    "lt2",
    "dk2",
    "accent1",
    "accent2",
    "accent3",
    "accent4",
    "accent5",
    "accent6",
    "hlink",
    "folHlink",
)

MAX_WORKBOOK_ROWS = 50_000
MAX_WORKBOOK_CELLS = 250_000
MAX_SHARED_STRINGS = 100_000
MAX_CELL_TEXT_LENGTH = 32_767
MAX_METADATA_XML_BYTES = 4 * 1024 * 1024
MAX_WORKSHEET_XML_BYTES = 16 * 1024 * 1024
_PARSER_SLOT = threading.BoundedSemaphore(1)


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
    markers = [
        marker_from_rgb(cell.fill_rgb)
        for column, cell in row.items()
        if start_column <= column <= end_column
    ]
    has_orange = "orange" in markers
    row_width = end_column - start_column + 1
    threshold = int(row_width * 0.80)
    if markers.count("yellow") >= threshold:
        return "yellow_with_orange" if has_orange else "yellow"
    if markers.count("orange") >= threshold:
        return "orange"
    # Einzelne orange Zellen kennzeichnen fachlich geänderte Datensätze auch
    # dann, wenn die restliche Zeile nicht vollflächig gelb formatiert ist.
    if has_orange:
        return "orange_changed"
    return None


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    if archive.getinfo("xl/sharedStrings.xml").file_size > MAX_WORKSHEET_XML_BYTES:
        raise ValueError("Die XLSX-Datei enthält zu viele gemeinsame Texte.")
    strings: list[str] = []
    with archive.open("xl/sharedStrings.xml") as source:
        for _event, item in ET.iterparse(source, events=("end",)):
            if item.tag != f"{{{MAIN_NS}}}si":
                continue
            value = "".join(node.text or "" for node in item.findall(".//x:t", NS))
            if len(value) > MAX_CELL_TEXT_LENGTH:
                raise ValueError("Die XLSX-Datei enthält einen zu langen Zelltext.")
            strings.append(value)
            item.clear()
            if len(strings) > MAX_SHARED_STRINGS:
                raise ValueError("Die XLSX-Datei enthält zu viele gemeinsame Texte.")
    return strings


def _metadata_xml(archive: zipfile.ZipFile, name: str) -> ET.Element:
    if archive.getinfo(name).file_size > MAX_METADATA_XML_BYTES:
        raise ValueError("Die XLSX-Datei enthält ungewöhnlich große Metadaten.")
    with archive.open(name) as source:
        return ET.fromstring(source.read(MAX_METADATA_XML_BYTES + 1))


def _theme_colors(archive: zipfile.ZipFile) -> dict[int, str]:
    theme_paths = [name for name in archive.namelist() if name.startswith("xl/theme/theme") and name.endswith(".xml")]
    if not theme_paths:
        return {}
    root = _metadata_xml(archive, sorted(theme_paths)[0])
    scheme = next((node for node in root.iter() if node.tag.endswith("}clrScheme")), None)
    if scheme is None:
        return {}
    by_name: dict[str, str] = {}
    for item in scheme:
        name = item.tag.rsplit("}", 1)[-1]
        color_node = next(iter(item), None)
        if color_node is None:
            continue
        color = color_node.attrib.get("lastClr") or color_node.attrib.get("val")
        if color:
            by_name[name] = color[-6:].upper()
    return {
        index: by_name[name]
        for index, name in enumerate(THEME_COLOR_NAMES)
        if name in by_name
    }


def _apply_tint(rgb: str, tint: float) -> str:
    red, green, blue = (int(rgb[index : index + 2], 16) / 255 for index in (0, 2, 4))
    hue, luminance, saturation = colorsys.rgb_to_hls(red, green, blue)
    luminance = luminance * (1 + tint) if tint < 0 else luminance * (1 - tint) + tint
    tinted = colorsys.hls_to_rgb(hue, max(0, min(1, luminance)), saturation)
    return "".join(f"{round(channel * 255):02X}" for channel in tinted)


def _style_fills(archive: zipfile.ZipFile) -> list[str | None]:
    root = _metadata_xml(archive, "xl/styles.xml")
    theme_colors = _theme_colors(archive)
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
                elif "theme" in foreground.attrib:
                    color = theme_colors.get(int(foreground.attrib["theme"]))
                    if color and "tint" in foreground.attrib:
                        color = _apply_tint(color, float(foreground.attrib["tint"]))
        fills.append(color)

    style_fills: list[str | None] = []
    for cell_format in root.findall("x:cellXfs/x:xf", NS):
        fill_id = int(cell_format.attrib.get("fillId", 0))
        style_fills.append(fills[fill_id] if fill_id < len(fills) else None)
    return style_fills


def _sheet_paths(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    workbook = _metadata_xml(archive, "xl/workbook.xml")
    relationships = _metadata_xml(archive, "xl/_rels/workbook.xml.rels")
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
        try:
            return shared_strings[int(value)]
        except (ValueError, IndexError) as exc:
            raise ValueError("Die XLSX-Datei enthält einen ungültigen Textverweis.") from exc
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
    if not _PARSER_SLOT.acquire(blocking=False):
        raise ValueError("Eine andere XLSX-Datei wird bereits verarbeitet. Bitte erneut versuchen.")
    try:
        with zipfile.ZipFile(path) as archive:
            shared_strings = _shared_strings(archive)
            style_fills = _style_fills(archive)
            sheets: dict[str, Sheet] = {}
            total_rows = 0
            total_cells = 0
            for name, target in _sheet_paths(archive):
                if archive.getinfo(target).file_size > MAX_WORKSHEET_XML_BYTES:
                    raise ValueError("Ein XLSX-Arbeitsblatt ist ungewöhnlich groß.")
                rows: dict[int, dict[int, Cell]] = {}
                with archive.open(target) as source:
                    row_number: int | None = None
                    row: dict[int, Cell] | None = None
                    for event, node in ET.iterparse(source, events=("start", "end")):
                        if event == "start" and node.tag == f"{{{MAIN_NS}}}row":
                            total_rows += 1
                            if total_rows > MAX_WORKBOOK_ROWS:
                                raise ValueError("Die XLSX-Datei enthält zu viele Zeilen.")
                            row_number = int(node.attrib.get("r", total_rows))
                            row = {}
                            continue
                        if event == "end" and node.tag == f"{{{MAIN_NS}}}c" and row is not None:
                            cell_node = node
                            total_cells += 1
                            if total_cells > MAX_WORKBOOK_CELLS:
                                raise ValueError("Die XLSX-Datei enthält zu viele Zellen.")
                            column = column_index(cell_node.attrib["r"])
                            style_id = int(cell_node.attrib.get("s", 0))
                            formula_node = cell_node.find("x:f", NS)
                            formula = formula_node.text if formula_node is not None else None
                            value = _cell_value(cell_node, shared_strings)
                            if len(str(value or "")) > MAX_CELL_TEXT_LENGTH or len(formula or "") > MAX_CELL_TEXT_LENGTH:
                                raise ValueError("Die XLSX-Datei enthält einen zu langen Zellinhalt.")
                            row[column] = Cell(
                                value=value,
                                formula=formula,
                                style_id=style_id,
                                fill_rgb=style_fills[style_id] if style_id < len(style_fills) else None,
                            )
                            cell_node.clear()
                            continue
                        if event == "end" and node.tag == f"{{{MAIN_NS}}}row" and row is not None:
                            assert row_number is not None
                            rows[row_number] = row
                            row = None
                            row_number = None
                            node.clear()
                sheets[name] = Sheet(name=name, rows=rows)
        return WorkbookData(sheets=sheets)
    finally:
        _PARSER_SLOT.release()
