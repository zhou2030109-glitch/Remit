"""Desktop navigation requires a ready API, not merely an occupied port."""

import importlib.util
import io
import threading
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest


@pytest.fixture
def desktop():
    path = Path(__file__).resolve().parents[1] / "tools/desktop_app.py"
    spec = importlib.util.spec_from_file_location("remit_readiness_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def response(body, status=200):
    result = io.BytesIO(body)
    result.status = status
    return result


@pytest.mark.parametrize("first", [
    lambda: urllib.error.HTTPError("http://local/status", 404, "missing", {}, None),
    lambda: response(b"<html>wrong server</html>"),
    lambda: response(b'{"backend":null}'),
    lambda: response(b'{"backend":{"status":"starting"}}'),
    lambda: response(b'{"backend":{"status":"running"},"redis":{"status":"error"}}'),
    lambda: response(b"", 204),
])
def test_waits_for_real_api_response(desktop, first):
    shutdown = Mock()
    shutdown.is_set.return_value = False
    owner = SimpleNamespace(shutdown_event=shutdown)
    ready = response(b'{"backend":{"status":"running"},"redis":{"status":"running"}}')
    with patch.object(desktop.urllib.request, "urlopen", side_effect=[first(), ready]) as request:
        assert desktop.DesktopApp._wait_until_ready(owner, "http://local/status", 2, backend=True)
    assert request.call_count == 2
    shutdown.wait.assert_called_once_with(0.4)


def test_exit_interrupts_readiness_retry(desktop):
    shutdown = threading.Event()
    owner = SimpleNamespace(shutdown_event=shutdown)

    def refuse(*args, **kwargs):
        shutdown.set()
        raise OSError("offline")

    with patch.object(desktop.urllib.request, "urlopen", side_effect=refuse) as request:
        assert not desktop.DesktopApp._wait_until_ready(owner, "http://local/", 120)
    request.assert_called_once()


def test_frontend_accepts_successful_page(desktop):
    owner = SimpleNamespace(shutdown_event=threading.Event())
    with patch.object(desktop.urllib.request, "urlopen", return_value=response(b"<html>Remit</html>")):
        assert desktop.DesktopApp._wait_until_ready(owner, "http://local/", 2)
