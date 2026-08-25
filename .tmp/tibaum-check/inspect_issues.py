from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dpl_export.database import DatabaseConfig
from dpl_export.service import list_datapoints


database = DatabaseConfig.sqlite(Path(".tmp/tibaum-check/test.sqlite").resolve())
for record in list_datapoints(database):
    if record["issue_level"] in {"error", "warning"}:
        print(f"{record['issue_level'].upper()} | {record['acron_identification']} | {record['source_long_name']}")
        for issue in record["issues"]:
            if issue["level"] in {"error", "warning"}:
                print(f"  {issue.get('field') or issue.get('path') or '-'}: {issue.get('message') or issue}")
