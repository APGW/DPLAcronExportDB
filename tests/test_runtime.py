from __future__ import annotations

import os
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from dpl_export import __version__
from dpl_export.runtime import APP_VERSION, RuntimePaths, request_timeout_seconds


ROOT = Path(__file__).resolve().parents[1]


class RuntimeConfigurationTest(unittest.TestCase):
    def test_public_and_runtime_versions_are_identical(self) -> None:
        self.assertEqual("3.4.0", APP_VERSION)
        self.assertEqual(APP_VERSION, __version__)

    def test_request_timeout_is_bounded(self) -> None:
        with patch.dict(os.environ, {"DPL_REQUEST_TIMEOUT_SECONDS": "240"}):
            self.assertEqual(240, request_timeout_seconds())
        for invalid in ("nein", "9", "901"):
            with self.subTest(invalid=invalid):
                with patch.dict(os.environ, {"DPL_REQUEST_TIMEOUT_SECONDS": invalid}):
                    with self.assertRaises(ValueError):
                        request_timeout_seconds()

    def test_runtime_paths_fail_fast_when_templates_are_missing(self) -> None:
        directory = ROOT / ".tmp" / f"runtime-{uuid.uuid4().hex}"
        directory.mkdir(parents=True)
        try:
            with self.assertRaisesRegex(RuntimeError, "Exportvorlage fehlt"):
                RuntimePaths(directory).require_templates()
        finally:
            shutil.rmtree(directory, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
