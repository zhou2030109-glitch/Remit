"""Remit 生产版桌面入口：启动后端，打开工作台并常驻托盘。

设计要点：
- 不依赖 PowerShell / Node / 系统 Python，全部使用包内 runtime。
- 后端就绪后直接打开系统默认浏览器，托盘可再次打开工作台。
- 托盘常驻；退出托盘时停止本包启动的后台服务。
- 支持 --check / --stop / --no-ui 三种无界面模式，供安装与排障使用。
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import logging
import os
import socket
import sqlite3
from contextlib import closing
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from ctypes import wintypes
from pathlib import Path
from typing import Any

import psutil

APP_TITLE = "Remit 数学建模工作台"
FRONTEND_URL = "http://127.0.0.1:18000/"
# 旧版本使用的 Redis 端口；仅用于停止时回收遗留进程。
LEGACY_REDIS_PORT = 16379
BACKEND_PORT = 18000

# 启动器自身（--check / --stop / UI）不写入任何 __pycache__，
# 后端进程则通过 env PYTHONDONTWRITEBYTECODE=1 控制。
sys.dont_write_bytecode = True

if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).resolve().parent
else:
    ROOT = Path(__file__).resolve().parent.parent

RUNTIME_PYTHON = ROOT / "runtime" / "python" / "python.exe"
BACKEND_DIR = ROOT / "backend"
DATA_DIR = Path(os.environ.get("REMIT_DATA_DIR") or (Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Remit" / "data")).expanduser().resolve()
LOG_DIR = DATA_DIR / "logs"
APPLICATION_OWNER_PATH = LOG_DIR / "app-owner.json"
ICON_PATH = ROOT / "assets" / "remit-m-icon.ico"
KERNEL_JSON = (
    ROOT / "runtime" / "share" / "jupyter" / "kernels" / "python3" / "kernel.json"
)

_services: list[subprocess.Popen] = []


def _acquire_single_instance(*, allow_existing: bool = False) -> int | None:
    """同一安装只保留一个服务所有者，重复启动只打开已有界面。"""
    install_id = hashlib.sha256(str(ROOT.resolve()).casefold().encode()).hexdigest()[
        :24
    ]
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.CreateMutexW(None, False, f"Local\\RemitPackagedApp-{install_id}")
    if not handle:
        raise ctypes.WinError()
    if kernel32.GetLastError() == 183 and not allow_existing:
        kernel32.CloseHandle(handle)
        return None
    return int(handle)


def _release_single_instance(handle: int) -> None:
    ctypes.windll.kernel32.CloseHandle(handle)


def _record_application_owner() -> None:
    """停止命令必须先结束正在启动服务的所有者，避免清理后又被重启。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    APPLICATION_OWNER_PATH.write_text(
        json.dumps({"pid": os.getpid(), "created": psutil.Process().create_time()}),
        encoding="utf-8",
    )


def _clear_application_owner() -> None:
    try:
        APPLICATION_OWNER_PATH.unlink()
    except FileNotFoundError:
        pass


def _stop_application_owner() -> None:
    """只停止身份记录对应的本安装启动器，绝不信任复用后的 PID。"""
    try:
        identity = json.loads(APPLICATION_OWNER_PATH.read_text(encoding="utf-8"))
        pid = int(identity["pid"])
        created = float(identity["created"])
    except (OSError, ValueError, TypeError, KeyError):
        return
    if pid <= 0 or pid == os.getpid():
        return
    try:
        process = psutil.Process(pid)
        if process.create_time() == created and _process_belongs_to_install(pid):
            _terminate_pid(pid)
            try:
                process.wait(timeout=5)
            except psutil.TimeoutExpired as exc:
                raise RuntimeError("无法停止当前 Remit 实例，请先从托盘退出") from exc
    except psutil.NoSuchProcess:
        pass
    _clear_application_owner()


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


def _process_belongs_to_install(pid: int) -> bool:
    """只认领可执行文件或命令行明确位于当前安装目录的进程。"""
    if pid <= 0 or pid == os.getpid():
        return False
    try:
        process = psutil.Process(pid)
        candidates = [process.exe(), *process.cmdline()]
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return False

    root_prefix = str(ROOT.resolve()).casefold().rstrip("\\/") + os.sep
    for candidate in candidates:
        if not candidate:
            continue
        try:
            normalized = str(Path(candidate).resolve()).casefold()
        except (OSError, ValueError):
            normalized = str(candidate).casefold()
        if normalized.startswith(root_prefix):
            return True
    return False


def _owned_service_pid_for_port(port: int) -> int | None:
    """返回当前安装目录在指定端口上的监听进程，避免误杀其他程序。"""
    try:
        for connection in psutil.net_connections(kind="tcp"):
            if (
                connection.status == psutil.CONN_LISTEN
                and connection.laddr
                and connection.laddr.port == port
                and connection.pid
                and _process_belongs_to_install(int(connection.pid))
            ):
                return int(connection.pid)
    except (psutil.AccessDenied, OSError):
        logging.warning("无法检查端口 %s 的进程归属", port)
    return None


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
        if _owned_service_pid_for_port(BACKEND_PORT) is not None:
            logging.info("后端已在端口 %s 监听，复用当前安装实例", BACKEND_PORT)
            return
        raise RuntimeError(
            f"端口 {BACKEND_PORT} 已被其他程序占用，请先退出旧版 Remit 或占用该端口的程序"
        )
    if not RUNTIME_PYTHON.is_file():
        raise RuntimeError(f"缺少包内 Python: {RUNTIME_PYTHON}")
    prepare_user_data()
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env["ENV"] = "dev"
    env["PYTHONPATH"] = str(BACKEND_DIR)
    env["REMIT_USER_CONFIG_PATH"] = str(DATA_DIR / ".env.user")
    env["REMIT_MESSAGES_DIR"] = str(DATA_DIR / "logs" / "messages")
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
        DATA_DIR,
        "backend",
        env=env,
    )
    _wait_port(BACKEND_PORT, timeout=180, service="后端")


def prepare_user_data() -> None:
    """首次启动复制旧安装内的数据，保留原件；升级不得覆盖已有用户数据。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    prepare_message_archive()
    marker = DATA_DIR / "migration-v1.json"
    if marker.is_file():
        return
    old = BACKEND_DIR / "project"
    destination = DATA_DIR / "project"
    if old.is_dir() and not destination.exists():
        staging = DATA_DIR / "project-migration-pending"
        if staging.exists():
            raise RuntimeError("发现上次未完成的数据迁移，原项目保留在旧安装目录。请核对 project-migration-pending 后重试。")
        shutil.copytree(old, staging)
        staging.replace(destination)
    destination.mkdir(exist_ok=True)
    old_config = BACKEND_DIR / ".env.user"
    if old_config.is_file() and not (DATA_DIR / ".env.user").exists():
        shutil.copy2(old_config, DATA_DIR / ".env.user")
    marker.write_text(json.dumps({"schema_version": 1, "legacy_copy_preserved": True,
                                  "rollback": "旧版本仅能读取保留的旧副本；请勿将新数据覆盖回旧目录。"}, ensure_ascii=False), encoding="utf-8")


def prepare_message_archive() -> None:
    """复制旧消息档案；SQLite 在线备份包含已提交的 WAL 数据，原档案保留。"""
    marker = DATA_DIR / "migration-messages-v2.json"
    if marker.is_file():
        return
    source = BACKEND_DIR / "logs" / "messages"
    destination = DATA_DIR / "logs" / "messages"
    if source.resolve() == destination.resolve():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and any(destination.iterdir()):
        # An existing user archive is authoritative; never overwrite or merge
        # event sequence IDs, which are also browser reconnect cursors.
        status = "existing_user_archive_preserved"
    else:
        staging = destination.with_name("messages-migration-pending")
        if staging.exists():
            raise RuntimeError("发现未完成的消息迁移，旧对话仍保留；请核对 messages-migration-pending 后重试。")
        staging.mkdir()
        if source.is_dir():
            for legacy in source.glob("*.json"):
                shutil.copy2(legacy, staging / legacy.name)
            database = source / "messages.sqlite3"
            if database.is_file():
                with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as original:
                    with closing(sqlite3.connect(staging / database.name)) as copied:
                        deadline = time.monotonic() + 30
                        def check_backup_progress(_status, _remaining, _total):
                            if time.monotonic() > deadline:
                                raise TimeoutError("消息档案备份超时，旧档案和暂存副本均保留。")
                        original.backup(copied, pages=256, progress=check_backup_progress)
                        if copied.execute("PRAGMA quick_check").fetchone() != ("ok",):
                            raise RuntimeError("消息档案备份校验失败，已保留原始档案和迁移副本。")
        if destination.exists():
            destination.rmdir()  # Only the empty destination checked above.
        staging.replace(destination)
        status = "legacy_archive_copied"
    marker.write_text(json.dumps({"schema_version": 2, "status": status,
                                  "legacy_copy_preserved": True}), encoding="utf-8")


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


def _open_default_browser_once() -> bool:
    """请求 Windows 打开默认浏览器；失败时回退 Python 浏览器注册表。"""
    if os.name == "nt":
        try:
            result = ctypes.windll.shell32.ShellExecuteW(
                None,
                "open",
                FRONTEND_URL,
                None,
                str(ROOT),
                1,
            )
            if int(result) > 32:
                return True
            logging.warning("Windows ShellExecute 打开浏览器失败，代码 %s", result)
        except Exception:
            logging.exception("Windows ShellExecute 打开浏览器异常")

    try:
        import webbrowser

        return bool(webbrowser.open(FRONTEND_URL, new=2))
    except Exception:
        logging.exception("Python 默认浏览器回退也失败")
        return False


def open_default_browser_with_retry(
    attempts: int = 3,
    retry_delay: float = 1.0,
) -> bool:
    """首次启动时协议处理器可能尚未响应，短暂重试保证界面可见。"""
    for attempt in range(1, max(1, attempts) + 1):
        if _open_default_browser_once():
            logging.info("已用默认浏览器打开 %s（第 %d 次）", FRONTEND_URL, attempt)
            return True
        if attempt < attempts:
            time.sleep(retry_delay)
    return False


def open_user_interface() -> bool:
    """安装版直接打开系统默认浏览器，避免依赖 WebView2。"""
    return open_default_browser_with_retry()


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

    killed_services: set[str] = set()
    killed_pids: set[int] = set()
    for name, port in (("redis", LEGACY_REDIS_PORT), ("backend", BACKEND_PORT)):
        pid_file = _pid_file(name)
        try:
            pid = int(pid_file.read_text(encoding="ascii").strip())
        except Exception:
            pid = 0
        if pid > 0 and _process_belongs_to_install(pid):
            _terminate_pid(pid)
            killed_pids.add(pid)
            killed_services.add(name)

        owned_pid = _owned_service_pid_for_port(port)
        if owned_pid is not None and owned_pid not in killed_pids:
            _terminate_pid(owned_pid)
            killed_pids.add(owned_pid)
            killed_services.add(name)

        try:
            pid_file.unlink()
        except OSError:
            pass
    logging.info(
        "后台服务已停止%s",
        f" ({', '.join(sorted(killed_services))})" if killed_services else "",
    )


def _build_tray_icon() -> Any:
    """构建托盘图标；pystray/PIL 缺失时返回 None。"""
    try:
        from PIL import Image
        import pystray

        try:
            image = (
                Image.open(ICON_PATH).convert("RGBA") if ICON_PATH.is_file() else None
            )
        except Exception:
            image = None
        if image is None:
            image = Image.new("RGBA", (64, 64), "#4f46e5")

        def do_show(_icon: Any = None, _item: Any = None) -> None:
            if not open_default_browser_with_retry():
                show_startup_error("工作台已在本机运行，但无法打开默认浏览器")

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
    """运行阻塞式托盘循环；调用方负责把它放在后台线程。"""
    icon = _build_tray_icon()

    if icon is None:
        logging.warning("托盘不可用，应用保持运行；退出请使用 停止Remit.bat")
        try:
            while True:
                time.sleep(5)
        except KeyboardInterrupt:
            pass
        return

    try:
        icon.run()
    except KeyboardInterrupt:
        pass
    except Exception:
        logging.exception("托盘运行异常")
    finally:
        stop_services()


def show_startup_error(message: str) -> None:
    """pythonw 没有控制台，启动失败必须显示 Windows 对话框。"""
    logging.error("启动失败: %s", message)
    try:
        ctypes.windll.user32.MessageBoxW(
            None,
            f"Remit 启动失败：\n{message}\n\n日志目录：{LOG_DIR}",
            APP_TITLE,
            0x00000010,
        )
    except Exception:
        logging.exception("无法显示启动错误对话框")


def check_installation() -> str:
    """检查包内文件是否齐全，供安装器与排障调用。"""
    required = [
        ("包内 Python", RUNTIME_PYTHON),
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
        result = check_installation()
        print(result)
        if result != "PACKAGED_APP_CHECK_OK":
            logging.error(result)
            return 1
        return 0

    if args.stop:
        # 保持命名对象存活到清理完成，停止期间重复点击不能另建服务所有者。
        stop_guard = _acquire_single_instance(allow_existing=True)
        if stop_guard is None:
            raise RuntimeError("无法锁定 Remit 停止过程")
        try:
            _stop_application_owner()
            stop_services()
        finally:
            _release_single_instance(stop_guard)
        return 0

    handle = _acquire_single_instance()
    if handle is None:
        if args.no_ui:
            return 0
        if wait_until_ready(FRONTEND_URL, timeout=180) and open_user_interface():
            return 0
        show_startup_error("已有 Remit 实例尚未就绪，请查看其启动日志")
        return 1
    try:
        _record_application_owner()
        return _run_application(no_ui=args.no_ui)
    finally:
        try:
            _clear_application_owner()
        finally:
            _release_single_instance(handle)


def _run_application(*, no_ui: bool) -> int:
    """由持有单实例句柄的进程管理服务和托盘。"""
    try:
        prepare_jupyter_kernelspec()
        ensure_backend_running()
    except Exception as exc:
        logging.exception("服务启动失败")
        if sys.stderr is not None:
            print(f"Remit startup failed: {exc}", file=sys.stderr)
        show_startup_error(str(exc))
        stop_services()
        return 1

    if not wait_until_ready(FRONTEND_URL, timeout=180):
        message = "本机服务已经启动，但界面在 180 秒内未就绪"
        logging.error(message)
        show_startup_error(message)
        stop_services()
        return 1

    if no_ui:
        print(f"Remit is running at {FRONTEND_URL}")
        try:
            while True:
                time.sleep(5)
        except KeyboardInterrupt:
            pass
        stop_services()
        return 0

    if not open_user_interface():
        show_startup_error("工作台已在本机运行，但无法打开默认浏览器")
        stop_services()
        return 1

    # 浏览器成功打开后再进入托盘常驻，避免托盘循环阻塞首次界面。
    tray_thread = threading.Thread(
        target=run_tray,
        name="remit-tray",
        daemon=False,
    )
    tray_thread.start()
    tray_thread.join()
    return 0


if __name__ == "__main__":
    sys.exit(main())
