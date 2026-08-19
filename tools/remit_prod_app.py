"""Remit 生产版桌面入口：启动 Redis 与后端，打开工作台并常驻托盘。

设计要点：
- 不依赖 PowerShell / Node / 系统 Python，全部使用包内 runtime。
- WebView 壳（pywebview）惰性加载：可用则窗口化，不可用自动降级默认浏览器。
- 托盘常驻；退出托盘时停止本包启动的后台服务。
- 支持 --check / --stop / --no-ui 三种无界面模式，供安装与排障使用。
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

APP_TITLE = "Remit 数学建模工作台"
FRONTEND_URL = "http://127.0.0.1:18000/"
REDIS_PORT = 16379
BACKEND_PORT = 18000

# 启动器自身（--check / --stop / UI）不写入任何 __pycache__，
# 后端进程则通过 env PYTHONDONTWRITEBYTECODE=1 控制。
sys.dont_write_bytecode = True

if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).resolve().parent
else:
    ROOT = Path(__file__).resolve().parent.parent

RUNTIME_PYTHON = ROOT / "runtime" / "python" / "python.exe"
REDIS_EXE = ROOT / "tools" / "redis" / "redis-server.exe"
BACKEND_DIR = ROOT / "backend"
LOG_DIR = ROOT / "logs"
ICON_PATH = ROOT / "assets" / "remit-m-icon.ico"
KERNEL_JSON = (
    ROOT
    / "runtime"
    / "share"
    / "jupyter"
    / "kernels"
    / "python3"
    / "kernel.json"
)

_services: list[subprocess.Popen] = []


def _log_file(name: str) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return LOG_DIR / f"{name}.out.log"


def _err_file(name: str) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return LOG_DIR / f"{name}.err.log"


def _pid_file(name: str) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return LOG_DIR / f"{name}.pid"


def configure_logging() -> None:
    """日志同时输出到控制台与 logs/app.log。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = LOG_DIR / "app.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log, encoding="utf-8"),
        ],
    )


def port_is_open(port: int) -> bool:
    """判断本机端口是否已有监听。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.6)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _start_hidden(
    args: list[str],
    cwd: Path,
    name: str,
    env: dict[str, str] | None = None,
) -> subprocess.Popen:
    """以无窗口方式启动后台服务，记录 PID 与日志。"""
    out = open(_log_file(name), "ab")
    err = open(_err_file(name), "ab")
    process = subprocess.Popen(
        args,
        cwd=str(cwd),
        env=env,
        stdout=out,
        stderr=err,
        stdin=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
    )
    process._remit_log_streams = (out, err)  # type: ignore[attr-defined]
    _services.append(process)
    _pid_file(name).write_text(str(process.pid), encoding="ascii")
    logging.info("%s 已启动 (PID %s)", name, process.pid)
    return process


def ensure_redis_running() -> None:
    """启动包内 Redis；端口已占用时视为已运行。"""
    if port_is_open(REDIS_PORT):
        logging.info("Redis 已在端口 %s 监听，复用现有实例", REDIS_PORT)
        return
    if not REDIS_EXE.is_file():
        raise RuntimeError(f"缺少 Redis 可执行文件: {REDIS_EXE}")
    _start_hidden(
        [
            str(REDIS_EXE),
            "--port",
            str(REDIS_PORT),
            "--bind",
            "127.0.0.1",
            "::1",
            "--save",
            "",
            "--appendonly",
            "no",
        ],
        REDIS_EXE.parent,
        "redis",
    )
    _wait_port(REDIS_PORT, timeout=30, service="Redis")


def prepare_jupyter_kernelspec() -> None:
    """确保 Jupyter kernelspec 指向包内 Python，避免依赖 PATH。"""
    if not KERNEL_JSON.is_file():
        return
    try:
        spec = json.loads(KERNEL_JSON.read_text(encoding="utf-8"))
    except Exception:
        return
    argv = spec.get("argv") or []
    if argv and argv[0] != str(RUNTIME_PYTHON):
        argv[0] = str(RUNTIME_PYTHON)
        spec["argv"] = argv
        KERNEL_JSON.write_text(
            json.dumps(spec, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def ensure_backend_running() -> None:
    """启动包内 FastAPI 后端（同时托管前端静态文件）。"""
    if port_is_open(BACKEND_PORT):
        logging.info("后端已在端口 %s 监听，复用现有实例", BACKEND_PORT)
        return
    if not RUNTIME_PYTHON.is_file():
        raise RuntimeError(f"缺少包内 Python: {RUNTIME_PYTHON}")
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env["ENV"] = "dev"
    env["PATH"] = str(RUNTIME_PYTHON.parent) + os.pathsep + env.get("PATH", "")
    _start_hidden(
        [
            str(RUNTIME_PYTHON),
            "-B",
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(BACKEND_PORT),
            "--ws-ping-interval",
            "60",
            "--ws-ping-timeout",
            "120",
        ],
        BACKEND_DIR,
        "backend",
        env=env,
    )
    _wait_port(BACKEND_PORT, timeout=180, service="后端")


def _wait_port(port: int, timeout: float, service: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if port_is_open(port):
            logging.info("%s 就绪 (端口 %s)", service, port)
            return
        time.sleep(0.5)
    raise RuntimeError(f"{service} 在 {timeout:.0f} 秒内未就绪，请查看 logs 目录")


def wait_until_ready(url: str, timeout: float) -> bool:
    """轮询 HTTP 直到返回非 5xx 状态码。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
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


def open_user_interface() -> None:
    """优先 WebView 窗口，失败时回退默认浏览器；关闭窗口时隐藏到托盘。"""
    try:
        importlib.import_module("webview")
        from webview import create_window, start as webview_start

        window = create_window(
            APP_TITLE,
            FRONTEND_URL,
            width=1440,
            height=900,
            min_size=(960, 640),
            maximized=True,
            background_color="#f7f8fc",
            text_select=True,
            zoomable=True,
        )
        if window is None:
            raise RuntimeError("无法创建窗口")

        def _hide_to_tray() -> bool:
            try:
                window.hide()
            except Exception:
                pass
            return False  # 取消关闭，隐藏到托盘

        window.events.closing += _hide_to_tray
        logging.info("使用 WebView 窗口")
        webview_start(gui="edgechromium", debug=False, private_mode=False)
        return
    except Exception as exc:
        logging.warning("WebView 不可用，改用默认浏览器: %s", exc)

    import webbrowser

    webbrowser.open(FRONTEND_URL)
    logging.info("已用默认浏览器打开 %s", FRONTEND_URL)


def _terminate_pid(pid: int) -> None:
    """按 PID 结束进程（仅限本包启动的服务）。"""
    if pid <= 0:
        return
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
            check=False,
        )
    except Exception:
        pass


def stop_services() -> None:
    """终止本包启动的后台服务。PID 文件缺失时按端口归属清理。"""
    for process in list(_services):
        try:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=8)
        except Exception:
            pass
    _services.clear()

    killed_paths: set[str] = set()
    for name, port in (("redis", REDIS_PORT), ("backend", BACKEND_PORT)):
        pid_file = _pid_file(name)
        try:
            pid = int(pid_file.read_text(encoding="ascii").strip())
        except Exception:
            pid = 0
        if pid > 0:
            _terminate_pid(pid)
            killed_paths.add(str(name))
            try:
                pid_file.unlink()
            except OSError:
                pass
    logging.info("后台服务已停止%s", f" ({', '.join(sorted(killed_paths))})" if killed_paths else "")


def _build_tray_icon() -> Any:
    """构建托盘图标；pystray/PIL 缺失时返回 None。"""
    try:
        from PIL import Image
        import pystray

        try:
            image = Image.open(ICON_PATH).convert("RGBA") if ICON_PATH.is_file() else None
        except Exception:
            image = None
        if image is None:
            image = Image.new("RGBA", (64, 64), "#4f46e5")

        def do_show(_icon: Any = None, _item: Any = None) -> None:
            import webbrowser

            webbrowser.open(FRONTEND_URL)

        def do_exit(_icon: Any = None, _item: Any = None) -> None:
            stop_services()
            if _icon is not None:
                _icon.stop()
            os._exit(0)

        menu = pystray.Menu(
            pystray.MenuItem("打开 Remit", do_show, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出应用", do_exit),
        )
        return pystray.Icon("Remit", image, APP_TITLE, menu)
    except Exception as exc:
        logging.warning("托盘不可用: %s", exc)
        return None


def run_tray() -> None:
    """常驻托盘线程；图标退出后主程序停止服务并退出。"""
    icon = _build_tray_icon()

    if icon is None:
        logging.warning("托盘不可用，应用保持运行；退出请使用 停止Remit.bat")
        try:
            while True:
                time.sleep(5)
        except KeyboardInterrupt:
            pass
        return

    def _tray_loop() -> None:
        try:
            icon.run()
        except Exception:
            logging.exception("托盘运行异常")

    tray_thread = threading.Thread(target=_tray_loop, name="remit-tray")
    tray_thread.daemon = False
    tray_thread.start()
    try:
        tray_thread.join()
    except KeyboardInterrupt:
        pass
    stop_services()


def check_installation() -> str:
    """检查包内文件是否齐全，供安装器与排障调用。"""
    required = [
        ("包内 Python", RUNTIME_PYTHON),
        ("Redis", REDIS_EXE),
        ("后端代码", BACKEND_DIR / "app"),
        ("前端静态文件", ROOT / "frontend" / "dist" / "index.html"),
        ("字体", BACKEND_DIR / "fonts" / "simhei.ttf"),
        ("图标", ICON_PATH),
    ]
    missing = [
        label
        for label, path in required
        if not (path.is_file() if path.suffix else path.is_dir())
    ]
    if missing:
        return "PACKAGED_APP_CHECK_FAIL: " + ", ".join(missing)
    return "PACKAGED_APP_CHECK_OK"


def main() -> int:
    parser = argparse.ArgumentParser(description="Remit 生产版启动器")
    parser.add_argument("--check", action="store_true", help="检查安装完整性")
    parser.add_argument("--stop", action="store_true", help="停止后台服务")
    parser.add_argument("--no-ui", action="store_true", help="只启动服务，不打开界面")
    args = parser.parse_args()

    configure_logging()

    if args.check:
        print(check_installation())
        return 0

    if args.stop:
        stop_services()
        return 0

    try:
        ensure_redis_running()
        prepare_jupyter_kernelspec()
        ensure_backend_running()
    except Exception as exc:
        logging.exception("服务启动失败")
        print(f"Remit startup failed: {exc}", file=sys.stderr)
        stop_services()
        return 1

    if not wait_until_ready(FRONTEND_URL, timeout=180):
        logging.error("界面在 180 秒内未就绪")
        stop_services()
        return 1

    if args.no_ui:
        print(f"Remit is running at {FRONTEND_URL}")
        try:
            while True:
                time.sleep(5)
        except KeyboardInterrupt:
            pass
        stop_services()
        return 0

    run_tray()
    open_user_interface()
    return 0


if __name__ == "__main__":
    sys.exit(main())
