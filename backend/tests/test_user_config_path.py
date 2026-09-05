"""用户配置可写入独立挂载目录，并被后续后端进程自动读取。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from app.config.setting import Settings


_BACKEND = Path(__file__).resolve().parents[1]
_BOOTSTRAP = """
import asyncio
import importlib.util
import json
import sys
import app.config

# 加载真实设置实现的隔离副本，使进程只访问临时目录中的测试环境文件。
spec = importlib.util.spec_from_file_location("app.config.setting", sys.argv[1])
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
app.config.setting = module
spec.loader.exec_module(module)
"""


class UserConfigPathTests(unittest.TestCase):
    def _run_process(self, setting_file: Path, env: dict, script: str) -> dict:
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                textwrap.dedent(_BOOTSTRAP + script),
                str(setting_file),
            ],
            cwd=_BACKEND,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr[-4000:])
        return json.loads(completed.stdout.strip().splitlines()[-1])

    def test_ui_save_and_new_process_loading_use_the_same_config_location(self):
        for override in (None, "", "custom"):
            with self.subTest(override=override), tempfile.TemporaryDirectory() as tmp:
                # 对齐 macOS /var 链接和 Windows 临时目录短路径的规范形式。
                root = Path(tmp).resolve()
                backend = root / "backend"
                setting_file = backend / "app" / "config" / "setting.py"
                setting_file.parent.mkdir(parents=True)
                shutil.copyfile(
                    _BACKEND / "app" / "config" / "setting.py", setting_file
                )
                # 两个基础文件均为测试值；用户保存的配置应覆盖它们。
                (backend / ".env.dev").write_text("COORDINATOR_MODEL=dev-test-model\n")
                (backend / ".env.council").write_text(
                    "COORDINATOR_MODEL=council-test-model\n"
                )
                expected_path = (
                    root / "mounted config" / "new directory" / ".env.user"
                    if override == "custom"
                    else backend / ".env.user"
                )
                env = {
                    key: value
                    for key, value in os.environ.items()
                    if key not in Settings.model_fields
                    and key != "REMIT_USER_CONFIG_PATH"
                }
                env["PYTHONIOENCODING"] = "utf-8"
                env["PYTHONDONTWRITEBYTECODE"] = "1"
                if override is not None:
                    env["REMIT_USER_CONFIG_PATH"] = (
                        str(expected_path) if override else ""
                    )

                saved = self._run_process(
                    setting_file,
                    env,
                    """
from app.routers import modeling_router
from app.routers.modeling_router import SaveApiConfigRequest, store_api_configuration
from app.config.user_config import persist_user_config
config = {
    "apiKey": "fake-persistence-key", "baseUrl": "https://example.invalid/v1",
    "modelId": "first-test-model", "apiType": "openai-chat", "contextWindow": 128000,
}
asyncio.run(store_api_configuration(SaveApiConfigRequest(
    coordinator=config, modeler=config, coder=config, writer=config, openalex_email="",
)))
# 再保存一次验证原子替换已有文件，使用真正的默认持久化路径。
module.settings.COORDINATOR_MODEL = "saved-test-model"
written = persist_user_config(module.settings)
print(json.dumps({
    "settings_path": str(module.USER_CONFIG_PATH),
    "route_path": str(modeling_router._USER_CONFIG_PATH),
    "written_path": str(written),
}))
""",
                )
                self.assertTrue(expected_path.is_file())
                self.assertFalse(expected_path.with_name(".env.user.tmp").exists())
                for path in saved.values():
                    self.assertEqual(Path(path), expected_path)
                if override == "custom":
                    self.assertFalse((backend / ".env.user").exists())

                reloaded = self._run_process(
                    setting_file,
                    env,
                    """
from app.routers import modeling_router
alternate = module.Settings.from_env("dev")
print(json.dumps({
    "settings_model": module.settings.COORDINATOR_MODEL,
    "settings_key": module.settings.COORDINATOR_API_KEY,
    "from_env_model": alternate.COORDINATOR_MODEL,
    "from_env_key": alternate.COORDINATOR_API_KEY,
    "route_path": str(modeling_router._USER_CONFIG_PATH),
}))
""",
                )
                self.assertEqual(reloaded["settings_model"], "saved-test-model")
                self.assertEqual(reloaded["from_env_model"], "saved-test-model")
                self.assertEqual(reloaded["settings_key"], "fake-persistence-key")
                self.assertEqual(reloaded["from_env_key"], "fake-persistence-key")
                self.assertEqual(Path(reloaded["route_path"]), expected_path)
