"""桌面壳启动器的跨进程句柄回归测试。"""

from __future__ import annotations

import importlib.util
import subprocess
import unittest
from pathlib import Path
from urllib.parse import urlparse
from unittest.mock import MagicMock, patch


def load_desktop_module():
    module_path = Path(__file__).parents[2] / "tools" / "desktop_app.py"
    spec = importlib.util.spec_from_file_location("desktop_app_under_test", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载桌面启动器")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DesktopLauncherTests(unittest.TestCase):
    def test_desktop_sets_explicit_windows_app_identity(self) -> None:
        """解释器承载的窗口必须有独立 AppID，否则任务栏显示 Python 图标。"""
        desktop = load_desktop_module()

        with patch.object(desktop.ctypes, "windll") as windll:
            windll.shell32.SetCurrentProcessExplicitAppUserModelID.return_value = 0
            desktop._configure_windows_identity()

        windll.shell32.SetCurrentProcessExplicitAppUserModelID.assert_called_once_with(
            desktop.APP_USER_MODEL_ID
        )

    def test_homepage_uses_remit_brand_without_upstream_promotions(self) -> None:
        """主页应只展示 Remit 品牌，不再携带上游作者的推广入口。"""
        homepage = (
            Path(__file__).parents[2] / "frontend" / "src" / "pages" / "home.vue"
        ).read_text(encoding="utf-8")

        self.assertIn(">Remit</span>", homepage)
        self.assertNotIn("MathModelAgent", homepage)
        self.assertNotIn("jihe520", homepage)
        self.assertNotIn("mathmodel.top", homepage)

    def test_desktop_external_link_guard_is_installed(self) -> None:
        """Future same-window external anchors must also be converted to new windows."""
        desktop = load_desktop_module()
        app = desktop.DesktopApp()
        app.window = MagicMock()

        app._install_external_link_guard()

        script = app.window.evaluate_js.call_args.args[0]
        self.assertIn("closest('a[href]')", script)
        self.assertIn("anchor.target = '_blank'", script)
        self.assertIn("noopener noreferrer", script)

    def test_service_launcher_does_not_capture_long_lived_child_pipes(self) -> None:
        """后台服务继承 PIPE 会让 communicate 永远等不到 EOF。"""
        desktop = load_desktop_module()
        app = desktop.DesktopApp()
        app.window = MagicMock()

        with (
            patch.object(app, "_wait_until_ready", return_value=True),
            patch.object(desktop.time, "sleep"),
            patch.object(
                desktop.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0),
            ) as run,
        ):
            app._bootstrap_services()

        launcher_options = run.call_args.kwargs
        self.assertIs(launcher_options["stdin"], subprocess.DEVNULL)
        self.assertIs(launcher_options["stdout"], subprocess.DEVNULL)
        self.assertIs(launcher_options["stderr"], subprocess.DEVNULL)
        self.assertNotIn("capture_output", launcher_options)

    def test_desktop_cleans_stale_services_before_starting(self) -> None:
        """桌面壳冷启动必须先清理上次崩溃留下的半死服务。"""
        desktop = load_desktop_module()
        app = desktop.DesktopApp()
        app.window = MagicMock()

        with (
            patch.object(app, "_wait_until_ready", return_value=True),
            patch.object(desktop.time, "sleep"),
            patch.object(
                desktop.subprocess,
                "run",
                side_effect=[
                    subprocess.CompletedProcess([], 0),
                    subprocess.CompletedProcess([], 0),
                ],
            ) as run,
        ):
            app._bootstrap_services()

        self.assertEqual(run.call_count, 2)
        self.assertIn(str(desktop.STOP_SCRIPT), run.call_args_list[0].args[0])
        self.assertIn(str(desktop.START_SCRIPT), run.call_args_list[1].args[0])

    def test_desktop_backend_does_not_use_uvicorn_hot_reload(self) -> None:
        """桌面发行模式不能让热重载进程拖住应用退出和下次启动。"""
        desktop = load_desktop_module()
        start_script = desktop.START_SCRIPT.read_text(encoding="utf-8")

        self.assertNotIn('"--reload"', start_script)

    def test_service_launcher_prefers_canonical_dot_venv(self) -> None:
        """桌面壳与测试、依赖安装必须使用同一个 .venv。"""
        desktop = load_desktop_module()
        start_script = desktop.START_SCRIPT.read_text(encoding="utf-8")
        dot_venv = start_script.index('".venv\\Scripts\\python.exe"')
        legacy_venv = start_script.index('"venv\\Scripts\\python.exe"')

        self.assertLess(dot_venv, legacy_venv)

    def test_vite_binds_to_the_desktop_healthcheck_address(self) -> None:
        """Vite 仅监听 ::1 时，127.0.0.1 健康检查会永远失败。"""
        desktop = load_desktop_module()
        expected_host = urlparse(desktop.FRONTEND_URL).hostname
        start_script = desktop.START_SCRIPT.read_text(encoding="utf-8")

        self.assertEqual(expected_host, "127.0.0.1")
        self.assertIn(f"--host {expected_host}", start_script)
        self.assertNotIn("run dev -- --host", start_script)


if __name__ == "__main__":
    unittest.main()
