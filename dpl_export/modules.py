from __future__ import annotations

import io
import json
import os
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .xlsx_reader import WorkbookData


BUILTIN_MODULE_ROOT = Path(__file__).with_name("anlage_modules")
PACKAGE_FILES = ("module.json", "database-template.json", "example-data.json")
MODULE_CODE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
MAX_MODULE_PACKAGE_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class AnlageModule:
    code: str
    name: str
    version: str
    root: Path
    config: dict[str, Any]
    database_template: dict[str, Any]
    example_data: dict[str, Any]
    builtin: bool

    def public_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "version": self.version,
            "description": self.config.get("description", ""),
            "sheetNames": self.config["dpl"]["sheet_names"],
            "builtin": self.builtin,
        }

    def snapshot(self) -> str:
        return json.dumps(
            {
                "module": self.config,
                "database_template": self.database_template,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Ungültige Anlagenmodul-Datei: {path.name}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} muss ein JSON-Objekt enthalten.")
    return value


def _validate(root: Path, *, builtin: bool, expected_code: str | None = None) -> AnlageModule:
    missing = [name for name in PACKAGE_FILES if not (root / name).is_file()]
    if missing:
        raise ValueError(f"Anlagenmodul {root.name} ist unvollständig: {', '.join(missing)}")
    config = _read_json(root / "module.json")
    database_template = _read_json(root / "database-template.json")
    example_data = _read_json(root / "example-data.json")
    code = str(config.get("code", "")).strip().casefold()
    if not MODULE_CODE.fullmatch(code) or code != (expected_code or root.name).casefold():
        raise ValueError(f"Ungültiger Modulcode für {root.name}.")
    for field in ("name", "version", "dpl"):
        if not config.get(field):
            raise ValueError(f"Im Anlagenmodul {code} fehlt {field}.")
    dpl = config["dpl"]
    required_dpl = ("metadata_sheet", "sheet_names", "header_row", "first_data_row", "marker_columns", "columns")
    if not isinstance(dpl, dict) or any(field not in dpl for field in required_dpl):
        raise ValueError(f"Die DPL-Konfiguration des Anlagenmoduls {code} ist unvollständig.")
    required_columns = {
        "folder_level1", "folder_level2", "number", "class_name", "long_name", "vg_short",
        "pcs7_variable", "unit", "decimals", "archive", "cycle_seconds", "delta1",
        "value_type", "pval_handling", "interval_comp", "period_comp", "data_type",
        "range_min", "range_max", "acron_long", "acron_short", "comment",
    }
    if set(dpl["columns"]) != required_columns:
        raise ValueError(f"Das Anlagenmodul {code} bildet nicht alle normalisierten Datenpunktfelder ab.")
    if database_template.get("schema") != "dpl-acron.normalized/v1":
        raise ValueError(f"Das Anlagenmodul {code} verwendet keine unterstützte Datenbankschablone.")
    if example_data.get("schema") != "dpl-acron.example/v1":
        raise ValueError(f"Das Anlagenmodul {code} verwendet kein unterstütztes Beispieldatenformat.")
    return AnlageModule(
        code=code,
        name=str(config["name"]),
        version=str(config["version"]),
        root=root,
        config=config,
        database_template=database_template,
        example_data=example_data,
        builtin=builtin,
    )


def _module_roots(custom_root: Path | None = None) -> Iterable[tuple[Path, bool]]:
    roots: dict[str, tuple[Path, bool]] = {}
    if BUILTIN_MODULE_ROOT.is_dir():
        for path in BUILTIN_MODULE_ROOT.iterdir():
            if path.is_dir():
                roots[path.name.casefold()] = (path, True)
    if custom_root and custom_root.is_dir():
        for path in custom_root.iterdir():
            if path.is_dir():
                roots[path.name.casefold()] = (path, False)
    return roots.values()


def list_modules(custom_root: Path | None = None) -> list[AnlageModule]:
    modules = [_validate(path, builtin=builtin) for path, builtin in _module_roots(custom_root)]
    return sorted(modules, key=lambda item: item.code)


def get_module(code: str, custom_root: Path | None = None) -> AnlageModule:
    normalized = code.strip().casefold()
    return next((module for module in list_modules(custom_root) if module.code == normalized), None) or _missing(code)


def _missing(code: str) -> AnlageModule:
    raise ValueError(f"Anlagenmodul „{code}“ ist nicht installiert.")


def detect_module(workbook: WorkbookData, custom_root: Path | None = None) -> AnlageModule:
    sheet_names = set(workbook.sheets)
    matches = []
    for module in list_modules(custom_root):
        dpl = module.config["dpl"]
        if dpl["metadata_sheet"] in sheet_names and any(name in sheet_names for name in dpl["sheet_names"]):
            matches.append(module)
    if len(matches) != 1:
        installed = ", ".join(module.name for module in list_modules(custom_root))
        if not matches:
            raise ValueError(f"Kein passendes Anlagenmodul anhand der Tabellenblätter erkannt. Installiert: {installed}.")
        raise ValueError("Die DPL passt zu mehreren Anlagenmodulen; bitte die Modulkonfiguration prüfen.")
    return matches[0]


def export_module_package(code: str, custom_root: Path | None = None) -> tuple[str, bytes]:
    module = get_module(code, custom_root)
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in PACKAGE_FILES:
            archive.write(module.root / name, name)
    return f"{module.code}-{module.version}.dplmod", target.getvalue()


def import_module_package(content: bytes, custom_root: Path) -> AnlageModule:
    if not content or len(content) > MAX_MODULE_PACKAGE_BYTES:
        raise ValueError("Das Anlagenmodul ist leer oder größer als 2 MB.")
    custom_root.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = {entry.filename for entry in archive.infolist() if not entry.is_dir()}
            if names != set(PACKAGE_FILES):
                raise ValueError("Das Anlagenmodul muss genau module.json, database-template.json und example-data.json enthalten.")
            module_config = json.loads(archive.read("module.json").decode("utf-8"))
            code = str(module_config.get("code", "")).strip().casefold()
            if not MODULE_CODE.fullmatch(code):
                raise ValueError("Der Modulcode ist ungültig.")
            target = custom_root / code
            replacement = custom_root / f".{code}.new"
            if replacement.exists():
                for path in replacement.iterdir():
                    path.unlink()
                replacement.rmdir()
            replacement.mkdir()
            try:
                for name in PACKAGE_FILES:
                    (replacement / name).write_bytes(archive.read(name))
                _validate(replacement, builtin=False, expected_code=code)
                if target.exists():
                    old = custom_root / f".{code}.old"
                    if old.exists():
                        for path in old.iterdir():
                            path.unlink()
                        old.rmdir()
                    os.replace(target, old)
                    os.replace(replacement, target)
                    for path in old.iterdir():
                        path.unlink()
                    old.rmdir()
                else:
                    os.replace(replacement, target)
            finally:
                if replacement.exists():
                    for path in replacement.iterdir():
                        path.unlink()
                    replacement.rmdir()
    except (zipfile.BadZipFile, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Die Datei ist kein gültiges Anlagenmodul.") from exc
    return _validate(custom_root / code, builtin=False)
