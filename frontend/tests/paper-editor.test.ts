import ComposerSelect from "@/pages/team/ComposerSelect.vue";
import "./helpers/stub-floating-panels";
import PaperEditor from "@/pages/writing/PaperEditor.vue";
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
	getPaperWorkspace: vi.fn(),
	getPaperSource: vi.fn(),
	savePaperSource: vi.fn(),
	compilePaper: vi.fn(),
	generatePaper: vi.fn(),
	syncPaper: vi.fn(),
	cancelPaper: vi.fn(),
	setPaperMain: vi.fn(),
	setPaperMode: vi.fn(),
	getPaperHistory: vi.fn(),
	writingUrl: (id: string, path = "") => `/writing/${id}${path}`,
}));
vi.mock("@/apis/writingApi", () => api);
const reviewApi = vi.hoisted(() => ({
	getPaperProposals: vi.fn(),
	decidePaperProposal: vi.fn(),
}));
vi.mock("@/apis/teamApi", () => reviewApi);
vi.mock("vue-router", () => ({
	onBeforeRouteLeave: vi.fn(),
	RouterLink: { template: "<a><slot /></a>" },
}));

describe("论文编辑器保存与编译", () => {
	beforeEach(() => {
		vi.useFakeTimers();
		vi.clearAllMocks();
		api.getPaperWorkspace.mockResolvedValue({
			data: {
				main: "main.tex",
				ready: true,
				revision: "first",
				pdf_available: true,
				files: [{ name: "main.tex", editable: true }],
				inputs: {},
				generation: { status: "idle" },
				compile: { pdf_revision: "first", page_count: 1 },
			},
		});
		api.getPaperSource.mockResolvedValue({
			data: { content: "initial", version: "first" },
		});
		api.savePaperSource.mockResolvedValue({
			data: { version: "saved", revision: "saved" },
		});
		api.compilePaper.mockResolvedValue({ data: { status: "completed" } });
	});
	afterEach(() => vi.useRealTimers());
	it("初次连接失败后轮询恢复源码，未载入时禁止编辑空白内容", async () => {
		api.getPaperWorkspace.mockRejectedValueOnce(new Error("连接中断"));
		const wrapper = mount(PaperEditor, { props: { task_id: "test" } });
		try {
			await flushPromises();
			expect(wrapper.get<HTMLTextAreaElement>(".code-input").element.disabled).toBe(true);
			await vi.advanceTimersByTimeAsync(4000);
			await flushPromises();
			expect(api.getPaperSource).toHaveBeenCalledWith("test", "main.tex");
			expect(wrapper.get<HTMLTextAreaElement>(".code-input").element.value).toBe("initial");
			expect(wrapper.text()).not.toContain("连接中断");
		} finally { wrapper.unmount(); }
	});
	it("等待源码保存时重复编译只发起一次请求", async () => {
		let finish!: (value: unknown) => void;
		api.savePaperSource.mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
		const wrapper = mount(PaperEditor, { props: { task_id: "test" } });
		try {
			await flushPromises();
			await wrapper.get(".code-input").setValue("updated");
			await wrapper.get(".compile-button").trigger("click");
			await wrapper.get(".compile-button").trigger("click");
			finish({ data: { version: "saved", revision: "saved" } });
			await flushPromises();
			expect(api.compilePaper).toHaveBeenCalledOnce();
		} finally { wrapper.unmount(); }
	});
	it("保存尚未返回时停止编译，保存返回后也不会启动编译", async () => {
		let finish!: (value: unknown) => void;
		api.savePaperSource.mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
		api.cancelPaper.mockResolvedValue({ data: { status: "cancelled" } });
		const wrapper = mount(PaperEditor, { props: { task_id: "test" } });
		try {
			await flushPromises();
			await wrapper.get(".code-input").setValue("updated");
			await wrapper.get(".compile-button").trigger("click");
			await wrapper.get('[aria-label="停止论文编译"]').trigger("click");
			await flushPromises();
			finish({ data: { version: "saved", revision: "saved" } });
			await flushPromises();
			expect(api.compilePaper).not.toHaveBeenCalled();
			expect(wrapper.get<HTMLTextAreaElement>(".code-input").element.value).toBe("updated");
		} finally { wrapper.unmount(); }
	});
	it("慢请求不堆积轮询，离开页面后不再安排刷新", async () => {
		const response = await api.getPaperWorkspace();
		api.getPaperWorkspace.mockClear();
		const wrapper = mount(PaperEditor, { props: { task_id: "test" } });
		try {
			await flushPromises();
			let finish: (value: unknown) => void = () => {};
			api.getPaperWorkspace.mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
			await vi.advanceTimersByTimeAsync(16000);
			expect(api.getPaperWorkspace).toHaveBeenCalledTimes(2);
			finish(response);
			await flushPromises();
			await vi.advanceTimersByTimeAsync(4000);
			expect(api.getPaperWorkspace).toHaveBeenCalledTimes(3);
		} finally { wrapper.unmount(); }
		await vi.advanceTimersByTimeAsync(16000);
		expect(api.getPaperWorkspace).toHaveBeenCalledTimes(3);
		expect(vi.getTimerCount()).toBe(0);
	});
	it("首次加载完成前离开，不读取源码也不遗留轮询计时器", async () => {
		const response = await api.getPaperWorkspace();
		api.getPaperWorkspace.mockClear();
		let finish: (value: unknown) => void = () => {};
		api.getPaperWorkspace.mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
		const wrapper = mount(PaperEditor, { props: { task_id: "test" } });
		wrapper.unmount();
		finish(response);
		await flushPromises();
		await vi.advanceTimersByTimeAsync(16000);
		expect(api.getPaperWorkspace).toHaveBeenCalledOnce();
		expect(api.getPaperSource).not.toHaveBeenCalled();
		expect(vi.getTimerCount()).toBe(0);
	});
	it("章节提案保留手工编辑，接受冲突后仍可拒绝", async () => {
		const workspace = (await api.getPaperWorkspace()).data;
		api.getPaperWorkspace.mockResolvedValue({
			data: {
				...workspace,
				generation: { status: "awaiting_review", proposal_id: "proposal-1" },
			},
		});
		reviewApi.getPaperProposals.mockResolvedValue({
			data: [
				{
					id: "proposal-1",
					name: "main.tex",
					summary: "局部修订",
					diff: "-old\n+new",
					status: "pending",
				},
			],
		});
		reviewApi.decidePaperProposal.mockRejectedValueOnce(
			new Error("源码已更新"),
		);
		const wrapper = mount(PaperEditor, { props: { task_id: "test" } });
		await flushPromises();
		expect(wrapper.get('[aria-label="论文修改建议"]').text()).toContain(
			"局部修订",
		);
		await wrapper.get(".code-input").setValue("manual changes");
		await wrapper.get(".review-actions .primary-button").trigger("click");
		await flushPromises();
		expect(api.savePaperSource).toHaveBeenCalled();
		expect(reviewApi.decidePaperProposal).toHaveBeenCalledWith(
			"test",
			"proposal-1",
			true,
		);
		expect(
			(wrapper.get(".code-input").element as HTMLTextAreaElement).value,
		).toBe("manual changes");
		expect(wrapper.text()).toContain("源码已更新");
		reviewApi.decidePaperProposal.mockResolvedValue({
			data: { status: "rejected" },
		});
		api.getPaperWorkspace.mockResolvedValue({ data: workspace });
		await wrapper.get(".review-actions button").trigger("click");
		await flushPromises();
		expect(reviewApi.decidePaperProposal).toHaveBeenLastCalledWith(
			"test",
			"proposal-1",
			false,
		);
		expect(wrapper.find('[aria-label="论文修改建议"]').exists()).toBe(false);
		wrapper.unmount();
	});

	it("按章返修先保存编辑，携带版本并在失败后保留意见", async () => {
		const workspace = (await api.getPaperWorkspace()).data;
		const current = {
			...workspace,
			inputs: { revision: "evidence-1" },
			generation: {
				status: "completed",
				generation_id: "generation-1",
				completed_sections: ["ques1", "firstPage", "judge"],
			},
		};
		api.getPaperWorkspace.mockResolvedValue({ data: current });
		const wrapper = mount(PaperEditor, { props: { task_id: "test" } });
		await flushPromises();
		await wrapper.get(".chapter-revision > button").trigger("click");
		await wrapper.get('input[value="ques1"]').setValue(true);
		expect(wrapper.get(".chapter-revision").text()).toContain("一并更新摘要");
		await wrapper.get('input[value="ques1"]').setValue(false);
		await wrapper.get('input[value="judge"]').setValue(true);
		await wrapper
			.get('[aria-label="本次章节返修意见"]')
			.setValue("纠正最大斜率差的名称");
		await wrapper.get("textarea.code-input").setValue("local source edit");
		api.getPaperWorkspace.mockResolvedValue({
			data: { ...current, revision: "saved" },
		});
		api.generatePaper.mockRejectedValueOnce(new Error("版本冲突"));
		await wrapper.get(".chapter-revision form").trigger("submit");
		await flushPromises();
		expect(api.savePaperSource).toHaveBeenCalledWith(
			"test",
			"main.tex",
			"local source edit",
			"first",
		);
		expect(api.generatePaper).toHaveBeenCalledWith("test", {
			sections: ["judge"],
			instructions: "纠正最大斜率差的名称",
			generation_id: "generation-1",
			input_revision: "evidence-1",
			source_revision: "saved",
		});
		expect(
			wrapper.get<HTMLTextAreaElement>('[aria-label="本次章节返修意见"]')
				.element.value,
		).toBe("纠正最大斜率差的名称");
		expect(
			wrapper.get<HTMLInputElement>('input[value="judge"]').element.checked,
		).toBe(true);
		expect(wrapper.get(".error-banner").text()).toContain("版本冲突");
		wrapper.unmount();
	});
	it("历史写作失败与当前成功编译分别显示", async () => {
		const workspace = (await api.getPaperWorkspace()).data;
		api.getPaperWorkspace.mockResolvedValue({
			data: {
				...workspace,
				generation: { status: "failed", error: "草稿已保留，排版仍需修订" },
				compile: {
					...workspace.compile,
					status: "completed",
					layout_review: { status: "passed_automatic_checks", issues: [] },
				},
			},
		});
		const wrapper = mount(PaperEditor, { props: { task_id: "test" } });
		await flushPromises();
		expect(wrapper.get(".generation-bar").text()).toContain("上次自动写作记录");
		expect(wrapper.get(".generation-bar").text()).toContain(
			"当前文稿的编译结果见 PDF 面板",
		);
		expect(wrapper.text()).toContain("编译成功");
		expect(api.generatePaper).not.toHaveBeenCalled();
		wrapper.unmount();
	});
	it("切换模式仅改变新草稿用途，旧 PDF 标签和未保存内容保持", async () => {
		const workspace = (await api.getPaperWorkspace()).data;
		api.getPaperWorkspace.mockResolvedValueOnce({
			data: {
				...workspace,
				mode: "full_paper",
				mode_version: "v1",
				compile: { ...workspace.compile, pdf_mode: "full_paper" },
			},
		});
		const wrapper = mount(PaperEditor, { props: { task_id: "test" } });
		await flushPromises();
		await wrapper.get("textarea").setValue("unsaved content");
		api.setPaperMode.mockResolvedValue({ data: {} });
		api.getPaperWorkspace.mockResolvedValue({
			data: {
				...workspace,
				mode: "short_report",
				mode_version: "v2",
				compile: { ...workspace.compile, pdf_mode: "full_paper" },
			},
		});
		wrapper.findAllComponents(ComposerSelect).find(component => component.props('label') === '新草稿模式')?.vm.$emit('update:modelValue', 'short_report');
		await flushPromises();
		expect(api.setPaperMode).toHaveBeenCalledWith("test", "short_report", "v1");
		expect(wrapper.get("textarea").element.value).toBe("unsaved content");
		expect(wrapper.text()).toContain("当前 PDF：完整论文模式");
		expect(wrapper.get(".generate-button").text()).toContain("生成短报告");
		expect(api.generatePaper).not.toHaveBeenCalled();
		wrapper.unmount();
	});
	it("停止编译不等待冲突保存，不清空源码，也不自动重启编译", async () => {
		let finish!: (value: unknown) => void;
		let stopped = false;
		api.compilePaper.mockImplementationOnce(
			() =>
				new Promise((resolve) => {
					finish = resolve;
				}),
		);
		const workspace = (await api.getPaperWorkspace()).data;
		api.getPaperWorkspace.mockImplementation(async () => ({
			data: {
				...workspace,
				compiling: !stopped,
				compile: {
					...workspace.compile,
					status: stopped ? "cancelled" : "stopping",
				},
			},
		}));
		// Initial page has an active compile from another request/window.
		api.getPaperWorkspace.mockResolvedValueOnce({
			data: { ...workspace, compiling: false },
		});
		const wrapper = mount(PaperEditor, { props: { task_id: "test" } });
		await flushPromises();
		await wrapper.get(".compile-button").trigger("click");
		await flushPromises();
		await wrapper.get("textarea").setValue("unsaved revision");
		api.savePaperSource.mockRejectedValue(new Error("保存冲突"));
		api.cancelPaper.mockResolvedValue({ data: { status: "stopping" } });
		await wrapper.get('[aria-label="停止论文编译"]').trigger("click");
		await flushPromises();
		expect(api.cancelPaper).toHaveBeenCalledWith("test");
		expect(wrapper.get('[aria-label="停止论文编译"]').text()).toContain(
			"正在停止",
		);
		expect(
			wrapper.get<HTMLButtonElement>('[aria-label="停止论文编译"]').element
				.disabled,
		).toBe(true);
		expect(wrapper.get("textarea").element.value).toBe("unsaved revision");
		stopped = true;
		finish({ data: { status: "cancelled" } });
		await flushPromises();
		await vi.advanceTimersByTimeAsync(2000);
		expect(api.compilePaper).toHaveBeenCalledTimes(1);
		expect(wrapper.text()).toContain("已停止 · 保留上次 PDF");
		expect(wrapper.find(".pdf-pages img").exists()).toBe(true);
		wrapper.unmount();
	});
	it("打开尚无 PDF 的论文页不会自动编译空模板", async () => {
		api.getPaperWorkspace.mockResolvedValue({
			data: {
				main: "main.tex",
				ready: false,
				revision: "first",
				pdf_available: false,
				files: [{ name: "main.tex", editable: true }],
				inputs: {},
				generation: { status: "idle" },
				compile: {},
			},
		});
		const wrapper = mount(PaperEditor, { props: { task_id: "test" } });
		await flushPromises();
		expect(api.compilePaper).not.toHaveBeenCalled();
		await wrapper.get(".compile-button").trigger("click");
		await flushPromises();
		expect(api.compilePaper).toHaveBeenCalledOnce();
		wrapper.unmount();
	});

	it("保存冲突保留正在编辑的内容，并阻止编译", async () => {
		const wrapper = mount(PaperEditor, { props: { task_id: "test" } });
		await flushPromises();
		api.savePaperSource.mockRejectedValue(new Error("文件已被其他窗口修改"));
		await wrapper.get("textarea").setValue("my unsaved changes");
		await vi.advanceTimersByTimeAsync(750);
		await flushPromises();
		expect(wrapper.get("textarea").element.value).toBe("my unsaved changes");
		expect(wrapper.text()).toContain("文件已被其他窗口修改");
		expect(api.compilePaper).not.toHaveBeenCalled();
		wrapper.unmount();
	});

	it("保存请求期间继续输入时，按新版本顺序保存而不丢失修改", async () => {
		let finish!: (value: unknown) => void;
		api.savePaperSource.mockReturnValueOnce(
			new Promise((resolve) => {
				finish = resolve;
			}),
		);
		const wrapper = mount(PaperEditor, { props: { task_id: "test" } });
		await flushPromises();
		await wrapper.get("textarea").setValue("first edit");
		await vi.advanceTimersByTimeAsync(750);
		await wrapper.get("textarea").setValue("newer edit");
		finish({ data: { version: "version-2", revision: "revision-2" } });
		await flushPromises();
		expect(api.savePaperSource).toHaveBeenNthCalledWith(
			1,
			"test",
			"main.tex",
			"first edit",
			"first",
		);
		expect(api.savePaperSource).toHaveBeenNthCalledWith(
			2,
			"test",
			"main.tex",
			"newer edit",
			"version-2",
		);
		expect(wrapper.get("textarea").element.value).toBe("newer edit");
		wrapper.unmount();
	});

	it("论文手生成新文件时保留当前源码", async () => {
		const wrapper = mount(PaperEditor, { props: { task_id: "test" } });
		await flushPromises();
		const generate = wrapper
			.findAll("button")
			.find((button) => button.text() === "生成论文初稿");
		await generate?.trigger("click");
		await flushPromises();
		expect(api.generatePaper).toHaveBeenCalledWith("test");
		expect(wrapper.get("textarea").element.value).toBe("initial");
		expect(api.getPaperSource).toHaveBeenCalledTimes(1);
		wrapper.unmount();
	});
});
