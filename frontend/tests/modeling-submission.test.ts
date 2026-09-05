import { submitModelingTask } from "@/apis/submitModelingApi";
import { beforeEach, describe, expect, it, vi } from "vitest";

const http = vi.hoisted(() => ({ post: vi.fn() }));

vi.mock("@/utils/request", () => ({ default: http }));

function submittedFormData(): FormData {
	return http.post.mock.calls.at(-1)?.[1] as FormData;
}

describe("建模任务计算环境", () => {
	beforeEach(() => {
		http.post.mockReset();
		http.post.mockResolvedValue({ data: { task_id: "task-1" } });
	});

	it("显式选择 Python 时发送项目级后端", async () => {
		await submitModelingTask({
			ques_all: "建立预测模型",
			execution_backend: "python",
		});

		expect(submittedFormData().get("execution_backend")).toBe("python");
	});

	it("未选择时仍以 MATLAB 为默认值", async () => {
		await submitModelingTask({ ques_all: "建立预测模型" });

		expect(submittedFormData().get("execution_backend")).toBe("matlab");
	});
});
