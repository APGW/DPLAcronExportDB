from __future__ import annotations

import json
import shutil
import threading
import unittest
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import quote

from dpl_export.importer import import_project
from dpl_export.service import list_datapoints, project_summary, update_datapoint
from dpl_export.upload import import_uploaded_dpl, validate_xlsx_upload
from dpl_export.web_server import DPLHTTPServer, DPLRequestHandler


ROOT = Path(__file__).resolve().parents[1]
DPL = ROOT / "2026-06-12 DPL_PWK Lippestraße; Braukgraben.xlsx"
ACRON_TEMPLATE = ROOT / "brauckgraben_neu.csv.xlsx"
PROVIDER_TEMPLATE = ROOT / "shhamm_provider1_WINCC60.csv"


class UploadTest(unittest.TestCase):
    def setUp(self) -> None:
        self.test_directory = ROOT / ".tmp" / f"upload-{uuid.uuid4().hex}"
        self.test_directory.mkdir(parents=True)
        self.database = self.test_directory / "dpl.sqlite"
        import_project(self.database, DPL, ACRON_TEMPLATE, PROVIDER_TEMPLATE)

    def tearDown(self) -> None:
        shutil.rmtree(self.test_directory, ignore_errors=True)

    def test_valid_upload_is_activated_after_backup(self) -> None:
        first = list_datapoints(self.database)[0]
        update_datapoint(
            self.database,
            first["id"],
            {"revision": first["revision"], "acron": {"comment": "Vor dem Upload"}},
        )

        result = import_uploaded_dpl(
            self.database,
            "Aktuelle DPL.xlsx",
            DPL.read_bytes(),
            ACRON_TEMPLATE,
            PROVIDER_TEMPLATE,
        )

        self.assertEqual(55, result["selected"])
        self.assertEqual(45, result["provider"])
        self.assertEqual("Aktuelle DPL.xlsx", result["source_filename"])
        self.assertIsNone(result["backup_filename"])
        self.assertTrue(Path(result["stored_source"]).exists())
        summary = project_summary(self.database)
        self.assertEqual("Aktuelle DPL.xlsx", summary["latestImport"]["source_filename"])
        self.assertEqual(55, summary["counts"]["total"])

    def test_invalid_upload_keeps_current_database(self) -> None:
        first = list_datapoints(self.database)[0]
        update_datapoint(
            self.database,
            first["id"],
            {"revision": first["revision"], "acron": {"comment": "Bleibt erhalten"}},
        )

        with self.assertRaisesRegex(ValueError, "gültige XLSX"):
            import_uploaded_dpl(
                self.database,
                "defekt.xlsx",
                b"keine Excel-Datei",
                ACRON_TEMPLATE,
                PROVIDER_TEMPLATE,
            )

        current = list_datapoints(self.database)[0]
        self.assertEqual("Bleibt erhalten", current["acron_comment"])

    def test_upload_rejects_wrong_extension(self) -> None:
        with self.assertRaisesRegex(ValueError, "Format .xlsx"):
            validate_xlsx_upload("DPL.xls", DPL.read_bytes())

    def test_workbook_without_dpl_sheets_keeps_current_database(self) -> None:
        with self.assertRaisesRegex(ValueError, "Tabellenblätter"):
            import_uploaded_dpl(
                self.database,
                "keine-dpl.xlsx",
                ACRON_TEMPLATE.read_bytes(),
                ACRON_TEMPLATE,
                PROVIDER_TEMPLATE,
            )
        self.assertEqual(55, project_summary(self.database)["counts"]["total"])

    def test_selected_module_must_match_the_uploaded_dpl(self) -> None:
        with self.assertRaisesRegex(ValueError, "entspricht jedoch"):
            import_uploaded_dpl(
                self.database,
                "falsch-zugeordnet.xlsx",
                DPL.read_bytes(),
                ACRON_TEMPLATE,
                PROVIDER_TEMPLATE,
                module_code="rue",
            )
        self.assertEqual(55, project_summary(self.database)["counts"]["total"])


class UploadEndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self.test_directory = ROOT / ".tmp" / f"endpoint-{uuid.uuid4().hex}"
        self.test_directory.mkdir(parents=True)
        self.database = self.test_directory / "dpl.sqlite"
        import_project(self.database, DPL, ACRON_TEMPLATE, PROVIDER_TEMPLATE)
        handler = type(
            "UploadTestHandler",
            (DPLRequestHandler,),
            {
                "database": self.database,
                "data_directory": self.test_directory,
            },
        )
        self.server = DPLHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        shutil.rmtree(self.test_directory, ignore_errors=True)

    def test_endpoint_imports_xlsx_and_returns_summary(self) -> None:
        request = urllib.request.Request(
            f"{self.base_url}/api/import-dpl",
            data=DPL.read_bytes(),
            method="POST",
            headers={
                "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "X-Filename": quote("Neue Anlagen-DPL.xlsx"),
                "X-Anlage-Module": "pwk",
            },
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.load(response)
            self.assertEqual(201, response.status)

        self.assertEqual(55, payload["selected"])
        self.assertEqual("Neue Anlagen-DPL.xlsx", payload["source_filename"])
        self.assertEqual(55, payload["summary"]["counts"]["total"])

    def test_data_api_is_available_without_login(self) -> None:
        with urllib.request.urlopen(f"{self.base_url}/api/project", timeout=10) as response:
            payload = json.load(response)
        self.assertEqual("braukgraben", payload["project"]["code"])

    def test_health_endpoint_reports_active_project(self) -> None:
        with urllib.request.urlopen(f"{self.base_url}/api/health", timeout=10) as response:
            payload = json.load(response)
        self.assertEqual("ok", payload["status"])
        self.assertEqual("sqlite", payload["backend"])
        self.assertEqual("PWK Lippestraße; Braukgraben", payload["project"])
        self.assertEqual("3.1.1", payload["version"])

    def test_module_api_only_lists_internal_supported_types(self) -> None:
        with urllib.request.urlopen(f"{self.base_url}/api/modules", timeout=10) as response:
            payload = json.load(response)
        self.assertEqual(["pwk", "rue", "rueb"], [item["code"] for item in payload["items"]])
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(f"{self.base_url}/api/modules/rue/export", timeout=10)
        self.assertEqual(404, error.exception.code)
        error.exception.close()

    def test_endpoint_requires_an_explicit_module_selection(self) -> None:
        request = urllib.request.Request(
            f"{self.base_url}/api/import-dpl",
            data=DPL.read_bytes(),
            method="POST",
            headers={
                "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "X-Filename": quote("Ohne Auswahl.xlsx"),
            },
        )
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=10)
        self.assertEqual(400, error.exception.code)
        error.exception.close()

    def test_endpoint_rejects_invalid_file_without_replacement(self) -> None:
        request = urllib.request.Request(
            f"{self.base_url}/api/import-dpl",
            data=b"defekt",
            method="POST",
            headers={
                "Content-Type": "application/octet-stream",
                "X-Filename": quote("defekt.xlsx"),
                "X-Anlage-Module": "pwk",
            },
        )
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=10)
        self.assertEqual(400, error.exception.code)
        error.exception.close()
        self.assertEqual(55, project_summary(self.database)["counts"]["total"])


if __name__ == "__main__":
    unittest.main()
