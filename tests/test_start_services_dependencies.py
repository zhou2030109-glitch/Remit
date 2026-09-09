"""Dependency validation tests for the Windows service launcher."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
START_SERVICES = PROJECT_ROOT / "tools" / "start_services.ps1"


@unittest.skipUnless(
    sys.platform == "win32",
    "start_services.ps1 requires Windows venv/redis layout and powershell.exe; "
    "POSIX environments are covered by test_posix_start_launcher.py",
)
class StartServicesDependencyTests(unittest.TestCase):
    def test_launcher_rejects_ports_owned_by_another_project(self) -> None:
        text = START_SERVICES.read_text(encoding="utf-8")
        self.assertIn("Test-ProjectOwnedListener", text)
        self.assertIn("occupied by another application", text)

    def test_launcher_generates_missing_dev_env_after_dependency_check(self) -> None:
        text = START_SERVICES.read_text(encoding="utf-8")
        call = "\nInitialize-BackendEnvironment\nNew-Item"
        self.assertIn("function Initialize-BackendEnvironment", text)
        self.assertIn("Copy-Item -LiteralPath $examplePath -Destination $envPath", text)
        self.assertIn(call, text)
        self.assertLess(text.index("if ($Check)"), text.index(call))

    def test_launcher_requires_node_24_and_pnpm_10(self) -> None:
        text = START_SERVICES.read_text(encoding="utf-8")
        self.assertIn("Node.js 24 is required", text)
        self.assertIn("pnpm 10 is required", text)

    def test_check_rejects_broken_vite_installation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            root = Path(temp_directory)
            launcher = root / "tools" / "start_services.ps1"
            launcher.parent.mkdir(parents=True)
            shutil.copy2(START_SERVICES, launcher)

            (root / "tools" / "redis").mkdir(parents=True)
            (root / "tools" / "redis" / "redis-server.exe").touch()
            (root / "backend" / ".venv" / "Scripts").mkdir(parents=True)
            (root / "backend" / ".venv" / "Scripts" / "python.exe").touch()
            (root / "frontend" / "node_modules").mkdir(parents=True)
            (root / "frontend" / "package.json").touch()

            process = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(launcher),
                    "-Check",
                ],
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                # Keep the failure assertion strict while allowing for a slow
                # first PowerShell process on a fresh Windows CI runner.
                timeout=45,
                check=False,
            )

        output = process.stdout + process.stderr
        self.assertNotEqual(process.returncode, 0, output)
        self.assertIn("Frontend Vite entry point not found", output)


if __name__ == "__main__":
    unittest.main()
