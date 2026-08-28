from __future__ import annotations

import csv
import io
import json
import unittest
import uuid
from pathlib import Path

from dpl_export.exporter import acron_export, provider_export
from dpl_export.importer import _project_name, import_project
from dpl_export.service import (
    RevisionConflictError,
    list_datapoints,
    project_summary,
    recent_audit,
    update_datapoint,
)


ROOT = Path(__file__).resolve().parents[1]
DPL = ROOT / "2026-06-12 DPL_PWK Lippestraße; Braukgraben.xlsx"
ACRON_TEMPLATE = ROOT / "brauckgraben_neu.csv.xlsx"
PROVIDER_TEMPLATE = ROOT / "shhamm_provider1_WINCC60.csv"


def decode_tsv(content: bytes) -> list[dict[str, str]]:
    text = content.decode("utf-16")
    return list(csv.DictReader(io.StringIO(text), delimiter="\t"))


class PipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        test_root = ROOT / ".tmp"
        test_root.mkdir(parents=True, exist_ok=True)
        self.database = test_root / f"pipeline-{uuid.uuid4().hex}.sqlite"
        self.result = import_project(self.database, DPL, ACRON_TEMPLATE, PROVIDER_TEMPLATE)

    def tearDown(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            Path(f"{self.database}{suffix}").unlink(missing_ok=True)

    def test_imports_only_fully_marked_analog_rows(self) -> None:
        self.assertEqual(55, self.result["selected"])
        self.assertEqual(45, self.result["provider"])
        records = list_datapoints(self.database)
        self.assertEqual(55, len(records))
        self.assertEqual({"yellow"}, {record["marker_color"] for record in records})
        orange_changed = {
            record["acron_identification"]
            for record in records
            if record["has_orange_changes"]
        }
        self.assertEqual(
            {
                "702520PWK_M1_DFMS_BER_VOL",
                "702520PWK_M1_DFMS_BER_QIST",
                "702520PWK_M2_DFMS_BER_VOL",
                "702520PWK_M2_BER_QIST",
            },
            orange_changed,
        )
        self.assertEqual(45, sum(record["source_value_type"] == "Online" for record in records))
        self.assertEqual(4, sum(record["source_value_type"] == "Rechenwert in Acron" for record in records))
        self.assertEqual(6, sum(record["source_value_type"] == "Handwert in Acron" for record in records))

    def test_existing_import_status_is_json_serializable(self) -> None:
        result = import_project(self.database, DPL, ACRON_TEMPLATE, PROVIDER_TEMPLATE)
        self.assertEqual("existing", result["status"])
        self.assertEqual(55, result["selected"])
        self.assertEqual(45, result["provider"])
        self.assertIsInstance(result["project_id"], int)
        json.dumps(result)

    def test_representative_acron_record_matches_supplied_template(self) -> None:
        _, generated = acron_export(self.database)
        generated_row = next(
            row for row in decode_tsv(generated)
            if row["§Identification"] == "702520ZUL_NIVM_MSG_KHST_REL"
        )
        self.assertEqual("PWKHAMLsB_Kritische Höchststaumarke relativ", generated_row["Name"])
        self.assertEqual(
            "PWK Lippestraße; Braukgraben|1. Allgemeines / Niveaus",
            generated_row["Group"],
        )
        self.assertEqual("702520ZUL_NIVM_MSG/I.PV_Lim1#Value", generated_row["Formula"])
        self.assertEqual("0.00", generated_row["PValFrom"])
        self.assertEqual("10.00", generated_row["PValTo"])
        self.assertEqual("24", generated_row["ICompMode"])
        self.assertEqual("7", generated_row["DCompMode"])

    def test_representative_provider_record_matches_supplied_template(self) -> None:
        _, generated = provider_export(self.database)
        generated_row = next(
            row for row in decode_tsv(generated)
            if row["§VarName"] == "702520ZUL_NIVM_MSG/I.PV_Lim1#Value"
        )
        expected = decode_tsv(PROVIDER_TEMPLATE.read_bytes())[0]
        expected["Group"] = "PV_vPLS_SH_Hamm|PWK Lippestraße; Braukgraben"
        self.assertEqual(expected, generated_row)
        self.assertEqual(45, len(decode_tsv(generated)))

    def test_rechen_and_hand_values_are_not_in_provider_export(self) -> None:
        _, generated = provider_export(self.database)
        descriptions = {row["Description"] for row in decode_tsv(generated)}
        self.assertNotIn("702520PWK_VOL", descriptions)
        self.assertNotIn("702520EVUZ1_WEL_HT_HAND", descriptions)

    def test_edits_are_persisted_and_audited(self) -> None:
        record = list_datapoints(self.database)[0]
        updated = update_datapoint(
            self.database,
            record["id"],
            {"revision": record["revision"], "acron": {"comment": "Geprüft", "pvalTo": "12.5"}},
        )
        self.assertEqual("Geprüft", updated["acron_comment"])
        self.assertEqual("12.5", updated["acron_pval_to"])
        audit = recent_audit(self.database)
        self.assertTrue({"comment", "pvalTo"}.issubset({entry["field_name"] for entry in audit}))
        self.assertNotIn("changed_by_user_id", audit[0])

    def test_stale_edit_is_rejected_instead_of_overwriting(self) -> None:
        first_copy = list_datapoints(self.database)[0]
        stale_copy = dict(first_copy)
        update_datapoint(
            self.database,
            first_copy["id"],
            {"revision": first_copy["revision"], "acron": {"comment": "Erste Änderung"}},
        )
        with self.assertRaises(RevisionConflictError):
            update_datapoint(
                self.database,
                stale_copy["id"],
                {"revision": stale_copy["revision"], "acron": {"comment": "Veraltete Änderung"}},
            )
        current = list_datapoints(self.database)[0]
        self.assertEqual("Erste Änderung", current["acron_comment"])

    def test_validation_exposes_open_calculation_formulas(self) -> None:
        summary = project_summary(self.database)
        self.assertEqual(0, summary["issues"]["error"])
        self.assertEqual(0, summary["issues"]["warning"])
        self.assertEqual(1, summary["issues"]["info"])

    def test_calculation_formulas_use_only_selected_machine_values(self) -> None:
        records = {record["acron_identification"]: record for record in list_datapoints(self.database)}
        self.assertEqual(
            "702520PWK_M1_DFMS_BER_VOL + 702520PWK_M2_DFMS_BER_VOL",
            records["702520PWK_VOL"]["acron_formula"],
        )
        self.assertNotIn("M3", records["702520PWK_VOL"]["acron_formula"])
        self.assertEqual(
            "702520EVUZ1_WEL_HT_HAND + 702520EVUZ1_WEL_NT_HAND",
            records["702520PWK_WELV_GES"]["acron_formula"],
        )

    def test_project_name_drives_groups_and_vg_pv_filenames(self) -> None:
        summary = project_summary(self.database)
        self.assertEqual("PWK Lippestraße; Braukgraben VG.csv", summary["project"]["acron_filename"])
        self.assertEqual("PWK Lippestraße; Braukgraben PV.csv", summary["project"]["provider_filename"])
        self.assertTrue(all(
            record["acron_group"].startswith("PWK Lippestraße; Braukgraben|")
            for record in list_datapoints(self.database)
        ))

    def test_broken_metadata_project_name_falls_back_to_dpl_filename(self) -> None:
        self.assertEqual(
            "PWK HAM-Westhusen 1",
            _project_name("PWK ……", "2024-18-11 DPL_PWK HAM-Westhusen 1 (OL-40)_Acron - Kopie.xlsx"),
        )


if __name__ == "__main__":
    unittest.main()
