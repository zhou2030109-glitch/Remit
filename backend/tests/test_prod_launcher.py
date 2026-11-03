"""安装版启动器的启动顺序与错误可见性回归测试。"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


def load_prod_launcher():
    module_path = Path(__file__).parents[2] / "tools" / "remit_prod_app.py"
    spec = importlib.util.spec_from_file_location(
        "remit_prod_app_under_test", module_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载安装版启动器")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(
    sys.platform == "win32",
    "安装版启动器依赖 Windows 打包布局与 taskkill，仅 Windows 可加载",
)
class ProductionLauncherTests(unittest.TestCase):
    def test_stop_ends_owner_before_cleaning_services(self) -> None:
        launcher = load_prod_launcher()
        events: list[str] = []
        with (
            patch.object(sys, "argv", ["remit_prod_app.py", "--stop"]),
            patch.object(launcher, "configure_logging"),
            patch.object(launcher, "_acquire_single_instance", return_value=1234),
            patch.object(launcher, "_release_single_instance") as release,
            patch.object(
                launcher,
                "_stop_application_owner",
                side_effect=lambda: events.append("owner"),
            ),
            patch.object(
                launcher, "stop_services", side_effect=lambda: events.append("services")
            ),
        ):
            self.assertEqual(launcher.main(), 0)
        self.assertEqual(events, ["owner", "services"])
        release.assert_called_once_with(1234)

    def test_owner_record_cannot_kill_reused_foreign_or_own_pid(self) -> None:
        launcher = load_prod_launcher()
        cases = ((31001, 9, True), (31001, 10, False), (launcher.os.getpid(), 10, True))
        for pid, created, owned in cases:
            with self.subTest(pid=pid, created=created, owned=owned):
                record = MagicMock()
                record.read_text.return_value = launcher.json.dumps(
                    {"pid": pid, "created": 10}
                )
                process = MagicMock()
                process.create_time.return_value = created
                with (
                    patch.object(launcher, "APPLICATION_OWNER_PATH", record),
                    patch.object(launcher.psutil, "Process", return_value=process),
                    patch.object(
                        launcher, "_process_belongs_to_install", return_value=owned
                    ),
                    patch.object(launcher, "_terminate_pid") as terminate,
                ):
                    launcher._stop_application_owner()
                terminate.assert_not_called()

    def test_stop_waits_for_verified_owner_to_exit(self) -> None:
        launcher = load_prod_launcher()
        record = MagicMock()
        record.read_text.return_value = '{"pid": 31001, "created": 10}'
        process = MagicMock()
        process.create_time.return_value = 10
        with (
            patch.object(launcher, "APPLICATION_OWNER_PATH", record),
            patch.object(launcher.psutil, "Process", return_value=process),
            patch.object(launcher, "_process_belongs_to_install", return_value=True),
            patch.object(launcher, "_terminate_pid") as terminate,
        ):
            launcher._stop_application_owner()
        terminate.assert_called_once_with(31001)
        process.wait.assert_called_once_with(timeout=5)
        record.unlink.assert_called_once_with()

    def test_failed_installation_check_returns_nonzero(self) -> None:
        launcher = load_prod_launcher()
        with (
            patch.object(sys, "argv", ["remit_prod_app.py", "--check"]),
            patch.object(launcher, "configure_logging"),
            patch.object(
                launcher,
                "check_installation",
                return_value="PACKAGED_APP_CHECK_FAIL: 字体",
            ),
            patch.object(launcher, "_acquire_single_instance") as acquire,
        ):
            self.assertEqual(launcher.main(), 1)
        acquire.assert_not_called()

    def test_duplicate_launch_only_opens_existing_application(self) -> None:
        launcher = load_prod_launcher()
        with (
            patch.object(sys, "argv", ["remit_prod_app.py"]),
            patch.object(launcher, "configure_logging"),
            patch.object(launcher, "_acquire_single_instance", return_value=None),
            patch.object(launcher, "wait_until_ready", return_value=True),
            patch.object(launcher, "open_user_interface", return_value=True) as open_ui,
            patch.object(launcher, "_run_application") as run,
            patch.object(launcher, "stop_services") as stop,
        ):
            self.assertEqual(launcher.main(), 0)
        open_ui.assert_called_once_with()
        run.assert_not_called()
        stop.assert_not_called()

    def test_failed_duplicate_launch_does_not_stop_existing_services(self) -> None:
        launcher = load_prod_launcher()
        with (
            patch.object(sys, "argv", ["remit_prod_app.py"]),
            patch.object(launcher, "configure_logging"),
            patch.object(launcher, "_acquire_single_instance", return_value=None),
            patch.object(launcher, "wait_until_ready", return_value=False),
            patch.object(launcher, "show_startup_error"),
            patch.object(launcher, "stop_services") as stop,
        ):
            self.assertEqual(launcher.main(), 1)
        stop.assert_not_called()

    def test_single_instance_handle_is_released_after_failed_startup(self) -> None:
        launcher = load_prod_launcher()
        with (
            patch.object(sys, "argv", ["remit_prod_app.py"]),
            patch.object(launcher, "configure_logging"),
            patch.object(launcher, "_record_application_owner"),
            patch.object(launcher, "_clear_application_owner"),
            patch.object(launcher, "_acquire_single_instance", return_value=1234),
            patch.object(launcher, "_run_application", return_value=1),
            patch.object(launcher, "_release_single_instance") as release,
        ):
            self.assertEqual(launcher.main(), 1)
        release.assert_called_once_with(1234)

    def test_existing_mutex_is_closed_without_claiming_service_ownership(self) -> None:
        launcher = load_prod_launcher()
        with patch.object(launcher.ctypes, "windll") as windll:
            windll.kernel32.CreateMutexW.return_value = 1234
            windll.kernel32.GetLastError.return_value = 183
            self.assertIsNone(launcher._acquire_single_instance())
        windll.kernel32.CloseHandle.assert_called_once_with(1234)

    def test_stop_services_falls_back_to_owned_port_processes(self) -> None:
        """PID 文件丢失时也必须清理本安装目录遗留的监听进程。"""
        launcher = load_prod_launcher()
        launcher._services.clear()
        missing_pid_file = MagicMock()
        missing_pid_file.read_text.side_effect = FileNotFoundError

        with (
            patch.object(launcher, "_pid_file", return_value=missing_pid_file),
            patch.object(
                launcher,
                "_owned_service_pid_for_port",
                side_effect=[31001, 31002],
                create=True,
            ),
            patch.object(launcher, "_terminate_pid") as terminate,
        ):
            launcher.stop_services()

        self.assertEqual(
            [call.args[0] for call in terminate.call_args_list],
            [31001, 31002],
        )

    def test_backend_refuses_to_reuse_foreign_port_owner(self) -> None:
        """18000 被其他程序占用时不能误连到其他安装实例的后端。"""
        launcher = load_prod_launcher()

        with (
            patch.object(launcher, "port_is_open", return_value=True),
            patch.object(
                launcher,
                "_owned_service_pid_for_port",
                return_value=None,
                create=True,
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "18000"):
                launcher.ensure_backend_running()

    def test_default_browser_open_retries_after_first_shell_failure(self) -> None:
        launcher = load_prod_launcher()

        with (
            patch.object(
                launcher,
                "_open_default_browser_once",
                side_effect=[False, True],
                create=True,
            ) as open_once,
            patch.object(launcher.time, "sleep") as sleep,
        ):
            opened = launcher.open_default_browser_with_retry()

        self.assertTrue(opened)
        self.assertEqual(open_once.call_count, 2)
        sleep.assert_called_once()

    def test_browser_opens_before_tray_loop_starts(self) -> None:
        launcher = load_prod_launcher()
        tray_thread = MagicMock()
        events: list[str] = []
        tray_thread.start.side_effect = lambda: events.append("tray")

        with (
            patch.object(sys, "argv", ["remit_prod_app.py"]),
            patch.object(launcher, "configure_logging"),
            patch.object(launcher, "_record_application_owner"),
            patch.object(launcher, "_clear_application_owner"),
            patch.object(launcher, "prepare_jupyter_kernelspec"),
            patch.object(launcher, "ensure_backend_running"),
            patch.object(launcher, "wait_until_ready", return_value=True),
            patch.object(launcher, "run_tray") as run_tray,
            patch.object(
                launcher.threading, "Thread", return_value=tray_thread
            ) as thread,
            patch.object(
                launcher,
                "open_user_interface",
                side_effect=lambda: events.append("browser") or True,
            ) as open_ui,
        ):
            result = launcher.main()

        self.assertEqual(result, 0)
        thread.assert_called_once()
        self.assertIs(thread.call_args.kwargs["target"], run_tray)
        tray_thread.start.assert_called_once_with()
        open_ui.assert_called_once_with()
        self.assertEqual(events, ["browser", "tray"])
        tray_thread.join.assert_called_once_with()

    def test_startup_failure_is_shown_when_pythonw_has_no_console(self) -> None:
        launcher = load_prod_launcher()

        with (
            patch.object(sys, "argv", ["remit_prod_app.py"]),
            patch.object(launcher, "configure_logging"),
            patch.object(launcher, "_record_application_owner"),
            patch.object(launcher, "_clear_application_owner"),
            patch.object(launcher, "prepare_jupyter_kernelspec"),
            patch.object(
                launcher,
                "ensure_backend_running",
                side_effect=RuntimeError("backend failed"),
            ),
            patch.object(launcher, "stop_services"),
            patch.object(launcher, "show_startup_error", create=True) as show_error,
        ):
            result = launcher.main()

        self.assertEqual(result, 1)
        show_error.assert_called_once()
        self.assertIn("backend failed", show_error.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
