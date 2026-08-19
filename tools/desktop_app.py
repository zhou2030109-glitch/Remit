"""Windows desktop shell for Remit with WebView2 and a tray icon."""

from __future__ import annotations

import argparse
import base64
import ctypes
import json
import logging
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from ctypes import wintypes
from pathlib import Path
from typing import Any

import pystray
import webview
from PIL import Image, ImageDraw


APP_TITLE = "Remit 数学建模工作台"
APP_USER_MODEL_ID = "Remit.Desktop"
FRONTEND_URL = "http://127.0.0.1:15173/"
BACKEND_URL = "http://127.0.0.1:18000/"
MUTEX_NAME = "Local\\RemitDesktopApp"
ERROR_ALREADY_EXISTS = 183
ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "logs"
START_SCRIPT = ROOT / "tools" / "start_services.ps1"
STOP_SCRIPT = ROOT / "tools" / "stop_services.ps1"
DEFAULT_ICON_PNG = ROOT / "assets" / "remit-m-icon.png"
DEFAULT_ICON = ROOT / "assets" / "remit-m-icon.ico"
LOCAL_APP_DATA = Path(os.environ.get("LOCALAPPDATA", str(ROOT / ".desktop")))
APP_DATA_DIR = LOCAL_APP_DATA / "Remit"
SETTINGS_PATH = APP_DATA_DIR / "desktop_settings.json"
WEBVIEW_DATA_DIR = APP_DATA_DIR / "webview"

EXTERNAL_LINK_GUARD_SCRIPT = r"""
(() => {
  if (window.__remitExternalLinkGuardInstalled) return;
  window.__remitExternalLinkGuardInstalled = true;

  document.addEventListener('click', (event) => {
    const target = event.target;
    const anchor = target instanceof Element ? target.closest('a[href]') : null;
    if (!anchor) return;

    let destination;
    try {
      destination = new URL(anchor.href, window.location.href);
    } catch (_error) {
      return;
    }

    const supportedProtocol = ['http:', 'https:', 'mailto:', 'tel:'].includes(
      destination.protocol,
    );
    const localHost = ['127.0.0.1', 'localhost'].includes(destination.hostname);
    if (!supportedProtocol || localHost) return;

    anchor.target = '_blank';
    anchor.rel = 'noopener noreferrer';
  }, true);
})();
"""

def _load_icon_data_uri() -> str:
    """Embed the local app icon so the loading page works before Vite starts."""
    try:
        encoded = base64.b64encode(DEFAULT_ICON_PNG.read_bytes()).decode("ascii")
    except OSError:
        return ""
    return f"data:image/png;base64,{encoded}"


LOADING_HTML = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <style>
    * { box-sizing: border-box; }
    body {
      margin: 0; min-height: 100vh; display: grid; place-items: center;
      color: #172033; background:
        radial-gradient(circle at 18% 18%, rgba(124,58,237,.12), transparent 34%),
        radial-gradient(circle at 82% 78%, rgba(59,130,246,.10), transparent 32%),
        #f7f8fc;
      font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    }
    .card {
      width: min(520px, calc(100vw - 48px)); padding: 44px 46px;
      border: 1px solid rgba(148,163,184,.25); border-radius: 24px;
      background: rgba(255,255,255,.88); box-shadow: 0 24px 70px rgba(15,23,42,.12);
      text-align: center; backdrop-filter: blur(18px);
    }
    .mark {
      width: 82px; height: 82px; display: block; margin: 0 auto 24px;
      border-radius: 22px; object-fit: cover;
      box-shadow: 0 16px 36px rgba(79,70,229,.22);
    }
    h1 { margin: 0 0 9px; font-size: 30px; letter-spacing: -.4px; }
    .subtitle { margin: 0 0 30px; color: #64748b; font-size: 15px; }
    .track { height: 7px; overflow: hidden; border-radius: 999px; background: #e9eaf2; }
    .bar { height: 100%; width: 16%; border-radius: inherit; background: linear-gradient(90deg,#7c3aed,#2563eb); transition: width .35s ease; }
    #status { margin: 17px 0 0; min-height: 24px; color: #475569; font-size: 14px; }
    .hint { margin-top: 24px; color: #94a3b8; font-size: 12px; }
  </style>
</head>
<body>
  <main class="card">
    <img class="mark" src="__APP_ICON_DATA_URI__" alt="Remit">
    <h1>Remit</h1>
    <p class="subtitle">正在准备你的数学建模工作台</p>
    <div class="track"><div class="bar" id="bar"></div></div>
    <p id="status">正在检查本地服务…</p>
    <p class="hint">首次启动 MATLAB 时可能需要约 30–45 秒</p>
  </main>
  <script>
    window.setStartupStatus = (text, progress) => {
      document.getElementById('status').textContent = text;
      document.getElementById('bar').style.width = `${progress}%`;
    };
  </script>
</body>
</html>
"""
LOADING_HTML = LOADING_HTML.replace("__APP_ICON_DATA_URI__", _load_icon_data_uri())


class DesktopApp:
    """Owns the service lifecycle, WebView window, tray, and close policy."""

    def __init__(self) -> None:
        self.window: Any | None = None
        self.tray: pystray.Icon | None = None
        self.exit_requested = False
        self.shutdown_event = threading.Event()
        self.settings = self._load_settings()

    def run(self) -> None:
        # WebView2 emits target="_blank" links as NewWindowRequested. PyWebView
        # then delegates them to the user's default browser instead of replacing
        # the Remit application page.
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
        self.window = webview.create_window(
            APP_TITLE,
            html=LOADING_HTML,
            width=1440,
            height=900,
            min_size=(960, 640),
            maximized=True,
            background_color="#f7f8fc",
            text_select=True,
            zoomable=True,
        )
        if self.window is None:
            raise RuntimeError("无法创建桌面窗口")

        self.window.events.closing += self._on_window_closing
        self.window.events.closed += self._on_window_closed
        self.window.events.loaded += self._install_external_link_guard
        try:
            webview.start(
                self._on_webview_started,
                gui="edgechromium",
                debug=False,
                private_mode=False,
                storage_path=str(WEBVIEW_DATA_DIR),
                icon=str(DEFAULT_ICON) if DEFAULT_ICON.is_file() else None,
            )
        finally:
            self.shutdown_event.set()
            self._stop_tray()
            self._stop_services()

    def _on_webview_started(self) -> None:
        self._start_tray()
        threading.Thread(target=self._bootstrap_services, daemon=True).start()

    def _install_external_link_guard(self) -> None:
        """Keep every external anchor out of the application WebView.

        The delegated capture handler also covers links rendered later by Vue,
        so a future missing ``target=_blank`` cannot replace the desktop app.
        """
        if self.window is None or self.shutdown_event.is_set():
            return
        try:
            self.window.evaluate_js(EXTERNAL_LINK_GUARD_SCRIPT)
        except Exception:
            logging.debug("External link guard could not be installed", exc_info=True)

    def _bootstrap_services(self) -> None:
        try:
            logging.info("Desktop service bootstrap started")
            self._set_status("正在清理上次未正常退出的服务…", 16)
            cleanup = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(STOP_SCRIPT),
                ],
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW,
                check=False,
            )
            if cleanup.returncode != 0:
                raise RuntimeError(
                    f"无法清理上次残留的后台服务（代码 {cleanup.returncode}）"
                )
            logging.info("Stale desktop services cleaned up")

            self._set_status("正在启动 Redis、后端和前端…", 28)
            completed = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(START_SCRIPT),
                ],
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW,
                check=False,
            )
            if completed.returncode != 0:
                raise RuntimeError(
                    f"后台服务启动器返回失败（代码 {completed.returncode}）"
                )
            logging.info("Service launcher returned successfully")

            self._set_status("后端服务初始化中…", 55)
            if not self._wait_until_ready(BACKEND_URL, timeout=120):
                raise RuntimeError("后端在 120 秒内没有就绪")
            logging.info("Backend is ready")

            self._set_status("前端界面初始化中…", 80)
            if not self._wait_until_ready(FRONTEND_URL, timeout=60):
                raise RuntimeError("前端在 60 秒内没有就绪")
            logging.info("Frontend is ready")

            if self.shutdown_event.is_set() or self.window is None:
                return
            self._set_status("启动完成，正在进入工作台…", 100)
            time.sleep(0.35)
            self.window.load_url(FRONTEND_URL)
            logging.info("WebView navigation requested: %s", FRONTEND_URL)
        except Exception as exc:
            logging.exception("Desktop bootstrap failed")
            self._show_error(
                "Remit 启动失败",
                f"{exc}\n\n请查看日志目录：\n{LOG_DIR}",
            )

    def _set_status(self, text: str, progress: int) -> None:
        if self.window is None or self.shutdown_event.is_set():
            return
        script = f"window.setStartupStatus({json.dumps(text)}, {progress});"
        try:
            self.window.evaluate_js(script)
        except Exception:
            logging.debug("Loading page is not ready for status update", exc_info=True)

    def _wait_until_ready(self, url: str, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not self.shutdown_event.is_set():
            try:
                with urllib.request.urlopen(url, timeout=2) as response:
                    if response.status < 500:
                        return True
            except urllib.error.HTTPError as exc:
                if exc.code < 500:
                    return True
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(0.4)
        return False

    def _on_window_closing(self) -> bool:
        if self.exit_requested:
            return True

        action = self.settings.get("close_action")
        remember = False
        if action not in {"tray", "exit"}:
            action, remember = self._show_close_dialog()
            if action == "cancel":
                return False
            if remember:
                self.settings["close_action"] = action
                self._save_settings()

        if action == "tray":
            threading.Timer(0.05, self._hide_to_tray).start()
            return False

        self.exit_requested = True
        return True

    def _on_window_closed(self) -> None:
        self.shutdown_event.set()

    def _hide_to_tray(self) -> None:
        if self.window is None:
            return
        self.window.hide()
        if self.tray is not None:
            try:
                self.tray.notify(
                    "应用仍在后台运行，双击托盘图标可重新打开。", APP_TITLE
                )
            except Exception:
                logging.debug("Tray notification failed", exc_info=True)

    def _show_window(self, _icon: Any = None, _item: Any = None) -> None:
        if self.window is None:
            return
        self.window.show()
        self.window.restore()

    def _exit_from_tray(self, _icon: Any = None, _item: Any = None) -> None:
        self.exit_requested = True
        self.shutdown_event.set()
        if self.window is not None:
            self.window.destroy()

    def _reset_close_choice(self, _icon: Any = None, _item: Any = None) -> None:
        self.settings.pop("close_action", None)
        self._save_settings()
        if self.tray is not None:
            try:
                self.tray.notify("下次关闭窗口时将重新询问。", APP_TITLE)
            except Exception:
                logging.debug("Tray notification failed", exc_info=True)

    def _start_tray(self) -> None:
        image = self._load_tray_image()
        menu = pystray.Menu(
            pystray.MenuItem("打开 Remit", self._show_window, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("下次关闭时重新询问", self._reset_close_choice),
            pystray.MenuItem("退出应用", self._exit_from_tray),
        )
        self.tray = pystray.Icon("Remit", image, APP_TITLE, menu)
        self.tray.run_detached()

    def _stop_tray(self) -> None:
        if self.tray is not None:
            try:
                self.tray.stop()
            except Exception:
                logging.debug("Stopping tray failed", exc_info=True)
            self.tray = None

    @staticmethod
    def _load_tray_image() -> Image.Image:
        if DEFAULT_ICON.is_file():
            with Image.open(DEFAULT_ICON) as source:
                return source.convert("RGBA")
        image = Image.new("RGBA", (64, 64), "#4f46e5")
        drawer = ImageDraw.Draw(image)
        drawer.text((17, 12), "R", fill="white", stroke_width=1)
        return image

    def _show_close_dialog(self) -> tuple[str, bool]:
        try:
            import clr

            clr.AddReference("System.Drawing")
            clr.AddReference("System.Windows.Forms")
            from System.Drawing import Color, Font, FontStyle, Icon, Point, Size
            from System.Windows.Forms import (
                Button,
                CheckBox,
                DialogResult,
                Form,
                FormBorderStyle,
                FormStartPosition,
                Label,
                RadioButton,
            )

            form = Form()
            form.Text = "关闭 Remit"
            form.ClientSize = Size(520, 340)
            form.FormBorderStyle = FormBorderStyle.FixedDialog
            form.StartPosition = FormStartPosition.CenterScreen
            form.MaximizeBox = False
            form.MinimizeBox = False
            form.ShowInTaskbar = False
            if DEFAULT_ICON.is_file():
                form.Icon = Icon(str(DEFAULT_ICON))

            heading = Label()
            heading.Text = "点击关闭按钮以后"
            heading.AutoSize = True
            heading.Font = Font("Microsoft YaHei UI", 18, FontStyle.Bold)
            heading.Location = Point(44, 30)

            tray_option = RadioButton()
            tray_option.Text = "最小化到系统托盘"
            tray_option.AutoSize = True
            tray_option.Checked = True
            tray_option.Font = Font("Microsoft YaHei UI", 12)
            tray_option.Location = Point(48, 92)

            tray_hint = Label()
            tray_hint.Text = "应用继续在后台运行，可从托盘重新打开"
            tray_hint.AutoSize = True
            tray_hint.ForeColor = Color.FromArgb(100, 116, 139)
            tray_hint.Location = Point(76, 122)

            exit_option = RadioButton()
            exit_option.Text = "退出应用程序"
            exit_option.AutoSize = True
            exit_option.Font = Font("Microsoft YaHei UI", 12)
            exit_option.Location = Point(48, 162)

            exit_hint = Label()
            exit_hint.Text = "关闭工作台并停止 Redis、后端和前端"
            exit_hint.AutoSize = True
            exit_hint.ForeColor = Color.FromArgb(100, 116, 139)
            exit_hint.Location = Point(76, 192)

            remember_choice = CheckBox()
            remember_choice.Text = "记住我的选择，下次不再提示"
            remember_choice.AutoSize = True
            remember_choice.Location = Point(48, 231)

            cancel_button = Button()
            cancel_button.Text = "取消"
            cancel_button.DialogResult = DialogResult.Cancel
            cancel_button.Size = Size(132, 42)
            cancel_button.Location = Point(206, 282)

            confirm_button = Button()
            confirm_button.Text = "确定"
            confirm_button.DialogResult = DialogResult.OK
            confirm_button.Size = Size(132, 42)
            confirm_button.Location = Point(352, 282)

            for control in (
                heading,
                tray_option,
                tray_hint,
                exit_option,
                exit_hint,
                remember_choice,
                cancel_button,
                confirm_button,
            ):
                form.Controls.Add(control)
            form.AcceptButton = confirm_button
            form.CancelButton = cancel_button

            try:
                result = form.ShowDialog()
                if result != DialogResult.OK:
                    return "cancel", False
                action = "tray" if tray_option.Checked else "exit"
                return action, bool(remember_choice.Checked)
            finally:
                form.Dispose()
        except Exception:
            logging.exception("Close dialog failed, using MessageBox fallback")
            answer = ctypes.windll.user32.MessageBoxW(
                None,
                "选择“是”最小化到托盘；选择“否”退出应用程序。",
                "关闭 Remit",
                0x00000003 | 0x00000020,
            )
            return {6: "tray", 7: "exit"}.get(answer, "cancel"), False

    @staticmethod
    def _show_error(title: str, message: str) -> None:
        ctypes.windll.user32.MessageBoxW(None, message, title, 0x00000010)

    @staticmethod
    def _load_settings() -> dict[str, Any]:
        try:
            return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_settings(self) -> None:
        APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(
            json.dumps(self.settings, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _stop_services() -> None:
        try:
            subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(STOP_SCRIPT),
                ],
                cwd=ROOT,
                capture_output=True,
                timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW,
                check=False,
            )
        except Exception:
            logging.exception("Stopping desktop services failed")


def _configure_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=LOG_DIR / "desktop_app.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(threadName)s %(message)s",
        encoding="utf-8",
    )


def _configure_windows_identity() -> None:
    """Give the interpreter-hosted window its own Windows taskbar identity."""
    if os.name != "nt":
        return
    setter = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
    setter.argtypes = [wintypes.LPCWSTR]
    setter.restype = ctypes.c_long
    result = setter(APP_USER_MODEL_ID)
    if result != 0:
        raise ctypes.WinError(result)


def _activate_existing_window() -> None:
    user32 = ctypes.windll.user32
    found: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd: int, _lparam: int) -> bool:
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, len(buffer))
        if "Remit" in buffer.value:
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(callback_type(callback), 0)
    if found:
        user32.ShowWindow(found[0], 9)
        user32.SetForegroundWindow(found[0])


def _acquire_single_instance() -> int | None:
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        raise ctypes.WinError()
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        _activate_existing_window()
        return None
    return int(handle)


def _check_installation() -> None:
    required_files = (
        START_SCRIPT,
        STOP_SCRIPT,
        DEFAULT_ICON_PNG,
        DEFAULT_ICON,
    )
    missing = [str(path) for path in required_files if not path.is_file()]
    if missing:
        raise FileNotFoundError("缺少桌面组件: " + ", ".join(missing))
    print("DESKTOP_APP_CHECK_OK")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    _configure_logging()
    _configure_windows_identity()
    if args.check:
        _check_installation()
        return 0

    mutex_handle = _acquire_single_instance()
    if mutex_handle is None:
        return 0
    try:
        DesktopApp().run()
        return 0
    finally:
        ctypes.windll.kernel32.CloseHandle(mutex_handle)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as error:
        _configure_logging()
        logging.exception("Desktop application crashed")
        ctypes.windll.user32.MessageBoxW(
            None,
            f"桌面应用启动失败：\n{error}\n\n日志：{LOG_DIR / 'desktop_app.log'}",
            "Remit",
            0x00000010,
        )
        raise
