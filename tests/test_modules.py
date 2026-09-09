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
)
from dpl_export.xlsx_reader import Sheet, WorkbookData


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
