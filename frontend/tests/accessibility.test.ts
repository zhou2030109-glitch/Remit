import { readFileSync } from "node:fs";
import ServiceStatus from "@/components/ServiceStatus.vue";
import ThemeToggle from "@/components/ThemeToggle.vue";
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
	getServiceStatus: vi.fn(),
}));

vi.mock("@/apis/commonApi", () => api);

function source(relativePath: string): string {
	return readFileSync(new URL(relativePath, import.meta.url), "utf8");
}

beforeEach(() => {
	document.documentElement.classList.remove("dark");
	api.getServiceStatus.mockResolvedValue({
		data: {
			backend: { status: "running", message: "ok" },
			redis: { status: "error", message: "offline" },
		},
	});
});

afterEach(() => {
	document.documentElement.classList.remove("dark");
	vi.clearAllMocks();
});

describe("可访问状态表达", () => {
	it("服务状态使用中文文本并通过 live region 公布", async () => {
		const wrapper = mount(ServiceStatus);
		await flushPromises();

		expect(wrapper.attributes("role")).toBe("status");
		expect(wrapper.attributes("aria-live")).toBe("polite");
		expect(wrapper.text()).toContain("正常");
		expect(wrapper.text()).toContain("异常");
		wrapper.unmount();
	});

	it("主题切换暴露 switch 状态", async () => {
		const wrapper = mount(ThemeToggle);

		expect(wrapper.attributes("role")).toBe("switch");
		expect(wrapper.attributes("aria-checked")).toBe("false");
		await wrapper.trigger("click");
		expect(wrapper.attributes("aria-checked")).toBe("true");
		wrapper.unmount();
	});

	it("任务工作区提供跳转链接、主区域锚点和切换按钮状态", () => {
		const workspace = source(
			"../src/pages/task/components/ProjectWorkspaceShell.vue",
		);
		const home = source("../src/pages/home.vue");

		expect(workspace).toContain('href="#project-workspace-main"');
		expect(workspace).toContain('id="project-workspace-main"');
		expect(workspace).toContain(':aria-pressed="!showCodeAssets"');
		expect(workspace).toContain(':aria-pressed="showCodeAssets"');
		expect(home).toContain('aria-current="page"');
	});
});
