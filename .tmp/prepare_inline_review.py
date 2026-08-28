from pathlib import Path
import shutil
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from dpl_export.database import connect, transaction
from dpl_export.importer import import_project

run = root / ".tmp" / "inline-review"
shutil.rmtree(run, ignore_errors=True)
run.mkdir(parents=True)
database = run / "dpl.sqlite"
import_project(database, root / "2026-06-12 DPL_PWK Lippestraße; Braukgraben.xlsx", root / "brauckgraben_neu.csv.xlsx", root / "shhamm_provider1_WINCC60.csv")
connection = connect(database)
project = connection.execute("SELECT id FROM projects WHERE code = 'braukgraben'").fetchone()
with transaction(connection):
    cursor = connection.execute("INSERT INTO pcs7_imports(project_id, kind, source_filename, source_sha256, reference_count) VALUES (?, 'parameter', 'Parameter.xlsx', ?, 1)", (project["id"], "0" * 64))
    connection.execute("INSERT INTO pcs7_references(import_id, endpoint_key, plan_name, block_name, connection_name, data_type, source_sheet, source_row) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (cursor.lastrowid, "702520zul_nivm_msg/i.pv_lim1.value", "702520ZUL_NIVM_MSG", "I", "PV_Lim1.Value", "REAL", "Parameter", 42))
connection.close()
