"""Regression tests for legacy Excel ``.xls`` dataset support."""

from __future__ import annotations

import importlib.util
import re
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
FRONTEND_STEPPER = PROJECT_ROOT / "frontend" / "src" / "components" / "UserStepper.vue"
sys.path.insert(0, str(BACKEND_ROOT))

from app.tools.e2b_interpreter import E2BCodeInterpreter  # noqa: E402
from app.utils.common_utils import get_current_files  # noqa: E402


class LegacyXlsFrontendTests(unittest.TestCase):
    def test_file_picker_accepts_legacy_xls(self) -> None:
        source = FRONTEND_STEPPER.read_text(encoding="utf-8")
        accept_match = re.search(r'ACCEPTED_DATA_EXTENSIONS\s*=\s*"([^"]+)"', source)
        self.assertIsNotNone(accept_match)
        accepted_suffixes = {
            suffix.strip().lower() for suffix in accept_match.group(1).split(",")
        }
        self.assertIn(".xls", accepted_suffixes)

    def test_dropzone_handles_file_drops(self) -> None:
        source = FRONTEND_STEPPER.read_text(encoding="utf-8")
        self.assertIn('@drop.prevent="handleFileDrop"', source)


class LegacyXlsBackendTests(unittest.IsolatedAsyncioTestCase):
    def test_data_discovery_includes_legacy_xls(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            folder = Path(temp_directory)
            for filename in ("legacy.xls", "modern.xlsx", "table.csv", "ignore.pdf"):
                (folder / filename).touch()

            discovered = set(get_current_files(str(folder), "data"))

        self.assertEqual(discovered, {"legacy.xls", "modern.xlsx", "table.csv"})

    async def test_e2b_upload_includes_legacy_xls(self) -> None:
        class FakeFiles:
            def __init__(self) -> None:
                self.writes: list[tuple[str, bytes]] = []

            async def write(self, path: str, content: bytes) -> None:
                self.writes.append((path, content))

        class FakeSandbox:
            def __init__(self) -> None:
                self.files = FakeFiles()

        with tempfile.TemporaryDirectory() as temp_directory:
            legacy_file = Path(temp_directory) / "legacy.xls"
            legacy_file.write_bytes(b"legacy-excel-fixture")
            interpreter = E2BCodeInterpreter.__new__(E2BCodeInterpreter)
            interpreter.work_dir = temp_directory
            interpreter.sbx = FakeSandbox()

            await interpreter._upload_all_files()

        self.assertIn(
            ("/home/user/legacy.xls", b"legacy-excel-fixture"),
            interpreter.sbx.files.writes,
        )

    def test_xlrd_runtime_dependency_is_installed(self) -> None:
        self.assertIsNotNone(importlib.util.find_spec("xlrd"))


if __name__ == "__main__":
    unittest.main()
