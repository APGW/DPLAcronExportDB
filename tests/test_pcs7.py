from __future__ import annotations

import unittest
import uuid
from pathlib import Path

from dpl_export.database import connect, transaction
from dpl_export.importer import import_project
from dpl_export.pcs7 import _parse_sheet, endpoint_candidates
from dpl_export.service import list_datapoints, project_summary, update_datapoint
from dpl_export.xlsx_reader import Cell, Sheet


ROOT = Path(__file__).resolve().parents[1]
DPL = ROOT / "2026-06-12 DPL_PWK Lippestraße; Braukgraben.xlsx"
ACRON_TEMPLATE = ROOT / "brauckgraben_neu.csv.xlsx"
PROVIDER_TEMPLATE = ROOT / "shhamm_provider1_WINCC60.csv"


def cell(value: object) -> Cell:
    return Cell(value=value, formula=None, style_id=0, fill_rgb=None)


class PCS7ParserTest(unittest.TestCase):
    def test_detects_parameter_export_and_normalizes_struct_member(self) -> None:
        sheet = Sheet("Parameter", {
            1: {0: cell("Plan"), 1: cell("Baustein"), 2: cell("Anschluss"), 3: cell("Datentyp")},
            2: {0: cell("702520ZUL1_NIVM"), 1: cell("I"), 2: cell("PV_Out.Value"), 3: cell("REAL")},
        })
        kind, references = _parse_sheet(sheet) or (None, [])
        self.assertEqual("parameter", kind)
        self.assertEqual("702520zul1_nivm/i.pv_out.value", references[0].endpoint_key)
        self.assertEqual(
            ["702520zul1_nivm/i.pv_out.value", "702520zul1_nivm/i.pv_out"],
            endpoint_candidates("702520ZUL1_NIVM/I.PV_Out#Value"),
        )


class PCS7ConsistencyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.database = ROOT / ".tmp" / f"pcs7-{uuid.uuid4().hex}.sqlite"
        import_project(self.database, DPL, ACRON_TEMPLATE, PROVIDER_TEMPLATE)
        connection = connect(self.database)
        project_id = connection.execute("SELECT id FROM projects WHERE code = 'braukgraben'").fetchone()["id"]
        with transaction(connection):
            cursor = connection.execute(
                """
                INSERT INTO pcs7_imports(project_id, kind, source_filename, source_sha256, reference_count)
                VALUES (?, 'parameter', 'Parameter.xlsx', ?, 1)
                """,
                (project_id, "0" * 64),
            )
            connection.execute(
                """
                INSERT INTO pcs7_references(
                    import_id, endpoint_key, plan_name, block_name, connection_name,
                    data_type, source_sheet, source_row
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cursor.lastrowid,
                    "702520zul_nivm_msg/i.pv_lim1.value",
                    "702520ZUL_NIVM_MSG", "I", "PV_Lim1.Value", "REAL", "Parameter", 42,
                ),
            )
            connection.execute(
                """
                INSERT INTO pcs7_references(
                    import_id, endpoint_key, plan_name, block_name, connection_name,
                    data_type, source_sheet, source_row
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cursor.lastrowid,
                    "pcs7_zusatz/baustein.anschluss",
                    "PCS7_ZUSATZ", "BAUSTEIN", "ANSCHLUSS", "REAL", "Parameter", 43,
                ),
            )
        connection.close()

    def tearDown(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            Path(f"{self.database}{suffix}").unlink(missing_ok=True)

    def test_marks_confirmed_and_missing_pcs7_references(self) -> None:
        records = list_datapoints(self.database)
        confirmed = next(row for row in records if row["acron_identification"] == "702520ZUL_NIVM_MSG_KHST_REL")
        missing = next(row for row in records if row["acron_identification"] == "702520PWK_M1_DFMS_BER_QIST")
        self.assertEqual("confirmed", confirmed["pcs7"]["status"])
        self.assertEqual("mismatch", missing["pcs7"]["status"])
        self.assertTrue(any(issue["code"] == "pcs7_reference_missing" for issue in missing["issues"]))
        summary = project_summary(self.database)["pcs7"]
        self.assertTrue(summary["active"])
        self.assertEqual(45, summary["checked"])
        self.assertEqual(1, summary["confirmed"])

    def test_extra_pcs7_references_outside_dpl_are_ignored(self) -> None:
        records = list_datapoints(self.database)
        self.assertEqual(55, len(records))
        self.assertFalse(any(
            "PCS7_ZUSATZ" in issue["message"]
            for record in records for issue in record["issues"]
        ))

    def test_pcs7_mismatch_can_be_marked_as_manually_reviewed(self) -> None:
        record = next(
            row for row in list_datapoints(self.database)
            if row["acron_identification"] == "702520PWK_M1_DFMS_BER_QIST"
        )
        updated = update_datapoint(
            self.database,
            record["id"],
            {"revision": record["revision"], "pcs7Reviewed": True},
        )
        self.assertEqual("reviewed", updated["pcs7"]["status"])
        self.assertTrue(updated["pcs7_reviewed"])
        self.assertFalse(any(issue["level"] == "error" for issue in updated["issues"]))
        summary = project_summary(self.database)["pcs7"]
        self.assertEqual(1, summary["reviewed"])
        self.assertEqual(43, summary["mismatches"])

    def test_marks_edited_acron_reference_as_inconsistent(self) -> None:
        record = next(
            row for row in list_datapoints(self.database)
            if row["acron_identification"] == "702520ZUL_NIVM_MSG_KHST_REL"
        )
        updated = update_datapoint(
            self.database,
            record["id"],
            {"revision": record["revision"], "acron": {"formula": "ANDERE/QUELLE.PV#Value"}},
        )
        self.assertTrue(any(
            issue["code"] == "pcs7_acron_reference_mismatch" for issue in updated["issues"]
        ))


if __name__ == "__main__":
    unittest.main()
