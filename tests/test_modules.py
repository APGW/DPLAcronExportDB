from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path

from dpl_export.modules import (
    detect_module,
    export_module_package,
    import_module_package,
    list_modules,
    resolve_dpl_layout,
)
from dpl_export.xlsx_reader import Cell, Sheet, WorkbookData


ROOT = Path(__file__).resolve().parents[1]


class AnlageModuleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = ROOT / ".tmp" / f"modules-{uuid.uuid4().hex}"
        self.directory.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_builtin_modules_have_one_normalized_contract(self) -> None:
        modules = list_modules()
        self.assertEqual(["pwk", "rue", "rueb"], [module.code for module in modules])
        self.assertTrue(all(module.database_template["schema"] == "dpl-acron.normalized/v1" for module in modules))
        self.assertTrue(all(module.example_data["datapoints"] for module in modules))

    def test_sheet_names_detect_each_supported_module(self) -> None:
        for analog_sheet, expected in (
            ("PWK_Analogwerte", "pwk"),
            ("RUE Analogwerte", "rue"),
            ("RWB Analogwerte", "rueb"),
        ):
            workbook = WorkbookData({
                "Info und Historie AnlagenDPL": Sheet("Info und Historie AnlagenDPL", {}),
                analog_sheet: Sheet(analog_sheet, {}),
            })
            self.assertEqual(expected, detect_module(workbook).code)

    def test_rueb_module_covers_all_rainwater_treatment_types(self) -> None:
        module = next(item for item in list_modules() if item.code == "rueb")
        self.assertEqual(
            ["RÜB", "SKU", "RRB", "HRB", "RKB", "weitere RWB"],
            module.public_dict()["supportedTypes"],
        )

    def test_rueb_module_resolves_v71_and_legacy_column_layouts(self) -> None:
        module = next(item for item in list_modules() if item.code == "rueb")

        def workbook(headers: dict[int, str]) -> WorkbookData:
            row = {column: Cell(value, None, 0, None) for column, value in headers.items()}
            return WorkbookData({
                "Info und Historie AnlagenDPL": Sheet("Info und Historie AnlagenDPL", {}),
                "RWB Analogwerte": Sheet("RWB Analogwerte", {3: row}),
            })

        current = resolve_dpl_layout(module, workbook({
            29: "PCS7 Variable", 31: "Soll archiviert werden?", 40: "Wertetyp in Acron",
        }))
        legacy = resolve_dpl_layout(module, workbook({
            24: "PCS7 Variable", 26: "Soll archiviert werden?", 35: "Wertetyp in Acron",
        }))
        self.assertEqual(29, current.columns["pcs7_variable"])
        self.assertEqual((0, 56), current.marker_columns)
        self.assertEqual("Standard", current.name)
        self.assertEqual(24, legacy.columns["pcs7_variable"])
        self.assertEqual((0, 45), legacy.marker_columns)
        self.assertEqual("V7.0 / Bestandslayout", legacy.name)

    def test_module_package_can_be_exported_and_hot_replaced(self) -> None:
        filename, content = export_module_package("rue")
        self.assertTrue(filename.endswith(".dplmod"))
        imported = import_module_package(content, self.directory)
        self.assertEqual("rue", imported.code)
        self.assertFalse(imported.builtin)
        effective = next(module for module in list_modules(self.directory) if module.code == "rue")
        self.assertFalse(effective.builtin)


if __name__ == "__main__":
    unittest.main()
