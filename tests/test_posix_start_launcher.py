"""Regression tests for the POSIX (macOS/Linux) service launcher."""

from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
START_SERVICES = PROJECT_ROOT / "tools" / "start_services.sh"
STOP_SERVICES = PROJECT_ROOT / "tools" / "stop_services.sh"
BACKEND_PORT = 18000


def wait_for_port(port: int, *, listening: bool, timeout: float = 10) -> None:
    """等待本机端口进入指定监听状态。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                current = True
        except OSError:
            current = False
        if current is listening:
            return
        time.sleep(0.1)
    raise AssertionError(
        f"port {port} did not become " + ("ready" if listening else "free")
    )


@unittest.skipIf(
    sys.platform == "win32",
    "POSIX launcher tests need bash/lsof; Windows is covered by "
    "test_win_start_launcher.py and test_start_services_dependencies.py",
)
class PosixStartLauncherTests(unittest.TestCase):
    def test_launcher_anchors_itself_to_project_root(self) -> None:
        text = START_SERVICES.read_text(encoding="utf-8")
        self.assertIn("${BASH_SOURCE[0]}", text)
        self.assertNotIn("redis", text.lower())

    def test_launcher_uses_dedicated_project_ports(self) -> None:
        text = START_SERVICES.read_text(encoding="utf-8")
        self.assertIn("BACKEND_PORT=18000", text)
        self.assertIn("FRONTEND_PORT=15173", text)
        self.assertIn("--strictPort", text)

    def test_launcher_requires_node_24_and_pnpm_10(self) -> None:
        text = START_SERVICES.read_text(encoding="utf-8")
        self.assertIn("Node.js 24", text)
        self.assertIn("pnpm 10", text)

    def test_launcher_rejects_foreign_port_owners(self) -> None:
        text = START_SERVICES.read_text(encoding="utf-8")
        self.assertIn("occupied by another application", text)

    def test_launchers_use_posix_line_endings(self) -> None:
        for path in (START_SERVICES, STOP_SERVICES):
            self.assertNotIn(b"\r", path.read_bytes(), f"{path.name} must stay LF")

    def test_stop_script_only_stops_project_processes(self) -> None:
        text = STOP_SERVICES.read_text(encoding="utf-8")
        self.assertIn("lsof", text)
        self.assertIn("is_recorded_service_process", text)
        self.assertNotIn("topmost_project_ancestor", text)
        self.assertIn(
            "not started from this project; leaving it running",
            text,
            "stop script must warn instead of killing foreign listeners",
        )

    def test_start_script_records_the_actual_listener_pid(self) -> None:
        text = START_SERVICES.read_text(encoding="utf-8")
        self.assertIn("listener_matches_started_service", text)
        self.assertIn('printf \'%s\' "$owned_pid" >"$LOG_DIR/$name.pid"', text)

    def test_external_listener_on_backend_port_is_rejected_and_not_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            root = Path(temp_directory)
            launcher = root / "tools" / "start_services.sh"
            stopper = root / "tools" / "stop_services.sh"
            launcher.parent.mkdir(parents=True)
            shutil.copy2(START_SERVICES, launcher)
            shutil.copy2(STOP_SERVICES, stopper)
            launcher.chmod(0o755)
            stopper.chmod(0o755)

            backend_python = root / "backend" / ".venv" / "bin" / "python"
            backend_python.parent.mkdir(parents=True)
            backend_python.touch()
            backend_python.chmod(0o755)
            vite = root / "frontend" / "node_modules" / "vite" / "bin" / "vite.js"
            vite.parent.mkdir(parents=True)
            vite.touch()
            (root / "frontend" / "package.json").write_text("{}\n", encoding="utf-8")

            external_command = [
                sys.executable,
                "-m",
                "http.server",
                str(BACKEND_PORT),
                "--bind",
                "127.0.0.1",
            ]
            process = subprocess.Popen(
                external_command,
                # 即使外部服务恰好从仓库根目录启动，也不能仅凭 cwd 认领。
                cwd=root,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            try:
                wait_for_port(BACKEND_PORT, listening=True)

                start = subprocess.run(
                    ["bash", str(launcher)],
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    capture_output=True,
                    timeout=20,
                    check=False,
                )
                self.assertNotEqual(start.returncode, 0, start.stdout + start.stderr)
                self.assertIn("occupied by another application", start.stderr)
                self.assertIsNone(
                    process.poll(), "start launcher stopped a foreign process"
                )

                stop = subprocess.run(
                    ["bash", str(stopper)],
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    capture_output=True,
                    timeout=20,
                    check=False,
                )
                self.assertNotEqual(stop.returncode, 0, stop.stdout + stop.stderr)
                self.assertIn("leaving it running", stop.stdout)
                self.assertIsNone(
                    process.poll(), "stop launcher killed a foreign process"
                )
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                wait_for_port(BACKEND_PORT, listening=False)

    def test_backend_start_failure_returns_nonzero_without_success_banner(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            root = Path(temp_directory)
            launcher = root / "tools" / "start_services.sh"
            launcher.parent.mkdir(parents=True)
            shutil.copy2(START_SERVICES, launcher)
            launcher.chmod(0o755)

            backend_python = root / "backend" / ".venv" / "bin" / "python"
            backend_python.parent.mkdir(parents=True)
            backend_python.write_text("#!/usr/bin/env bash\nexit 7\n", encoding="utf-8")
            backend_python.chmod(0o755)

            vite = root / "frontend" / "node_modules" / "vite" / "bin" / "vite.js"
            vite.parent.mkdir(parents=True)
            vite.touch()
            (root / "frontend" / "package.json").write_text("{}\n", encoding="utf-8")

            result = subprocess.run(
                ["bash", str(launcher)],
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=20,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("backend exited before opening port", result.stderr)
            self.assertNotIn("Frontend: http://", result.stdout)

    def test_launcher_has_side_effect_free_dependency_check(self) -> None:
        # 从仓库外的当前目录运行，证明启动器按脚本自身位置定位项目根。
        with tempfile.TemporaryDirectory() as temp_directory:
            process = subprocess.run(
                ["bash", str(START_SERVICES), "--check"],
                cwd=temp_directory,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=45,
                check=False,
            )
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertIn("LAUNCHER_CHECK_OK", process.stdout)

    def test_check_rejects_broken_vite_installation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            root = Path(temp_directory)
            launcher = root / "tools" / "start_services.sh"
            launcher.parent.mkdir(parents=True)
            shutil.copy2(START_SERVICES, launcher)
            launcher.chmod(0o755)

            venv_bin = root / "backend" / ".venv" / "bin"
            venv_bin.mkdir(parents=True)
            (venv_bin / "python").touch()
            (venv_bin / "python").chmod(0o755)

            (root / "frontend" / "node_modules").mkdir(parents=True)
            (root / "frontend" / "package.json").touch()

            process = subprocess.run(
                ["bash", str(launcher), "--check"],
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=45,
                check=False,
            )

        output = process.stdout + process.stderr
        self.assertNotEqual(process.returncode, 0, output)
        self.assertIn("Frontend Vite entry point not found", output)


if __name__ == "__main__":
    unittest.main()
