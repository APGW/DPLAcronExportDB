from __future__ import annotations

import argparse
import json
from pathlib import Path

from dpl_export.database import DatabaseConfig, DatabaseTarget, database_from_environment
from dpl_export.exporter import write_exports
from dpl_export.importer import import_project
from dpl_export.service import project_summary
from dpl_export.web_server import serve


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATABASE = BASE_DIR / "data" / "dpl.sqlite"
DEFAULT_DPL = BASE_DIR / "2026-06-12 DPL_PWK Lippestraße; Braukgraben.xlsx"
DEFAULT_ACRON_TEMPLATE = BASE_DIR / "brauckgraben_neu.csv.xlsx"
DEFAULT_PROVIDER_TEMPLATE = BASE_DIR / "shhamm_provider1_WINCC60.csv"


def ensure_database(database: DatabaseTarget) -> None:
    result = import_project(
        database,
        DEFAULT_DPL,
        DEFAULT_ACRON_TEMPLATE,
        DEFAULT_PROVIDER_TEMPLATE,
    )
    if result["status"] == "imported":
        print(f"Datenbank erstellt: {result['selected']} markierte Datensätze.")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="DPL in Acron- und WINCC60-Provider-Exporte überführen")
    root.add_argument(
        "--database",
        type=Path,
        help="Optionale SQLite-Datenbank für Entwicklung/Tests; sonst gilt die DPL_DB_*-Konfiguration",
    )
    root.add_argument("--data-dir", type=Path, help="Verzeichnis für Uploads; sonst DPL_DATA_DIR")
    commands = root.add_subparsers(dest="command")

    init = commands.add_parser("init", help="DPL einlesen und Datenbank anlegen")
    init.add_argument("--force", action="store_true", help="Vorhandenes Projekt aus der DPL neu aufbauen")
    init.add_argument("--dpl", type=Path, default=DEFAULT_DPL)
    init.add_argument("--acron-template", type=Path, default=DEFAULT_ACRON_TEMPLATE)
    init.add_argument("--provider-template", type=Path, default=DEFAULT_PROVIDER_TEMPLATE)

    web = commands.add_parser("serve", help="Weboberfläche starten")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8765)

    export = commands.add_parser("export", help="Beide CSV-Dateien erzeugen")
    export.add_argument("--output", type=Path, default=BASE_DIR / "exports")

    commands.add_parser("validate", help="Import- und Exportstatus prüfen")

    return root


def main() -> None:
    arguments = parser().parse_args()
    database: DatabaseTarget
    if arguments.database:
        database = DatabaseConfig.sqlite(arguments.database.resolve())
    else:
        database = database_from_environment(DEFAULT_DATABASE)
    command = arguments.command or "serve"

    if command == "init":
        result = import_project(
            database,
            arguments.dpl.resolve(),
            arguments.acron_template.resolve(),
            arguments.provider_template.resolve(),
            force=arguments.force,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    ensure_database(database)
    if command == "serve":
        serve(
            database,
            getattr(arguments, "host", "127.0.0.1"),
            getattr(arguments, "port", 8765),
            data_directory=arguments.data_dir.resolve() if arguments.data_dir else None,
        )
    elif command == "export":
        for output in write_exports(database, arguments.output.resolve()):
            print(output)
    elif command == "validate":
        print(json.dumps(project_summary(database), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
