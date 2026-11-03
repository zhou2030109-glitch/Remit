"""Use isolated Redis processes to verify persistence across source upgrades."""

import importlib.util
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import redis


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/redis_lifecycle.py"
spec = importlib.util.spec_from_file_location("redis_lifecycle", SCRIPT)
lifecycle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lifecycle)


@pytest.fixture
def isolated_redis(tmp_path):
    executable = ROOT / "tools/redis/redis-server.exe"
    if sys.platform != "win32" or not executable.exists():
        pytest.skip("bundled Windows Redis is required")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    processes = []
    with (tmp_path / "redis.log").open("ab") as log:
        def start(appendonly):
            process = subprocess.Popen(
                [str(executable), "--bind", "127.0.0.1", "--port", str(port),
                 "--appendonly", appendonly, "--save", ""],
                cwd=tmp_path, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            processes.append(process)
            client = redis.Redis(host="127.0.0.1", port=port, decode_responses=True,
                                 socket_connect_timeout=0.2, socket_timeout=1,
                                 retry=redis.retry.Retry(redis.backoff.NoBackoff(), 0))
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                try:
                    client.ping()
                    return process, client
                except (redis.ConnectionError, redis.TimeoutError):
                    if process.poll() is not None:
                        pytest.fail((tmp_path / "redis.log").read_text(errors="replace"))
                    time.sleep(0.05)
            pytest.fail("isolated Redis did not start")

        try:
            yield start, port
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)


def test_legacy_snapshot_upgrade_and_shutdown_keep_recent_state(isolated_redis):
    start, port = isolated_redis
    process, client = start("no")
    with client:
        client.set("legacy-task", "checkpoint-before-upgrade")
        client.save()
        client.shutdown(save=True)
    process.wait(timeout=10)

    process, client = start("no")
    with client:
        lifecycle.prepare(client)
        assert client.get("legacy-task") == "checkpoint-before-upgrade"
        client.set("recent-task", "checkpoint-before-exit")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "stop", "--port", str(port)],
            capture_output=True, text=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    process.wait(timeout=10)

    process, client = start("yes")
    with client:
        lifecycle.prepare(client)
        assert client.get("legacy-task") == "checkpoint-before-upgrade"
        assert client.get("recent-task") == "checkpoint-before-exit"
        assert client.config_get("appendfsync")["appendfsync"] == "everysec"
        client.shutdown(save=True)
    process.wait(timeout=10)


def test_prepare_reports_failed_persistence():
    from unittest.mock import Mock

    client = Mock()
    client.info.return_value = {"aof_enabled": 1, "aof_last_bgrewrite_status": "err"}
    with pytest.raises(RuntimeError, match="append-only file"):
        lifecycle.prepare(client)
