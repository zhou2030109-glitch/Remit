"""Dependency validation tests for the Windows service launcher."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
START_SERVICES = PROJECT_ROOT / "tools" / "start_services.ps1"


class StartServicesDependencyTests(unittest.TestCase):
    def test_launcher_rejects_ports_owned_by_another_project(self) -> None:
        text = START_SERVICES.read_text(encoding="utf-8")
        self.assertIn("Test-ProjectOwnedListener", text)
        self.assertIn("occupied by another application", text)

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
                timeout=15,
                check=False,
            )

        output = process.stdout + process.stderr
        self.assertNotEqual(process.returncode, 0, output)
        self.assertIn("Frontend Vite entry point not found", output)


if __name__ == "__main__":
    unittest.main()
