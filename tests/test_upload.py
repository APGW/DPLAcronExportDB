from __future__ import annotations

import json
import http.cookiejar
import shutil
import threading
import unittest
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import quote

from dpl_export.importer import import_project
from dpl_export.auth import AuthConfig, SessionStore, hash_password
from dpl_export.service import list_datapoints, project_summary, update_datapoint
from dpl_export.upload import import_uploaded_dpl, validate_xlsx_upload
from dpl_export.web_server import DPLHTTPServer, DPLRequestHandler


ROOT = Path(__file__).resolve().parents[1]
DPL = ROOT / "2026-06-12 DPL_PWK Lippestraße; Braukgraben.xlsx"
ACRON_TEMPLATE = ROOT / "brauckgraben_neu.csv.xlsx"
PROVIDER_TEMPLATE = ROOT / "shhamm_provider1_WINCC60.csv"
TEST_PASSWORD = "unit-test-password!"
TEST_PASSWORD_HASH = hash_password(TEST_PASSWORD, iterations=100_000)


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
                "sessions": SessionStore(AuthConfig("admin", TEST_PASSWORD_HASH, trusted_proxy=True)),
            },
        )
        self.server = DPLHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        login = urllib.request.Request(
            f"{self.base_url}/api/auth/login",
            data=json.dumps({"username": "admin", "password": TEST_PASSWORD}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "Origin": self.base_url},
        )
        with self.opener.open(login, timeout=10) as response:
            self.login_cookie = response.headers["Set-Cookie"]
            self.csrf_token = json.load(response)["csrfToken"]

    def urlopen(self, request, timeout=10):
        if isinstance(request, str):
            request = urllib.request.Request(request)
        if request.get_method() not in {"GET", "HEAD"}:
            request.add_header("X-CSRF-Token", self.csrf_token)
        return self.opener.open(request, timeout=timeout)

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
        with self.urlopen(request, timeout=20) as response:
            payload = json.load(response)
            self.assertEqual(201, response.status)

        self.assertEqual(55, payload["selected"])
        self.assertEqual("Neue Anlagen-DPL.xlsx", payload["source_filename"])
        self.assertEqual(55, payload["summary"]["counts"]["total"])

    def test_endpoint_previews_without_activating_a_new_version(self) -> None:
        request = urllib.request.Request(
            f"{self.base_url}/api/import-dpl/preview",
            data=DPL.read_bytes(),
            method="POST",
            headers={
                "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "X-Filename": quote("Vorschau.xlsx"),
                "X-Anlage-Module": "pwk",
            },
        )
        with self.urlopen(request, timeout=20) as response:
            payload = json.load(response)
        self.assertEqual(55, payload["counts"]["total"])
        self.assertEqual(55, payload["comparison"]["unchanged"])
        self.assertEqual(1, len(json.load(self.urlopen(f"{self.base_url}/api/projects"))["items"]))

    def test_export_download_creates_a_checksum_history_entry(self) -> None:
        request = urllib.request.Request(
            f"{self.base_url}/api/exports/acron", data=b"", method="POST"
        )
        with self.urlopen(request, timeout=20) as response:
            content = response.read()
            checksum = response.headers["X-Export-SHA256"]
        self.assertTrue(content)
        self.assertEqual(64, len(checksum))
        with self.urlopen(f"{self.base_url}/api/exports/history", timeout=10) as response:
            history = json.load(response)["items"]
        self.assertEqual(checksum, history[0]["sha256"])

    def test_export_get_is_disabled_and_server_blocks_open_errors(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.urlopen(f"{self.base_url}/api/exports/acron", timeout=10)
        self.assertEqual(404, error.exception.code)
        error.exception.close()

        record = next(item for item in list_datapoints(self.database) if item["provider_include_export"])
        update_datapoint(
            self.database,
            record["id"],
            {"revision": record["revision"], "provider": {"varName": ""}},
        )
        request = urllib.request.Request(
            f"{self.base_url}/api/exports/provider", data=b"", method="POST"
        )
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.urlopen(request, timeout=10)
        self.assertEqual(422, error.exception.code)
        payload = json.load(error.exception)
        error.exception.close()
        self.assertGreater(payload["issues"], 0)

    def test_operations_endpoint_reports_runtime_state(self) -> None:
        with self.urlopen(f"{self.base_url}/api/operations", timeout=10) as response:
            payload = json.load(response)
        self.assertEqual("sqlite", payload["database"]["backend"])
        self.assertGreater(payload["storage"]["totalBytes"], 0)

    def test_data_api_requires_login(self) -> None:
        request = urllib.request.Request(f"{self.base_url}/api/project")
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=10)
        self.assertEqual(401, error.exception.code)
        error.exception.close()
        with self.urlopen(f"{self.base_url}/api/project", timeout=10) as response:
            payload = json.load(response)
        self.assertEqual("braukgraben", payload["project"]["code"])

    def test_login_cookie_csrf_and_logout_protect_writes(self) -> None:
        self.assertIn("HttpOnly", self.login_cookie)
        self.assertIn("SameSite=Strict", self.login_cookie)
        self.assertNotIn(TEST_PASSWORD, self.login_cookie)
        unauthenticated_patch = urllib.request.Request(
            f"{self.base_url}/api/datapoints/1",
            data=b"{}",
            method="PATCH",
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(unauthenticated_patch, timeout=10)
        self.assertEqual(401, error.exception.code)
        error.exception.close()

        missing_csrf = urllib.request.Request(
            f"{self.base_url}/api/datapoints/bulk-review",
            data=b'{}',
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.opener.open(missing_csrf, timeout=10)
        self.assertEqual(403, error.exception.code)
        error.exception.close()

        logout = urllib.request.Request(
            f"{self.base_url}/api/auth/logout",
            data=b"{}",
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with self.urlopen(logout, timeout=10) as response:
            self.assertFalse(json.load(response)["authenticated"])
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.opener.open(f"{self.base_url}/api/project", timeout=10)
        self.assertEqual(401, error.exception.code)
        error.exception.close()

    def test_login_rejects_wrong_credentials_and_cross_origin_requests(self) -> None:
        wrong_password = urllib.request.Request(
            f"{self.base_url}/api/auth/login",
            data=json.dumps({"username": "admin", "password": "not-the-password"}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "Origin": self.base_url},
        )
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(wrong_password, timeout=10)
        self.assertEqual(401, error.exception.code)
        self.assertEqual("Benutzername oder Kennwort ist falsch.", json.load(error.exception)["error"])
        error.exception.close()

        foreign_origin = urllib.request.Request(
            f"{self.base_url}/api/auth/login",
            data=json.dumps({"username": "admin", "password": TEST_PASSWORD}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "Origin": "https://fremd.example"},
        )
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(foreign_origin, timeout=10)
        self.assertEqual(403, error.exception.code)
        error.exception.close()

    def test_trusted_loopback_proxy_limits_clients_independently(self) -> None:
        for _ in range(5):
            request = urllib.request.Request(
                f"{self.base_url}/api/auth/login",
                data=json.dumps({"username": "admin", "password": "wrong-password"}).encode("utf-8"),
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Origin": self.base_url,
                    "X-Real-IP": "10.20.0.10",
                },
            )
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request, timeout=10)
            self.assertEqual(401, error.exception.code)
            error.exception.close()

        other_client = urllib.request.Request(
            f"{self.base_url}/api/auth/login",
            data=json.dumps({"username": "admin", "password": TEST_PASSWORD}).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Origin": self.base_url,
                "X-Real-IP": "10.20.0.11",
            },
        )
        with urllib.request.urlopen(other_client, timeout=10) as response:
            self.assertTrue(json.load(response)["authenticated"])

    def test_public_health_endpoint_is_minimal(self) -> None:
        with urllib.request.urlopen(f"{self.base_url}/api/health", timeout=10) as response:
            payload = json.load(response)
        self.assertEqual("ok", payload["status"])
        self.assertEqual("3.7.2", payload["version"])
        self.assertEqual({"status", "version"}, set(payload))

    def test_module_api_only_lists_internal_supported_types(self) -> None:
        with self.urlopen(f"{self.base_url}/api/modules", timeout=10) as response:
            payload = json.load(response)
        self.assertEqual(["pwk", "rue", "rueb"], [item["code"] for item in payload["items"]])
        rueb = next(item for item in payload["items"] if item["code"] == "rueb")
        self.assertEqual(["RÜB", "SKU", "RRB", "HRB", "RKB", "weitere RWB"], rueb["supportedTypes"])
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.urlopen(f"{self.base_url}/api/modules/rue/export", timeout=10)
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
            self.urlopen(request, timeout=10)
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
            self.urlopen(request, timeout=10)
        self.assertEqual(400, error.exception.code)
        error.exception.close()
        self.assertEqual(55, project_summary(self.database)["counts"]["total"])


if __name__ == "__main__":
    unittest.main()
