from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


APP_NAME = "DPL2ACRON"
APP_VERSION = "3.6.0"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_REQUEST_TIMEOUT_SECONDS = 120


@dataclass(frozen=True)
class RuntimePaths:
    """All immutable files shipped with one application release."""

    root: Path

    @property
    def default_dpl(self) -> Path:
        return self.root / "2026-06-12 DPL_PWK Lippestraße; Braukgraben.xlsx"

    @property
    def acron_template(self) -> Path:
        return self.root / "brauckgraben_neu.csv.xlsx"

    @property
    def provider_template(self) -> Path:
        return self.root / "shhamm_provider1_WINCC60.csv"

    def require_templates(self) -> None:
        missing = [
            str(path)
            for path in (self.acron_template, self.provider_template)
            if not path.is_file()
        ]
        if missing:
            raise RuntimeError("Erforderliche Exportvorlage fehlt: " + ", ".join(missing))

    def require_seed_files(self) -> None:
        self.require_templates()
        if not self.default_dpl.is_file():
            raise RuntimeError(f"Mitgelieferte Start-DPL fehlt: {self.default_dpl}")


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUNDLED_PATHS = RuntimePaths(PROJECT_ROOT)


def data_directory(configured: Path | None = None) -> Path:
    if configured is not None:
        return configured.resolve()
    return Path(os.environ.get("DPL_DATA_DIR", PROJECT_ROOT / "data")).resolve()


def request_timeout_seconds() -> int:
    raw = os.environ.get("DPL_REQUEST_TIMEOUT_SECONDS", str(DEFAULT_REQUEST_TIMEOUT_SECONDS))
    try:
        timeout = int(raw)
    except ValueError as exc:
        raise ValueError("DPL_REQUEST_TIMEOUT_SECONDS muss eine ganze Zahl sein.") from exc
    if not 10 <= timeout <= 900:
        raise ValueError("DPL_REQUEST_TIMEOUT_SECONDS muss zwischen 10 und 900 liegen.")
    return timeout
