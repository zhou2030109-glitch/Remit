"""Regression tests for Remit's project-specific local service ports."""

from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def read_dotenv(path: Path) -> dict[str, str]:
    config: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        config[key] = value
    return config


class RemitPortIsolationTests(unittest.TestCase):
    def test_desktop_and_launchers_use_dedicated_ports(self) -> None:
        desktop = (PROJECT_ROOT / "tools" / "desktop_app.py").read_text(
            encoding="utf-8"
        )
        start = (PROJECT_ROOT / "tools" / "start_services.ps1").read_text(
            encoding="utf-8"
        )
        stop = (PROJECT_ROOT / "tools" / "stop_services.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn('FRONTEND_URL = "http://127.0.0.1:15173/"', desktop)
        self.assertIn('BACKEND_URL = "http://127.0.0.1:18000/"', desktop)
        for script in (start, stop):
            self.assertIn("$RedisPort = 16379", script)
            self.assertIn("$BackendPort = 18000", script)
            self.assertIn("$FrontendPort = 15173", script)
        self.assertIn('"--port", "$BackendPort"', start)
        self.assertIn("--port {1} --strictPort", start)

    def test_local_environment_points_to_dedicated_ports(self) -> None:
        backend_env = read_dotenv(PROJECT_ROOT / "backend" / ".env.dev")
        frontend_env = read_dotenv(
            PROJECT_ROOT / "frontend" / ".env.development"
        )

        self.assertEqual(backend_env["REDIS_URL"], "redis://localhost:16379/0")
        self.assertEqual(backend_env["SERVER_HOST"], "http://localhost:18000")
        self.assertEqual(
            backend_env["CORS_ALLOW_ORIGINS"],
            "http://localhost:15173,http://127.0.0.1:15173",
        )
        self.assertEqual(
            frontend_env["VITE_API_BASE_URL"], "http://localhost:18000"
        )
        self.assertEqual(frontend_env["VITE_WS_URL"], "ws://localhost:18000")


if __name__ == "__main__":
    unittest.main()
