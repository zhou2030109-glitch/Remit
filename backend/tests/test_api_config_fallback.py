import unittest
from unittest.mock import patch

from app.config.setting import ApiType, settings
from app.routers.modeling_router import (
    SaveApiConfigRequest,
    get_api_config_status,
    save_api_config,
)


class ApiConfigFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_blank_ui_values_do_not_overwrite_environment_config(self):
        blank_config = {
            "apiKey": "",
            "baseUrl": "",
            "modelId": "",
            "apiType": "",
            "contextWindow": 128000,
        }
        request = SaveApiConfigRequest(
            coordinator=blank_config,
            modeler={},
            coder={},
            writer={},
            openalex_email="",
        )

        with (
            patch.object(settings, "COORDINATOR_API_KEY", "env-key"),
            patch.object(settings, "COORDINATOR_MODEL", "gpt-5.6-sol"),
            patch.object(
                settings,
                "COORDINATOR_BASE_URL",
                "https://api.ebondai.com/",
            ),
            patch.object(
                settings,
                "COORDINATOR_API_TYPE",
                ApiType.OPENAI_RESPONSES,
            ),
        ):
            await save_api_config(request)

            self.assertEqual(settings.COORDINATOR_API_KEY, "env-key")
            self.assertEqual(settings.COORDINATOR_MODEL, "gpt-5.6-sol")
            self.assertEqual(
                settings.COORDINATOR_BASE_URL,
                "https://api.ebondai.com/",
            )
            self.assertEqual(
                settings.COORDINATOR_API_TYPE,
                ApiType.OPENAI_RESPONSES,
            )

    async def test_status_reports_configuration_without_exposing_keys(self):
        patches = []
        for prefix in ("COORDINATOR", "MODELER", "CODER", "WRITER"):
            patches.extend(
                [
                    patch.object(settings, f"{prefix}_API_KEY", "secret-key"),
                    patch.object(settings, f"{prefix}_MODEL", "gpt-5.6-sol"),
                    patch.object(
                        settings,
                        f"{prefix}_BASE_URL",
                        "https://relay.example.com/v1",
                    ),
                    patch.object(
                        settings,
                        f"{prefix}_API_TYPE",
                        ApiType.OPENAI_RESPONSES,
                    ),
                    patch.object(settings, f"{prefix}_CONTEXT_WINDOW", 200000),
                ]
            )

        for active_patch in patches:
            active_patch.start()
            self.addCleanup(active_patch.stop)

        status = await get_api_config_status()
        payload = status.model_dump()

        self.assertTrue(payload["configured"])
        self.assertTrue(
            all(agent["configured"] for agent in payload["agents"].values())
        )
        coordinator = payload["agents"]["coordinator"]
        self.assertEqual(coordinator["api_type"], "openai-responses")
        self.assertEqual(coordinator["model_id"], "gpt-5.6-sol")
        self.assertEqual(
            coordinator["base_url"], "https://relay.example.com/v1"
        )
        self.assertEqual(coordinator["context_window"], 200000)
        self.assertTrue(coordinator["api_key_configured"])
        self.assertEqual(coordinator["source"], "environment")
        self.assertNotIn("secret-key", repr(payload))


if __name__ == "__main__":
    unittest.main()
