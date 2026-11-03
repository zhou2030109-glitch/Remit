"""Contract tests for the Windows developer desktop-shell launcher."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = PROJECT_ROOT / "tools" / "start_desktop.vbs"
SHORTCUT_SCRIPT = PROJECT_ROOT / "tools" / "create_desktop_shortcut.ps1"


def run_powershell(script: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            *arguments,
        ],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=90,
        check=False,
    )


@unittest.skipUnless(
    sys.platform == "win32",
    "uv 的 pythonw.exe trampoline 与 Windows Terminal 默认终端都是 Windows 行为；"
    "POSIX 桌面壳由 test_posix_start_launcher.py 覆盖",
)
class WindowsDesktopLauncherTests(unittest.TestCase):
    def test_launcher_is_ascii_only(self) -> None:
        """wscript.exe 按 ANSI 代码页读取 .vbs，非 ASCII 会让提示框显示乱码。"""
        LAUNCHER.read_bytes().decode("ascii")

    def test_launcher_hides_the_console_window(self) -> None:
        """窗口样式必须为 0。

        venv 里的 pythonw.exe 是控制台子系统 trampoline，可见的控制台会被默认
        终端（Windows Terminal）渲染成一个空白终端窗口。
        """
        self.assertRegex(
            LAUNCHER.read_text(encoding="ascii"),
            r"\.Run\s+\w+,\s*0\s*,\s*False",
            "start_desktop.vbs 必须以隐藏窗口样式启动桌面壳",
        )

    def test_launcher_resolves_paths_from_its_own_location(self) -> None:
        """双击桌面快捷方式时工作目录不确定，路径必须由脚本位置推导。"""
        text = LAUNCHER.read_text(encoding="ascii")
        for expected in (
            "WScript.ScriptFullName",
            r"backend\.venv\Scripts\pythonw.exe",
            r"backend\venv\Scripts\pythonw.exe",
            r"tools\desktop_app.py",
        ):
            self.assertIn(expected, text)

    def test_shortcut_script_targets_wscript_with_the_hidden_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            shortcut_path = Path(temporary) / "Remit.lnk"
            created = run_powershell(SHORTCUT_SCRIPT, "-ShortcutPath", str(shortcut_path))
            self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
            self.assertTrue(shortcut_path.is_file(), created.stdout + created.stderr)

            probe = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-Command",
                    (
                        "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
                        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut("
                        f"'{shortcut_path}');"
                        "'{0}|{1}|{2}' -f $s.TargetPath, $s.Arguments, $s.WorkingDirectory"
                    ),
                ],
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=90,
                check=False,
            )
            self.assertEqual(probe.returncode, 0, probe.stdout + probe.stderr)
            target, arguments, working_directory = probe.stdout.strip().split("|")
            self.assertEqual(Path(target).name.lower(), "wscript.exe")
            self.assertEqual(arguments, f'"{LAUNCHER}"')
            self.assertEqual(Path(working_directory), PROJECT_ROOT)