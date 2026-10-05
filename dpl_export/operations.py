from __future__ import annotations

import shutil
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .database import DatabaseTarget, connect, database_health
from .modules import list_modules


def _iso_timestamp(path: Path | None) -> str | None:
    if path is None:
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()


def operations_status(
    database: DatabaseTarget,
    data_directory: Path,
    backup_directory: Path = Path("/var/backups/dpl-acron"),
) -> dict[str, Any]:
    connection = connect(database)
    project_counts = connection.execute(
        "SELECT COUNT(*) AS versions, SUM(is_current) AS current_projects FROM projects"
    ).fetchone()
    latest_import = connection.execute(
        "SELECT source_filename, imported_at FROM imports ORDER BY id DESC LIMIT 1"
    ).fetchone()
    latest_export = connection.execute(
        "SELECT filename, kind, sha256, exported_at FROM export_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    connection.close()

    data_directory = data_directory.resolve()
    disk = shutil.disk_usage(data_directory)
    try:
        backups = sorted(backup_directory.glob("dpl-acron-*.sql.gz"), key=lambda path: path.stat().st_mtime)
    except OSError:
        backups = []
    latest_backup = backups[-1] if backups else None
    backup_status: dict[str, Any] = {}
    status_file = data_directory / "backup-status.json"
    try:
        backup_status = json.loads(status_file.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        pass
    modules = list_modules(data_directory / "modules")
    return {
        "database": database_health(database),
        "projects": {
            "current": int(project_counts["current_projects"] or 0),
            "versions": int(project_counts["versions"] or 0),
        },
        "modules": [
            {"code": module.code, "name": module.name, "version": module.version}
            for module in modules
        ],
        "storage": {
            "freeBytes": disk.free,
            "totalBytes": disk.total,
            "usedPercent": round((disk.used / disk.total) * 100, 1) if disk.total else 0,
        },
        "backup": {
            "available": latest_backup is not None or bool(backup_status.get("filename")),
            "filename": latest_backup.name if latest_backup else backup_status.get("filename"),
            "createdAt": _iso_timestamp(latest_backup) if latest_backup else backup_status.get("createdAt"),
        },
        "latestImport": dict(latest_import) if latest_import else None,
        "latestExport": dict(latest_export) if latest_export else None,
    }
