"""项目工作台关键点击行为的静态回归测试。"""

from pathlib import Path
import unittest


ROOT = Path(__file__).parents[2]


class ProjectWorkspaceInteractionTests(unittest.TestCase):
    def test_quick_analysis_calls_copilot_instead_of_only_saving_text(self) -> None:
        component = (
            ROOT / "frontend/src/pages/task/components/AICopilot.vue"
        ).read_text(encoding="utf-8")
        store = (ROOT / "frontend/src/stores/task.ts").read_text(encoding="utf-8")

        self.assertIn("taskStore.requestCopilot", component)
        self.assertNotIn("await sendMessage(action)", component)
        self.assertIn("requestCopilot", store)
        self.assertRegex(store, r'msg\.msg_type === "agent"')

    def test_project_settings_button_opens_dialog(self) -> None:
        sidebar = (
            ROOT / "frontend/src/pages/task/components/ProjectStageSidebar.vue"
        ).read_text(encoding="utf-8")
        shell = (
            ROOT / "frontend/src/pages/task/components/ProjectWorkspaceShell.vue"
        ).read_text(encoding="utf-8")

        self.assertIn("settings: []", sidebar)
        self.assertIn("@click=\"$emit('settings')\"", sidebar)
        self.assertIn('@settings="settingsOpen = true"', shell)

    def test_permanent_websocket_rejection_does_not_reconnect_forever(self) -> None:
        websocket = (ROOT / "frontend/src/utils/websocket.ts").read_text(
            encoding="utf-8"
        )

        self.assertIn("POLICY_REJECT_CODE = 1008", websocket)
        self.assertIn("event.code !== POLICY_REJECT_CODE", websocket)
        self.assertIn("this.scheduleReconnect()", websocket)


if __name__ == "__main__":
    unittest.main()
