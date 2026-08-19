"""Regression tests for the Windows one-click launcher."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = PROJECT_ROOT / "win_start.bat"


class WindowsLauncherTests(unittest.TestCase):
    def test_launcher_anchors_itself_to_project_root(self) -> None:
        text = LAUNCHER.read_text(encoding="utf-8")
        lines = text.lower().splitlines()
        launch_offset = next(
            index
            for index, line in enumerate(lines)
            if "powershell.exe" in line and '"%launcher%"' in line
        )
        self.assertGreater(launch_offset, 0)
        prelude = "\n".join(lines[:launch_offset])
        self.assertIn('set "root=%~dp0"', prelude)
        self.assertIn(
            'set "launcher=%root%tools\\start_services.ps1"',
            prelude,
            "win_start.bat must resolve the service launcher from its own directory",
        )

    def test_launcher_uses_windows_crlf_line_endings(self) -> None:
        data = LAUNCHER.read_bytes()
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""))

    def test_launcher_has_side_effect_free_dependency_check(self) -> None:
        process = subprocess.run(
            ["cmd.exe", "/d", "/c", "call", str(LAUNCHER), "--check"],
            cwd=Path("C:/Windows/System32"),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertIn("LAUNCHER_CHECK_OK", process.stdout)


if __name__ == "__main__":
    unittest.main()
