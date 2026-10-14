from __future__ import annotations

import hashlib
import json
import shutil
import unittest
import uuid
from pathlib import Path

from dpl_export.exporter import acron_export, export_history, record_export_run
from dpl_export.importer import import_project
from dpl_export.operations import operations_status
from dpl_export.service import get_datapoint, list_datapoints, list_projects, project_summary, update_datapoint
from dpl_export.upload import preview_uploaded_dpl


ROOT = Path(__file__).resolve().parents[1]
DPL = ROOT / "2026-06-12 DPL_PWK Lippestraße; Braukgraben.xlsx"
ACRON_TEMPLATE = ROOT / "brauckgraben_neu.csv.xlsx"
PROVIDER_TEMPLATE = ROOT / "shhamm_provider1_WINCC60.csv"


class ImprovementTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = ROOT / ".tmp" / f"improvements-{uuid.uuid4().hex}"
        self.directory.mkdir(parents=True)
        self.database = self.directory / "dpl.sqlite"
        self.first = import_project(self.database, DPL, ACRON_TEMPLATE, PROVIDER_TEMPLATE)

    def tearDown(self) -> None:
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_reimport_creates_read_only_project_version(self) -> None:
        original = list_datapoints(self.database)[0]
        second = import_project(
            self.database, DPL, ACRON_TEMPLATE, PROVIDER_TEMPLATE, force=True,
            source_filename="DPL Revision 2.xlsx",
        )

        self.assertEqual("braukgraben-v2", second["project_code"])
        projects = list_projects(self.database)
        self.assertEqual(["braukgraben-v2", "braukgraben"], [item["code"] for item in projects])
        self.assertFalse(projects[0]["read_only"])
        self.assertTrue(projects[1]["read_only"])
        with self.assertRaisesRegex(ValueError, "schreibgeschützt"):
            update_datapoint(
                self.database,
                original["id"],
                {"revision": original["revision"], "acron": {"comment": "nicht erlaubt"}},
            )
        self.assertTrue(get_datapoint(self.database, original["id"])["project_read_only"])

    def test_preview_reports_changes_without_importing(self) -> None:
        preview = preview_uploaded_dpl(
            self.database,
            "DPL Vorschau.xlsx",
            DPL.read_bytes(),
            ACRON_TEMPLATE,
            PROVIDER_TEMPLATE,
            data_directory=self.directory,
            module_code="pwk",
        )

        self.assertEqual(55, preview["counts"]["total"])
        self.assertEqual("braukgraben", preview["comparison"]["existingProject"])
        self.assertEqual(55, preview["comparison"]["unchanged"])
        self.assertEqual(1, len(list_projects(self.database)))
        self.assertEqual(1, project_summary(self.database)["project"]["version_number"])

    def test_export_run_records_reproducible_checksum(self) -> None:
        filename, content = acron_export(self.database)
        run = record_export_run(self.database, None, "acron", filename, content)

        self.assertEqual(hashlib.sha256(content).hexdigest(), run["sha256"])
        history = export_history(self.database)
        self.assertEqual(1, len(history))
        self.assertEqual(filename, history[0]["filename"])
        self.assertEqual(55, history[0]["record_count"])

    def test_operations_status_reports_database_storage_and_backup(self) -> None:
        backup_directory = self.directory / "backups"
        backup_directory.mkdir()
        marker = {"filename": "dpl-acron-test.sql.gz", "createdAt": "2026-10-05T08:00:00Z"}
        (self.directory / "backup-status.json").write_text(json.dumps(marker), encoding="utf-8")

        status = operations_status(
            self.database,
            self.directory,
            backup_directory,
            self.directory / "backup-status.json",
        )

        self.assertEqual("sqlite", status["database"]["backend"])
        self.assertEqual(1, status["projects"]["current"])
        self.assertGreater(status["storage"]["totalBytes"], 0)
        self.assertEqual(marker["filename"], status["backup"]["filename"])
        self.assertEqual(marker["createdAt"], status["backup"]["createdAt"])


if __name__ == "__main__":
    unittest.main()
