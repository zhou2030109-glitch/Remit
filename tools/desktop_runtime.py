"""Packaged desktop entry point. All writable state lives outside the installation."""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = sys.platform == "win32"


def data_directory() -> Path:
    if override := os.environ.get("REMIT_DATA_DIR"):
        return Path(override).expanduser().resolve()
    if WINDOWS:
        return Path(os.environ["LOCALAPPDATA"]) / "Remit" / "Desktop"
    return Path.home() / "Library" / "Application Support" / "Remit"


def python_executable(root: Path = ROOT) -> Path:
    return root / "runtime/python" / ("python.exe" if WINDOWS else "bin/python3")


def tex_directory(root: Path = ROOT) -> Path:
    return root / "runtime/tinytex/bin" / ("windows" if WINDOWS else "universal-darwin")


def environment(data: Path, root: Path = ROOT) -> dict[str, str]:
    """Create relocated kernel/cache paths without touching the app bundle."""
    for name in ("project/work_dir", "logs", "cache", "jupyter/kernels/python3"):
        (data / name).mkdir(parents=True, exist_ok=True)
    python = python_executable(root)
    kernel = data / "jupyter/kernels/python3/kernel.json"
    kernel.write_text(json.dumps({
        "argv": [str(python), "-m", "ipykernel_launcher", "-f", "{connection_file}"],
        "display_name": "Remit Python", "language": "python",
    }), encoding="utf-8")
    env = os.environ.copy()
    # Never inherit developer Python paths or a virtualenv into the installed app.
    for key in ("PYTHONHOME", "VIRTUAL_ENV", "PYTHONSTARTUP"):
        env.pop(key, None)
    env.update({
        "PYTHONPATH": str(root / "backend"), "PYTHONUTF8": "1",
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
        "REMIT_BUNDLED_TEX": "1",
        "REMIT_USER_CONFIG_PATH": str(data / ".env.user"),
        "JUPYTER_PATH": str(data / "jupyter"),
        "JUPYTER_CONFIG_DIR": str(data / "jupyter/config"),
        "JUPYTER_RUNTIME_DIR": str(data / "jupyter/runtime"),
        "MPLCONFIGDIR": str(data / "cache/matplotlib"),
        "HF_HOME": str(data / "cache/huggingface"),
        "TEXMFVAR": str(data / "cache/texmf-var"),
        "TEXMFCONFIG": str(data / "cache/texmf-config"),
        "TEXMFHOME": str(data / "texmf"),
        "PATH": os.pathsep.join([str(python.parent), str(tex_directory(root)), env.get("PATH", "")]),
    })
    sites = python.parent / "Lib/site-packages" if WINDOWS else root / "runtime/python/lib/python3.12/site-packages"
    env["PYPANDOC_PANDOC"] = str(sites / "pypandoc/files" / ("pandoc.exe" if WINDOWS else "pandoc"))
    return env


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def lock_instance(data: Path):
    handle = (data / "desktop.lock").open("a+b")
    handle.seek(0)
    handle.write(b"0")
    handle.flush()
    handle.seek(0)
    try:
        if WINDOWS:
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def check_files() -> dict:
    required = [python_executable(), ROOT / "backend/app/main.py",
                ROOT / "frontend/dist/index.html", ROOT / "assets/remit-m-icon.png",
                ROOT / "backend/fonts/FandolHei-Regular.otf",
                tex_directory() / ("xelatex.exe" if WINDOWS else "xelatex")]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError("安装文件不完整，请重新安装：" + ", ".join(missing))
    return {"installation_complete": True, "version": (ROOT / "VERSION").read_text().strip()}


def terminate_children(children):
    import psutil
    for child in reversed(children):
        if child.poll() is not None:
            continue
        try:
            parent = psutil.Process(child.pid)
            descendants = parent.children(recursive=True)
            # Send graceful termination first, then bounded cleanup of this process tree.
            parent.terminate()
            _, alive = psutil.wait_procs([parent], timeout=12)
            for process in descendants + alive:
                try:
                    process.kill()
                except psutil.NoSuchProcess:
                    pass
            child.wait(timeout=8)
        except (psutil.NoSuchProcess, subprocess.TimeoutExpired):
            pass


def start(data: Path, *, smoke: bool = False, report: Path | None = None):
    check_files()
    env = environment(data)
    lock = lock_instance(data)
    owner = data / "desktop.json"
    if lock is None:
        if smoke:
            raise RuntimeError("Smoke test data directory is already in use")
        for _ in range(90):
            try:
                url = json.loads(owner.read_text())["url"]
                webbrowser.open(url)
                return
            except (OSError, KeyError, ValueError):
                time.sleep(1)
        raise RuntimeError("Remit 正在启动，请查看数据目录中的 logs/desktop.log")
    children, handles = [], []
    stop = data / "stop.request"
    stop.unlink(missing_ok=True)
    owner.unlink(missing_ok=True)
    api_port = free_port()
    url = f"http://127.0.0.1:{api_port}"
    env.update(SERVER_HOST=url, CORS_ALLOW_ORIGINS=url)

    def spawn(args, logname):
        handle = (data / "logs" / logname).open("ab")
        handles.append(handle)
        process = subprocess.Popen([str(arg) for arg in args], cwd=data, env=env,
                                   stdin=subprocess.DEVNULL, stdout=handle, stderr=handle,
                                   creationflags=subprocess.CREATE_NO_WINDOW if WINDOWS else 0)
        children.append(process)
        return process

    try:
        backend = spawn([python_executable(), "-B", "-m", "uvicorn", "app.main:app",
                         "--host", "127.0.0.1", "--port", str(api_port),
                         "--ws-ping-interval", "60", "--ws-ping-timeout", "120"], "backend.log")
        for _ in range(180):
            try:
                with urllib.request.urlopen(url + "/openapi.json", timeout=1) as response:
                    if json.load(response)["info"]["title"] == "Remit":
                        break
            except Exception:
                pass
            if backend.poll() is not None:
                raise RuntimeError("工作台启动失败，请查看 logs/backend.log")
            time.sleep(0.5)
        else:
            raise RuntimeError("工作台启动超时，请查看 logs/backend.log")
        owner.write_text(json.dumps({"url": url, "pid": os.getpid()}), encoding="utf-8")
        if smoke:
            with urllib.request.urlopen(url + "/home", timeout=10) as response:
                if b"<html" not in response.read():
                    raise RuntimeError("Frontend not served by packaged backend")
            result = check_files() | {"backend_ready": True,
                                      "frontend_ready": True, "data_directory": str(data)}
            if report:
                report.write_text(json.dumps(result, indent=2), encoding="utf-8")
            print(json.dumps(result))
            return
        webbrowser.open(url + "/home")
        import pystray
        from PIL import Image
        icon = pystray.Icon("Remit", Image.open(ROOT / "assets/remit-m-icon.png"),
                            "Remit · 数模小助手", menu=pystray.Menu(
                                pystray.MenuItem("打开 Remit", lambda: webbrowser.open(url + "/home"), default=True),
                                pystray.MenuItem("打开项目文件夹", lambda: open_folder(data / "project/work_dir")),
                                pystray.MenuItem("退出 Remit", lambda: icon.stop()),
                            ))
        def watch():
            while icon.visible:
                if stop.exists() or backend.poll() is not None:
                    icon.stop()
                    return
                time.sleep(1)
        def ready(tray):
            tray.visible = True
            threading.Thread(target=watch, daemon=True).start()
        icon.run(setup=ready)
    finally:
        terminate_children(children)
        for handle in handles:
            handle.close()
        owner.unlink(missing_ok=True)
        stop.unlink(missing_ok=True)
        lock.close()


def open_folder(path):
    if WINDOWS:
        os.startfile(path)
    else:
        subprocess.Popen(["open", str(path)])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--is-running", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.check:
        print(json.dumps(check_files()))
        return
    data = data_directory()
    data.mkdir(parents=True, exist_ok=True)
    if args.is_running:
        lock = lock_instance(data)
        if lock:
            lock.close()
            raise SystemExit(1)
        return
    if args.stop:
        (data / "stop.request").touch()
        for _ in range(30):
            lock = lock_instance(data)
            if lock:
                lock.close()
                return
            time.sleep(1)
        raise RuntimeError("Remit 仍在退出，请稍后再安装或卸载")
    environment(data)
    logging.basicConfig(filename=data / "logs/desktop.log", level=logging.INFO)
    def interrupted(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    try:
        if args.smoke:
            with tempfile.TemporaryDirectory(prefix="Remit smoke ") as isolated:
                start(Path(isolated), smoke=True, report=args.report)
        else:
            start(data)
    except Exception as error:
        logging.exception("Remit startup failed")
        if args.smoke:
            raise
        message = f"Remit 暂时没能启动：{error}\n日志：{data / 'logs'}"
        if WINDOWS:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "Remit", 0x10)
        else:
            subprocess.run(["osascript", "-e", "on run argv", "-e",
                            'display alert "Remit" message (item 1 of argv)',
                            "-e", "end run", message], check=False)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
