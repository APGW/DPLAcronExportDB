from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from dpl_export.database import DatabaseTarget, database_from_environment
from dpl_export.exporter import write_exports
from dpl_export.importer import import_project
from dpl_export.runtime import APP_VERSION, BUNDLED_PATHS, DEFAULT_HOST, DEFAULT_PORT
from dpl_export.service import project_summary
from dpl_export.web_server import serve


LOGGER = logging.getLogger("dpl_acron")


def require_initialized_project(database: DatabaseTarget) -> None:
    try:
        project_summary(database)
    except KeyError as exc:
        raise RuntimeError(
            "Die Datenbank enthält noch kein aktives Projekt. "
            "Initialisieren Sie sie einmalig mit: python3 app.py init"
        ) from exc


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="DPL in Acron- und WINCC60-Provider-Exporte überführen")
    root.add_argument("--version", action="version", version=f"%(prog)s {APP_VERSION}")
    root.add_argument("--data-dir", type=Path, help="Verzeichnis für Uploads; sonst DPL_DATA_DIR")
    commands = root.add_subparsers(dest="command")

    init = commands.add_parser("init", help="DPL einlesen und Datenbank anlegen")
    init.add_argument("--force", action="store_true", help="Vorhandenes Projekt aus der DPL neu aufbauen")
    init.add_argument("--dpl", type=Path, default=BUNDLED_PATHS.default_dpl)
    init.add_argument("--acron-template", type=Path, default=BUNDLED_PATHS.acron_template)
    init.add_argument("--provider-template", type=Path, default=BUNDLED_PATHS.provider_template)

    web = commands.add_parser("serve", help="Weboberfläche starten")
    web.add_argument("--host", default=DEFAULT_HOST)
    web.add_argument("--port", type=int, default=DEFAULT_PORT)

    export = commands.add_parser("export", help="Beide CSV-Dateien erzeugen")
    export.add_argument("--output", type=Path, default=BUNDLED_PATHS.root / "exports")

    commands.add_parser("validate", help="Import- und Exportstatus prüfen")

    return root


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    arguments = parser().parse_args()
    database: DatabaseTarget = database_from_environment()
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

    BUNDLED_PATHS.require_templates()
    require_initialized_project(database)
    if command == "serve":
        serve(
            database,
            getattr(arguments, "host", DEFAULT_HOST),
            getattr(arguments, "port", DEFAULT_PORT),
            data_directory=arguments.data_dir.resolve() if arguments.data_dir else None,
        )
    elif command == "export":
        for output in write_exports(database, arguments.output.resolve()):
            print(output)
    elif command == "validate":
        print(json.dumps(project_summary(database), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError) as exc:
        LOGGER.error("Start abgebrochen: %s", exc)
        raise SystemExit(1) from exc
