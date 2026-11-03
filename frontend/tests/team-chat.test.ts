import "./helpers/stub-floating-panels";
import * as markdown from "@/utils/markdown";
import { type TeamEvent, mergeTeamEvents } from "@/apis/teamApi";
import TeamChat from "@/pages/team/TeamChat.vue";
import ComposerSelect from "@/pages/team/ComposerSelect.vue";
import ActivitySummary from "@/pages/team/ActivitySummary.vue";
import MessageContent from "@/pages/team/MessageContent.vue";
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
	getTaskHistory: vi.fn(),
	getCompetitions: vi.fn(),
	getPaperProposals: vi.fn(),
	updateProject: vi.fn(),
	deleteProject: vi.fn(),
	uploadProjectAttachments: vi.fn(),
	route: { query: {} as Record<string, string> },
	getTeamState: vi.fn(),
	sendTeamMessage: vi.fn(),
	submitModelingTask: vi.fn(),
	push: vi.fn(),
}));
vi.mock("@/apis/teamApi", async (original) => ({
	...(await original<typeof import("@/apis/teamApi")>()),
	getTeamState: api.getTeamState,
	getProjects: api.getTaskHistory,
	prepareProject: api.submitModelingTask,
	getCompetitions: api.getCompetitions,
	getPaperProposals: api.getPaperProposals,
	updateProject: api.updateProject,
	deleteProject: api.deleteProject,
	uploadProjectAttachments: api.uploadProjectAttachments,
	sendTeamMessage: api.sendTeamMessage,
}));
vi.mock("@/apis/submitModelingApi", () => ({
	submitModelingTask: api.submitModelingTask,
	explainModelingSubmissionFailure: (cause: Error) => cause.message,
}));
vi.mock("@/pages/chat/components/ApiDialog.vue", () => ({
	default: {
		props: ["open"],
		template: '<div v-if="open" role="dialog">模型连接配置</div>',
	},
}));
vi.mock("@/components/ProblemPdfDropzone.vue", () => ({
	default: { template: "<div />" },
}));
vi.mock("@/pages/team/HumanApprovalCard.vue", () => ({
	default: {
		template:
			"<div><button @click=\"$emit('approve')\">批准当前步骤</button><button @click=\"$emit('explain')\">让 AI 解释</button></div>",
	},
}));
vi.mock("vue-router", () => ({
	useRouter: () => ({ push: api.push }),
	useRoute: () => api.route,
	onBeforeRouteLeave: vi.fn(),
	RouterLink: { props: ["to"], template: '<a :href="to"><slot /></a>' },
}));

vi.mock("@/pages/writing/PaperEditor.vue", () => ({
	default: { template: "<div>论文源码与 PDF</div>" },
}));
vi.mock("@/pages/team/ProjectFiles.vue", () => ({
	default: { template: "<div>文件与结果</div>" },
}));
class Stream {
	static latest: Stream;
	static CLOSED = 2;
	readyState = 0;
	onmessage: ((event: { data: string }) => void) | null = null;
	onerror: (() => void) | null = null;
	onopen: (() => void) | null = null;
	close = vi.fn();
	addEventListener = vi.fn();
	constructor() {
		Stream.latest = this;
	}
	update(value: unknown) {
		this.onmessage?.({ data: JSON.stringify(value) });
	}
}
const state = {
	task_id: "project-1",
	title: "预测项目",
	status: "chat",
	steps: [
		{ id: "modeler", role: "modeler", label: "总体建模", status: "running" },
	],
	pending_approval: null,
	directives: [],
	commands: [],
	writing: {},
};
const event: TeamEvent = {
	seq: 1,
	at: "2026-09-27T08:00:00Z",
	role: "coder",
	kind: "tool",
	content: "执行 Python",
	data: {
		tool_name: "execute_code",
		input: { code: "print(42)" },
		output: [{ msg: "42" }],
	},
};

describe("团队对话调度与恢复", () => {
	it("首次读取状态失败仍建立事件流，服务恢复后自动显示项目", async () => {
		api.getTeamState.mockRejectedValueOnce(new Error("服务正在启动"));
		const previous = Stream.latest;
		const wrapper = mount(TeamChat, { props: { task_id: "project-1" } });
		try {
			await flushPromises();
			expect(Stream.latest).not.toBe(previous);
			Stream.latest.update({ state, events: [{ ...event, kind: "reply", content: "连接恢复后的结果" }] });
			await flushPromises();
			expect(wrapper.text()).toContain("连接恢复后的结果");
			expect(wrapper.text()).toContain("预测项目");
			expect(wrapper.text()).not.toContain("服务正在启动");
		} finally { wrapper.unmount(); }
	});
	it("消息超时后阶段变化不改变重试正文或幂等编号", async () => {
		api.sendTeamMessage.mockRejectedValueOnce(new Error("网络中断"));
		const wrapper = mount(TeamChat, { props: { task_id: "project-1" } });
		try {
			await flushPromises();
			await wrapper.get(".composer textarea").setValue("检查计算进度");
			await wrapper.get("button[aria-label='发送']").trigger("click");
			await flushPromises();
			const first = api.sendTeamMessage.mock.calls[0][1];
			Stream.latest.update({ state: { ...state, status: "running", preflight: { id: "new-plan", understanding: "计划已更新", steps: [], questions: [], attachments: [] } }, events: [] });
			await flushPromises();
			await wrapper.get("button[aria-label='发送']").trigger("click");
			await flushPromises();
			expect(api.sendTeamMessage.mock.calls[1][1]).toEqual(first);
		} finally { wrapper.unmount(); }
	});
	it("浏览器禁止存储时仍可打开项目、切换导航和发送消息", async () => {
		const denied = () => { throw new DOMException("Storage blocked", "SecurityError"); };
		const read = vi.spyOn(Storage.prototype, "getItem").mockImplementation(denied);
		const write = vi.spyOn(Storage.prototype, "setItem").mockImplementation(denied);
		const remove = vi.spyOn(Storage.prototype, "removeItem").mockImplementation(denied);
		const wrapper = mount(TeamChat, { props: { task_id: "project-1" } });
		try {
			await flushPromises();
			await wrapper.get('button[aria-label="切换项目导航"]').trigger("click");
			await wrapper.get(".composer textarea").setValue("继续讨论");
			await wrapper.get(".composer textarea").trigger("keydown", { key: "Enter" });
			await flushPromises();
			expect(api.sendTeamMessage).toHaveBeenCalledOnce();
			expect((wrapper.get(".composer textarea").element as HTMLTextAreaElement).value).toBe("");
			expect(wrapper.text()).not.toContain("Storage blocked");
		} finally { wrapper.unmount(); read.mockRestore(); write.mockRestore(); remove.mockRestore(); }
	});
 it("仅阶段状态改变时保留历史分组，同时更新当前进度和新事件", async () => {
  const wrapper = mount(TeamChat, { props: { task_id: "project-1" } });
  try {
   await flushPromises();
   Stream.latest.update({ state: { ...state, current_node: "modeler" }, events: [event, { ...event, seq: 2, kind: "reply", content: "已保存原结果" }, { ...event, seq: 3, kind: "activity", content: "检查当前结果" }] });
   await flushPromises();
   const groups = wrapper.findAllComponents(ActivitySummary).map(c => c.props("events"));
   Stream.latest.update({ state: { ...state, status: "stopped", current_node: "modeler", steps: [{ ...state.steps[0], status: "stopped" }] }, events: [] });
   await flushPromises();
   const updated = wrapper.findAllComponents(ActivitySummary);
   expect(updated.map(c => c.props("events"))).toHaveLength(groups.length);
   updated.forEach((c, index) => expect(c.props("events")).toBe(groups[index]));
   expect(updated.at(-1)?.text()).toContain("已暂停");
   Stream.latest.update({ state, events: [{ ...event, seq: 4, kind: "reply", content: "新结果仍然可见" }] });
   await flushPromises();
   expect(wrapper.text()).toContain("已保存原结果");
   expect(wrapper.text()).toContain("新结果仍然可见");
   Stream.latest.update({ state, events: [{ ...event, seq: 2, kind: "reply", content: "同序号修订仍然可见" }] });
   await flushPromises();
   expect(wrapper.text()).toContain("同序号修订仍然可见");
   expect(wrapper.text()).not.toContain("已保存原结果");
   expect(wrapper.text()).toContain("新结果仍然可见");
  } finally { wrapper.unmount(); }
 });

 it("次数耗尽时所有恢复入口收敛到确认面板", async () => {
  const wrapper = mount(TeamChat, { props: { task_id: "project-1" } });
  try {
   await flushPromises();
   Stream.latest.update({ state: { ...state, status: "failed", current_node: "solve:ques1", failure: { code: "EXECUTION_BUDGET", reason: "阶段次数已用完", saved_result_count: 0 } }, events: [] });
   await flushPromises();
   expect(wrapper.text()).toContain("查看执行次数并继续");
   expect(wrapper.text()).toContain("阶段次数已用完");
   expect(wrapper.find('[aria-label="任务概况"]').exists()).toBe(false);
   expect(wrapper.text()).not.toContain("重试当前步骤");
   await wrapper.get('[aria-label="切换共享状态表"]').trigger("click");
   expect(wrapper.text()).not.toContain("继续建模");
   expect(api.sendTeamMessage).not.toHaveBeenCalled();
  } finally { wrapper.unmount(); }
 });

	it("消息缓存随正文和项目变化失效，重新净化并更新图片路径", async () => {
		const wrapper = mount(MessageContent, {
			props: { content: "![图](plot.png)", taskId: "project-1" },
		});
		expect(wrapper.get("img").attributes("src")).toContain(
			"/static/project-1/plot.png",
		);
		await wrapper.setProps({ taskId: "project-2" });
		expect(wrapper.get("img").attributes("src")).toContain(
			"/static/project-2/plot.png",
		);
		await wrapper.setProps({
			content: '更新 $y=2x$ <img src=x onerror="alert(1)">',
		});
		expect(wrapper.text()).toContain("更新");
		expect(wrapper.find(".katex").exists()).toBe(true);
		expect(wrapper.html()).not.toContain("onerror=");
		wrapper.unmount();
	});
	it("输入草稿不重复解析历史公式，新消息仍经过净化", async () => {
		const render = vi.spyOn(markdown, "renderMarkdown");
		const wrapper = mount(TeamChat, { props: { task_id: "project-1" } });
		try {
			await flushPromises();
			Stream.latest.update({
				state,
				events: [
					{
						...event,
						seq: 1,
						kind: "reply",
						role: "coordinator",
						content: "已核对 $y=2x+1$",
					},
				],
			});
			await flushPromises();
			const calls = render.mock.calls.length;
			expect(wrapper.find(".katex").exists()).toBe(true);
			await wrapper.get(".composer textarea").setValue("新的草稿");
			await wrapper.get(".composer textarea").setValue("新的草稿继续编辑");
			expect(render).toHaveBeenCalledTimes(calls);
			Stream.latest.update({
				state,
				events: [
					{
						...event,
						seq: 2,
						kind: "reply",
						role: "coordinator",
						content: '新结果<img src=x onerror="alert(1)">',
					},
				],
			});
			await flushPromises();
			expect(render).toHaveBeenCalledTimes(calls + 1);
			expect(wrapper.html()).not.toContain("onerror=");
			expect(wrapper.text()).toContain("新结果");
		} finally {
			wrapper.unmount();
			render.mockRestore();
		}
	});

	it("历史批次合并后保留顺序和重放更新，实时消息立即显示", async () => {
		vi.useFakeTimers();
		const wrapper = mount(TeamChat, { props: { task_id: "project-1" } });
		try {
			await vi.advanceTimersByTimeAsync(0);
			const reply = (seq: number, content: string) => ({
				...event,
				seq,
				kind: "reply",
				content,
			});
			Stream.latest.update({
				state: { ...state, sequence: 3 },
				events: [reply(2, "第二条"), reply(1, "旧正文")],
			});
			await vi.advanceTimersByTimeAsync(20);
			expect(wrapper.findAll(".event-content")).toHaveLength(0);
			Stream.latest.update({
				state: { ...state, sequence: 3 },
				events: [reply(1, "第一条更新"), reply(3, "第三条")],
			});
			await vi.advanceTimersByTimeAsync(0);
			expect(
				wrapper.findAll(".event-content").map((node) => node.text()),
			).toEqual(["第一条更新", "第二条", "第三条"]);
			Stream.latest.update({
				state: { ...state, sequence: 4 },
				events: [reply(4, "实时第四条")],
			});
			await vi.advanceTimersByTimeAsync(0);
			expect(wrapper.text()).toContain("实时第四条");
		} finally {
			wrapper.unmount();
			vi.useRealTimers();
		}
	});

	it("慢速历史最多等待50毫秒，离开后清除待交付批次", async () => {
		vi.useFakeTimers();
		const wrapper = mount(TeamChat, { props: { task_id: "project-1" } });
		try {
			await vi.advanceTimersByTimeAsync(0);
			Stream.latest.update({
				state: { ...state, sequence: 100 },
				events: [{ ...event, kind: "reply", content: "已收到的部分" }],
			});
			await vi.advanceTimersByTimeAsync(50);
			expect(wrapper.text()).toContain("已收到的部分");
			Stream.latest.update({
				state: { ...state, sequence: 100 },
				events: [{ ...event, seq: 2, kind: "reply", content: "离开前的队列" }],
			});
			wrapper.unmount();
			expect(Stream.latest.close).toHaveBeenCalledOnce();
			expect(vi.getTimerCount()).toBe(0);
			await vi.advanceTimersByTimeAsync(100);
		} finally {
			vi.useRealTimers();
		}
	});

	it("当前首页展示 Remit，设置按钮打开模型配置", async () => {
		const wrapper = mount(TeamChat);
		await flushPromises();
		expect(wrapper.get(".brand").text()).toBe("Remit");
		for (const obsolete of ["MathModelAgent", "jihe520", "mathmodel.top"]) {
			expect(wrapper.html()).not.toContain(obsolete);
		}
		expect(wrapper.find('[role="dialog"]').exists()).toBe(false);
		const settings = wrapper
			.findAll("button")
			.find((button) => button.text() === "设置");
		if (!settings) throw new Error("设置入口缺失");
		await settings.trigger("click");
		expect(wrapper.get('[role="dialog"]').text()).toContain("模型连接配置");
		wrapper.unmount();
	});
	it("快速分析请求实际发送到当前项目，不只保存草稿", async () => {
		const wrapper = mount(TeamChat, { props: { task_id: "project-1" } });
		await flushPromises();
		await wrapper
			.get(".composer textarea")
			.setValue("请快速分析当前题目的数据与约束");
		await wrapper.get('button[aria-label="发送"]').trigger("click");
		await flushPromises();
		expect(api.sendTeamMessage).toHaveBeenCalledTimes(1);
		expect(api.sendTeamMessage.mock.calls[0][0]).toBe("project-1");
		expect(api.sendTeamMessage.mock.calls[0][1]).toMatchObject({
			content: "请快速分析当前题目的数据与约束",
		});
		expect(api.sendTeamMessage.mock.calls[0][1].action).toBeUndefined();
		expect(api.submitModelingTask).not.toHaveBeenCalled();
		Stream.latest.update({
			state,
			events: [
				{
					...event,
					kind: "reply",
					role: "coordinator",
					content: "先核对输入数据的单位与边界约束。",
				},
			],
		});
		await flushPromises();
		expect(wrapper.text()).toContain("先核对输入数据的单位与边界约束。");
		wrapper.unmount();
	});
	it("服务拒绝后重新建立同步连接，不自动重启任务", async () => {
		vi.useFakeTimers();
		const wrapper = mount(TeamChat, { props: { task_id: "project-1" } });
		try {
			await vi.advanceTimersByTimeAsync(0);
			const previous = Stream.latest;
			previous.readyState = Stream.CLOSED;
			previous.onerror?.();
			await vi.advanceTimersByTimeAsync(60000);
			expect(previous.close).toHaveBeenCalledTimes(1);
			expect(Stream.latest).not.toBe(previous);
			expect(wrapper.text()).toContain("同步仍未恢复，正在继续重连");
			expect(api.sendTeamMessage).not.toHaveBeenCalled();
			expect(api.submitModelingTask).not.toHaveBeenCalled();
		} finally {
			wrapper.unmount();
			vi.useRealTimers();
		}
	});

	it("大量重复心跳不挤掉默认可见的关键进展", async () => {
		const wrapper = mount(TeamChat, { props: { task_id: "project-1" } });
		await flushPromises();
		Stream.latest.update({
			state,
			events: [
				{
					...event,
					seq: 1,
					kind: "system",
					content: "已读取 18 个数据文件，仍有 4 项待核验",
				},
				...Array.from({ length: 390 }, (_, i) => ({
					...event,
					seq: i + 2,
					kind: "activity",
					content: "协调手正在输出…",
				})),
			],
		});
		await flushPromises();
		expect(wrapper.get(".activity-group").text()).toContain(
			"已读取 18 个数据文件",
		);
		expect(wrapper.findAll(".activity-entry")).toHaveLength(0);
		expect(wrapper.text()).not.toContain("查看更早的记录");
		wrapper.unmount();
	});
	it("计划留在生成位置，后续消息和思考状态排在它之后", async () => {
		const wrapper = mount(TeamChat, { props: { task_id: "project-1" } });
		await flushPromises();
		const plan = {
			id: "plan-1",
			understanding: "运输计划",
			steps: ["计算载荷"],
			questions: [],
			attachments: [],
		};
		Stream.latest.update({
			state: { ...state, preflight: plan, commands: [{ status: "running" }] },
			events: [
				{
					...event,
					seq: 1,
					role: "coordinator",
					kind: "preflight",
					data: plan,
				},
				{
					...event,
					seq: 2,
					role: "coordinator",
					kind: "reply",
					content: "请确认",
				},
				{
					...event,
					seq: 3,
					role: "user",
					kind: "chat",
					content: "先求解第一问",
				},
			],
		});
		await flushPromises();
		const card = wrapper.get('[aria-label="赛题预读计划"]').element;
		const message = wrapper
			.findAll("article.event")
			.find((node) => node.text().includes("先求解第一问"))?.element;
		if (!message) throw new Error("缺少用户确认消息");
		expect(
			card.compareDocumentPosition(message) & Node.DOCUMENT_POSITION_FOLLOWING,
		).toBeTruthy();
		expect(
			message.compareDocumentPosition(wrapper.get(".thinking").element) &
				Node.DOCUMENT_POSITION_FOLLOWING,
		).toBeTruthy();
		const updated = { ...plan, id: "plan-2", understanding: "更新的计划" };
		Stream.latest.update({
			state: { ...state, preflight: updated },
			events: [
				{
					...event,
					seq: 4,
					role: "coordinator",
					kind: "preflight",
					data: updated,
				},
				{ ...event, seq: 5, role: "user", kind: "chat", content: "确认新计划" },
			],
		});
		await flushPromises();
		expect(wrapper.findAll('[aria-label="赛题预读计划"]')).toHaveLength(1);
		const newCard = wrapper.get('[aria-label="赛题预读计划"]').element;
		expect(
			message.compareDocumentPosition(newCard) &
				Node.DOCUMENT_POSITION_FOLLOWING,
		).toBeTruthy();
		const latest = wrapper
			.findAll("article.event")
			.find((node) => node.text().includes("确认新计划"))?.element;
		if (!latest) throw new Error("缺少新计划确认消息");
		expect(
			newCard.compareDocumentPosition(latest) &
				Node.DOCUMENT_POSITION_FOLLOWING,
		).toBeTruthy();
		wrapper.unmount();
	});
	it("关键消息直接展示，四个角色可区分，思考与工具输出收进详情", async () => {
		const wrapper = mount(TeamChat, { props: { task_id: "project-1" } });
		await flushPromises();
		Stream.latest.update({
			state,
			events: [
				...["coordinator", "modeler", "coder", "writer"].map((role, i) => ({
					...event,
					seq: i + 1,
					role,
					kind: "reply",
					content: `关键结论${i + 1}`,
				})),
				{ ...event, seq: 5, kind: "agent", content: "内部思考过程" },
				{
					...event,
					seq: 6,
					kind: "tool",
					content: "execute_code",
					data: { input: { code: "raw-debug-code" } },
				},
				{ ...event, seq: 7, kind: "error", content: "本步骤失败，请检查附件" },
			],
		});
		await flushPromises();
		const headings = wrapper.findAll(".role-heading");
		expect(headings.slice(0, 4).map((node) => node.text())).toEqual([
			"Remit",
			"建模",
			"计算",
			"论文",
		]);
		expect(
			new Set(headings.slice(0, 4).map((node) => node.get("svg").html())).size,
		).toBe(4);
		expect(wrapper.findAll("article.event")).toHaveLength(5);
		expect(wrapper.get(".event-error").text()).toContain(
			"本步骤失败，请检查附件",
		);
		expect(wrapper.text()).not.toContain("内部思考过程");
		expect(wrapper.text()).not.toContain("raw-debug-code");
		expect(wrapper.findAll(".activity-entry")).toHaveLength(0);
		wrapper.unmount();
	});
	beforeEach(() => {
		vi.clearAllMocks();
		sessionStorage.clear();
		localStorage.clear();
		vi.stubGlobal("EventSource", Stream);
		api.route.query = {};
		api.getCompetitions.mockResolvedValue({
			data: [
				{ id: "cumcm", name: "国赛", year: 2026 },
				{ id: "gmcm", name: "华为杯", year: 2026 },
			],
		});
		api.getPaperProposals.mockResolvedValue({ data: [] });
		api.getTaskHistory.mockResolvedValue({ data: [] });
		api.getTeamState.mockResolvedValue({ data: state });
		api.sendTeamMessage.mockResolvedValue({ data: { status: "running" } });
		api.deleteProject.mockResolvedValue({ data: { deleted: true } });
		api.uploadProjectAttachments.mockResolvedValue({
			data: { files: ["data.csv"] },
		});
	});
	afterEach(() => vi.unstubAllGlobals());
	it("首页提供独立工作区入口，文件目录按项目打开且不发送模型请求", async () => {
		api.route.query = { view: "files", browse: "1" };
		api.getTaskHistory.mockResolvedValue({ data: [{ task_id: "project-1", title: "预测项目", archived: false, status: "chat", updated_at: "2026-10-07T08:00:00Z" }] });
		const wrapper = mount(TeamChat);
		await flushPromises();
		expect(wrapper.get('[aria-label="工作区导航"]').text()).toContain("文件与结果");
		expect(wrapper.find(".workspace-tabs").exists()).toBe(false);
		expect(wrapper.get('[aria-label="文件与结果项目目录"] a').attributes("href")).toBe("/project/project-1?view=files");
		expect(wrapper.get(".conversation").isVisible()).toBe(false);
		expect(wrapper.get(".new-chat").classes()).not.toContain("active");
		expect(api.getTeamState).not.toHaveBeenCalled();
		expect(api.sendTeamMessage).not.toHaveBeenCalled();
		wrapper.unmount();
	});
	it("论文视图切换侧栏项目时保留板块，目录入口始终可见", async () => {
		api.route.query = { view: "paper" };
		api.getTaskHistory.mockResolvedValue({ data: [{ task_id: "project-2", title: "另一项目", archived: false, status: "chat", updated_at: "2026-10-07T08:00:00Z" }] });
		const wrapper = mount(TeamChat, { props: { task_id: "project-1" } });
		await flushPromises();
		expect(wrapper.get('.project-row a').attributes("href")).toBe("/project/project-2?view=paper");
		expect(wrapper.get('[aria-label="工作区导航"] [aria-current="page"]').text()).toBe("论文");
		expect(wrapper.get('.header-library-link').attributes("href")).toBe("/home?view=paper&browse=1");
		wrapper.unmount();
	});
	it("解释成果仅发送通俗解释请求，不附带批准动作", async () => {
		api.getTeamState.mockResolvedValue({
			data: {
				...state,
				status: "awaiting_approval",
				pending_approval: { checkpoint_id: "review-1", node_id: "modeler" },
			},
		});
		const wrapper = mount(TeamChat, { props: { task_id: "project-1" } });
		await flushPromises();
		const button = wrapper
			.findAll("button")
			.find((item) => item.text() === "让 AI 解释");
		await button?.trigger("click");
		await flushPromises();
		expect(api.sendTeamMessage).toHaveBeenCalledTimes(1);
		const body = api.sendTeamMessage.mock.calls[0][1];
		expect(body.action).toBeUndefined();
		expect(body.content).toContain("具体例子");
		expect(body.content).toContain("只解释，不批准");
		wrapper.unmount();
	});
	it("失败后由用户点击重试当前步骤，打开页面不自动重跑", async () => {
		api.getTeamState.mockResolvedValue({
			data: { ...state, status: "failed" },
		});
		const wrapper = mount(TeamChat, { props: { task_id: "project-1" } });
		await flushPromises();
		expect(api.sendTeamMessage).not.toHaveBeenCalled();
		await wrapper.get(".failure-notice button").trigger("click");
		await flushPromises();
		expect(api.sendTeamMessage.mock.calls[0][0]).toBe("project-1");
		expect(api.sendTeamMessage.mock.calls[0][1]).toMatchObject({
			action: "resume",
			content: "重试当前失败步骤",
		});
		wrapper.unmount();
	});
	it("文件选择器和拖放均可导入 XLS 与 Bookshelf 附件，过滤隐藏文件", async () => {
		const wrapper = mount(TeamChat, { props: { task_id: "project-1" } });
		await flushPromises();
		const files = ["legacy.xls", "n100.blocks", "n100.nets", "n100.pl"].map(
			(name) => new File(["fixture"], name),
		);
		const input = wrapper.get<HTMLInputElement>('[aria-label="选择数据附件"]');
		expect(input.attributes("accept")).toBeUndefined();
		Object.defineProperty(input.element, "files", { value: files });
		await input.trigger("change");
		await flushPromises();
		expect(api.uploadProjectAttachments.mock.calls[0].slice(0, 2)).toEqual([
			"project-1",
			files,
		]);
		await wrapper.get(".composer").trigger("drop", {
			dataTransfer: { files: [...files, new File(["private"], ".hidden")] },
		});
		await flushPromises();
		expect(api.uploadProjectAttachments.mock.calls[1].slice(0, 2)).toEqual([
			"project-1",
			files,
		]);
		expect(wrapper.text()).toContain("已跳过 1 个隐藏或系统文件");
		wrapper.unmount();
	});
	it("上传未结束时再次拖放给出提示，不并发上传", async () => {
		const wrapper = mount(TeamChat, { props: { task_id: "project-1" } });
		await flushPromises();
		let finish!: (value: unknown) => void;
		api.uploadProjectAttachments.mockImplementationOnce(
			() =>
				new Promise((resolve) => {
					finish = resolve;
				}),
		);
		const dataTransfer = { files: [new File(["data"], "data.csv")] };
		await wrapper.get(".composer").trigger("drop", { dataTransfer });
		await wrapper.get(".composer").trigger("drop", { dataTransfer });
		expect(api.uploadProjectAttachments).toHaveBeenCalledTimes(1);
		expect(wrapper.text()).toContain("请完成后再导入文件");
		finish({ data: { files: ["data.csv"] } });
		await flushPromises();
		wrapper.unmount();
	});
	it("普通对话没有计划时仍能导入附件和文件夹", async () => {
		const wrapper = mount(TeamChat, { props: { task_id: "project-1" } });
		await flushPromises();
		await wrapper.get('[aria-label="添加赛题或数据"]').trigger("click");
		expect(wrapper.get(".attachment-menu").text()).toContain("数据文件夹");
		const file = new File(["x\n1"], "data.csv");
		Object.defineProperty(file, "webkitRelativePath", {
			value: "数据/train/data.csv",
		});
		const input = wrapper.get("input[webkitdirectory]");
		Object.defineProperty(input.element, "files", { value: [file] });
		await input.trigger("change");
		await flushPromises();
		expect(api.uploadProjectAttachments.mock.calls[0][0]).toBe("project-1");
		expect(
			api.uploadProjectAttachments.mock.calls[0][1][0].webkitRelativePath,
		).toBe("数据/train/data.csv");
		expect(wrapper.text()).toContain("已导入 1 个文件");
		wrapper.unmount();
	});
	it("首页按文件夹汇总附件，可单独发送并保留子目录路径", async () => {
		api.submitModelingTask.mockResolvedValue({
			data: { task_id: "folder-project" },
		});
		const wrapper = mount(TeamChat);
		await flushPromises();
		const files = ["data/a.csv", "data/sub/a.csv", "data/.DS_Store"].map(
			(path) => {
				const file = new File(["x\n1"], path.split("/").at(-1) || "a.csv");
				Object.defineProperty(file, "webkitRelativePath", { value: path });
				return file;
			},
		);
		const input = wrapper.get("input[webkitdirectory]");
		Object.defineProperty(input.element, "files", { value: files });
		await input.trigger("change");
		await flushPromises();
		expect(wrapper.get(".attachment-list").text()).toContain("2 个文件");
		expect(wrapper.text()).toContain("已跳过 1 个");
		await wrapper.get('[aria-label="发送"]').trigger("click");
		await flushPromises();
		expect(
			api.submitModelingTask.mock.calls[0][1].map(
				(file: File) => file.webkitRelativePath,
			),
		).toEqual(["data/a.csv", "data/sub/a.csv"]);
		expect(api.push).toHaveBeenCalledWith("/project/folder-project");
		wrapper.unmount();
	});
	it("问候只显示回复，隐藏新旧内部同步标记并保留真实执行记录", async () => {
		const wrapper = mount(TeamChat, { props: { task_id: "project-1" } });
		await flushPromises();
		Stream.latest.update({
			state,
			events: [
				{
					...event,
					seq: 1,
					role: "coordinator",
					kind: "reply",
					content: "模型返回的问候",
				},
				{
					...event,
					seq: 2,
					kind: "system",
					event_key: "archive-imported",
					content: "历史执行记录已同步到团队对话",
				},
				{ ...event, seq: 3, kind: "internal", content: "内部同步标记" },
			],
		});
		await flushPromises();
		expect(wrapper.text()).toContain("模型返回的问候");
		expect(wrapper.find(".activity-group").exists()).toBe(false);
		Stream.latest.update({ state, events: [{ ...event, seq: 4 }] });
		await flushPromises();
		expect(wrapper.findAll(".activity-entry")).toHaveLength(0);
		expect(wrapper.text()).toContain("工具调用 1 次");
		expect(wrapper.text()).not.toContain("历史执行记录已同步");
		wrapper.unmount();
	});
	it("归档项目删除先确认，取消不请求，成功后移出列表并离开已删项目", async () => {
		api.getTaskHistory.mockResolvedValue({
			data: [{ task_id: "project-1", title: "待删除项目", archived: true }],
		});
		const wrapper = mount(TeamChat, {
			props: { task_id: "project-1" },
			attachTo: document.body,
		});
		await flushPromises();
		await wrapper.get('[aria-label="切换归档项目"]').trigger("click");
		await wrapper
			.get('button[aria-label="管理项目 待删除项目"]')
			.trigger("click");
		await wrapper.get(".project-menu .danger-text").trigger("click");
		await flushPromises();
		expect(api.deleteProject).not.toHaveBeenCalled();
		expect(wrapper.get('[role="alertdialog"]').text()).toContain("无法恢复");
		await wrapper.get('[role="alertdialog"] footer button').trigger("click");
		await flushPromises();
		expect(api.deleteProject).not.toHaveBeenCalled();
		await wrapper
			.get('button[aria-label="管理项目 待删除项目"]')
			.trigger("click");
		await wrapper.get(".project-menu .danger-text").trigger("click");
		await flushPromises();
		await wrapper.get(".delete-confirm").trigger("click");
		await flushPromises();
		expect(api.deleteProject).toHaveBeenCalledExactlyOnceWith("project-1");
		expect(
			wrapper.find('button[aria-label="管理项目 待删除项目"]').exists(),
		).toBe(false);
		expect(Stream.latest.close).toHaveBeenCalled();
		expect(api.push).toHaveBeenCalledWith("/home");
		wrapper.unmount();
	});
	it("删除失败保留项目和确认框并展示错误", async () => {
		api.getTaskHistory.mockResolvedValue({
			data: [{ task_id: "project-1", title: "保留项目", archived: true }],
		});
		api.deleteProject.mockRejectedValueOnce(new Error("文件正在使用"));
		const wrapper = mount(TeamChat, { attachTo: document.body });
		await flushPromises();
		await wrapper.get('[aria-label="切换归档项目"]').trigger("click");
		await wrapper
			.get('button[aria-label="管理项目 保留项目"]')
			.trigger("click");
		await wrapper.get(".project-menu .danger-text").trigger("click");
		await flushPromises();
		await wrapper.get(".delete-confirm").trigger("click");
		await flushPromises();
		expect(wrapper.get('[role="alert"]').text()).toBe("文件正在使用");
		expect(
			wrapper.find('button[aria-label="管理项目 保留项目"]').exists(),
		).toBe(true);
		expect(api.push).not.toHaveBeenCalled();
		wrapper.unmount();
	});
	it("运行中问进度直接发送为对话，不带重启或调整时机", async () => {
		api.getTeamState.mockResolvedValue({
			data: { ...state, status: "running" },
		});
		const wrapper = mount(TeamChat, { props: { task_id: "project-1" } });
		await flushPromises();
		await wrapper.get("textarea").setValue("现在到底进行得怎么样了");
		await wrapper.get('button[aria-label="发送"]').trigger("click");
		await flushPromises();
		expect(api.sendTeamMessage).toHaveBeenCalledTimes(1);
		expect(api.sendTeamMessage.mock.calls[0][1]).toMatchObject({
			content: "现在到底进行得怎么样了",
			conversation_only: true,
		});
		expect(api.sendTeamMessage.mock.calls[0][1].timing).toBeUndefined();
		expect(api.sendTeamMessage.mock.calls[0][1].action).toBeUndefined();
		expect(wrapper.find('[aria-label="调整任务"]').exists()).toBe(false);
		wrapper.unmount();
	});
	it("仅主动调整任务时选择时机，普通发送保持对话", async () => {
		api.getTeamState.mockResolvedValue({
			data: { ...state, status: "running" },
		});
		const wrapper = mount(TeamChat, { props: { task_id: "project-1" } });
		await flushPromises();
		await wrapper.get("textarea").setValue("换个方法试试");
		await wrapper
			.findAll("button")
			.find((button) => button.text() === "调整任务")
			?.trigger("click");
		expect(api.sendTeamMessage).not.toHaveBeenCalled();
		expect(wrapper.find('[aria-label="调整任务"]').exists()).toBe(true);
		await wrapper.findAll(".adjustment-choices > button")[1]?.trigger("click");
		await flushPromises();
		expect(api.sendTeamMessage.mock.calls[0][1]).toMatchObject({
			content: "换个方法试试",
			timing: "after_step",
		});
		wrapper.unmount();
	});
	it("助手正文支持排版，用户消息保留原文", async () => {
		const wrapper = mount(TeamChat, { props: { task_id: "project-1" } });
		await flushPromises();
		Stream.latest.update({
			state,
			events: [
				{
					...event,
					kind: "reply",
					role: "coordinator",
					content: "**模型建议**\n\n- 先检查数据\n- 再比较基线",
				},
			],
		});
		await flushPromises();
		expect(wrapper.get(".markdown-body strong").text()).toBe("模型建议");
		expect(wrapper.findAll(".markdown-body li")).toHaveLength(2);
		wrapper.unmount();
	});
	it("桌面侧栏可折叠恢复并记住选择，普通对话不挂载论文编辑器", async () => {
		const wrapper = mount(TeamChat, { props: { task_id: "project-1" } });
		await flushPromises();
		expect(wrapper.text()).not.toContain("论文源码与 PDF");
		const toggle = wrapper.get('button[aria-label="切换项目导航"]');
		await toggle.trigger("click");
		expect(wrapper.classes()).toContain("sidebar-collapsed");
		expect(toggle.attributes("aria-expanded")).toBe("false");
		wrapper.unmount();
		const reopened = mount(TeamChat);
		expect(reopened.classes()).toContain("sidebar-collapsed");
		await reopened.get('button[aria-label="切换项目导航"]').trigger("click");
		expect(reopened.classes()).not.toContain("sidebar-collapsed");
		expect(localStorage.getItem("remit-sidebar-collapsed")).toBe("false");
		reopened.unmount();
	});
	it("重连按序号去重，保留工具输入输出和后续角色记录", async () => {
		const wrapper = mount(TeamChat, { props: { task_id: "project-1" } });
		await flushPromises();
		Stream.latest.update({ state, events: [event] });
		Stream.latest.update({
			state,
			events: [
				event,
				{
					...event,
					seq: 2,
					role: "writer",
					kind: "writing",
					content: "正在写摘要",
				},
			],
		});
		await flushPromises();
		expect(wrapper.findAll(".activity-group")).toHaveLength(1);
		expect(wrapper.get(".activity-group").attributes("open")).toBeUndefined();
		await wrapper.get('.raw-log > button').trigger('click');
		await wrapper.get('.activity-entry > button').trigger('click');
		expect(wrapper.text()).toContain("print(42)");
		expect(wrapper.text()).toContain("正在写摘要");
		wrapper.unmount();
		expect(Stream.latest.close).toHaveBeenCalledOnce();
	});
	it("请求失败保留草稿和幂等编号，重试不重复派活", async () => {
		api.sendTeamMessage.mockRejectedValueOnce(new Error("网络中断"));
		const wrapper = mount(TeamChat, { props: { task_id: "project-1" } });
		await flushPromises();
		await wrapper.get("textarea").setValue("请代码手保存误差图");
		await wrapper.get("button[aria-label='发送']").trigger("click");
		await flushPromises();
		const first = api.sendTeamMessage.mock.calls[0][1];
		expect(wrapper.get("textarea").element.value).toContain("保存误差图");
		expect(wrapper.text()).toContain("网络中断");
		await wrapper.get("button[aria-label='发送']").trigger("click");
		await flushPromises();
		expect(api.sendTeamMessage.mock.calls[1][1].request_id).toBe(
			first.request_id,
		);
		expect(wrapper.get("textarea").element.value).toBe("");
		wrapper.unmount();
	});
	it("批准附带当前检查点，停止使用独立控制动作", async () => {
		api.getTeamState.mockResolvedValue({
			data: {
				...state,
				status: "running",
				pending_approval: { checkpoint_id: "checkpoint-new" },
			},
		});
		const wrapper = mount(TeamChat, { props: { task_id: "project-1" } });
		await flushPromises();
		await wrapper
			.findAll("button")
			.find((button) => button.text() === "批准当前步骤")
			?.trigger("click");
		await flushPromises();
		expect(api.sendTeamMessage.mock.calls[0][1]).toMatchObject({
			action: "approve",
			checkpoint_id: "checkpoint-new",
		});
		await wrapper.get("button[aria-label='停止建模']").trigger("click");
		await flushPromises();
		expect(api.sendTeamMessage.mock.calls[1][1].action).toBe("stop");
		wrapper.unmount();
	});
	it("首页问题进入同一个项目对话", async () => {
		api.submitModelingTask.mockResolvedValue({
			data: { task_id: "new-project" },
		});
		const wrapper = mount(TeamChat);
		await wrapper
			.get("textarea")
			.setValue("根据温度数据预测用电量，输出误差分析");
		await wrapper.get("button[aria-label='发送']").trigger("click");
		await flushPromises();
		expect(api.submitModelingTask.mock.calls[0][0].competition_id).toBe(
			"cumcm",
		);
		expect(api.submitModelingTask.mock.calls[0][0].literature_enabled).toBe(
			"true",
		);
		expect(api.submitModelingTask.mock.calls[0][0].task_purpose).toBe(
			"modeling",
		);
		expect(wrapper.find("img[alt='Remit Logo']").exists()).toBe(true);
		expect(wrapper.text()).not.toContain("workspace");
		expect(api.submitModelingTask.mock.calls[0][0].ques_all).toContain(
			"温度数据",
		);
		expect(api.push).toHaveBeenCalledWith("/project/new-project");
		wrapper.unmount();
	});
	it("菜单选择的赛事与计算环境随提交生效，环境诊断只保留一个入口", async () => {
		api.submitModelingTask.mockResolvedValue({ data: { task_id: "selected-project" } });
		const wrapper = mount(TeamChat);
		await flushPromises();
		const selectors = wrapper.findAllComponents(ComposerSelect);
		selectors[0].vm.$emit("update:modelValue", "gmcm");
		selectors[1].vm.$emit("update:modelValue", "matlab");
		await wrapper.get("textarea").setValue("分析这组数据");
		await wrapper.get('[aria-label="发送"]').trigger("click");
		await flushPromises();
		expect(api.submitModelingTask.mock.calls[0][0]).toMatchObject({ competition_id: "gmcm", execution_backend: "matlab" });
		expect(wrapper.findAll('[data-runtime-trigger]')).toHaveLength(1);
		wrapper.unmount();
	});
	it("关闭建模前文献检索会随新项目一起提交", async () => {
		api.submitModelingTask.mockResolvedValue({
			data: { task_id: "no-literature" },
		});
		const wrapper = mount(TeamChat);
		await flushPromises();
		await wrapper.get('button[aria-label="赛事设置"]').trigger("click");
		await wrapper.get('input[aria-label="建模前检索文献"]').setValue(false);
		wrapper.findAllComponents(ComposerSelect).find(component => component.props('label') === '任务目标')?.vm.$emit('update:modelValue', 'numerical_verification');
		await wrapper.get(".composer textarea").setValue("用已有方法核对数据");
		await wrapper.get('button[aria-label="发送"]').trigger("click");
		await flushPromises();
		expect(api.submitModelingTask.mock.calls[0][0].literature_enabled).toBe(
			"false",
		);
		expect(api.submitModelingTask.mock.calls[0][0].task_purpose).toBe(
			"numerical_verification",
		);
		wrapper.unmount();
	});
	it("计划确认携带当前计划版本，预读不直接执行", async () => {
		api.getTeamState.mockResolvedValue({
			data: {
				...state,
				status: "ready",
				preflight: {
					id: "plan-current",
					understanding: "数据已就绪",
					steps: ["基线比较"],
					questions: [],
					attachments: [],
				},
			},
		});
		const wrapper = mount(TeamChat, { props: { task_id: "project-1" } });
		await flushPromises();
		expect(api.sendTeamMessage).not.toHaveBeenCalled();
		await wrapper.get(".review-card .primary-button").trigger("click");
		await flushPromises();
		expect(api.sendTeamMessage.mock.calls[0][1]).toMatchObject({
			action: "start",
			plan_id: "plan-current",
		});
		wrapper.unmount();
	});
	it("论文页沿用项目导航，并可打开同一段对话", async () => {
		api.route.query = { view: "paper" };
		const wrapper = mount(TeamChat, { props: { task_id: "project-1" } });
		await flushPromises();
		expect(wrapper.text()).toContain("论文源码与 PDF");
		expect(wrapper.find('a[href="/projects"]').exists()).toBe(false);
		await wrapper.get('button[aria-label="切换项目对话"]').trigger("click");
		expect(wrapper.get(".copilot").isVisible()).toBe(true);
		wrapper.unmount();
	});
	it("乱序重放不会丢失已有事件", () => {
		expect(
			mergeTeamEvents(
				[{ ...event, seq: 3 }],
				[{ ...event, seq: 2 }, event, { ...event, seq: 3 }],
			).map((item) => item.seq),
		).toEqual([1, 2, 3]);
	});
	it("瞬时断线不提示，持续中断说明恢复方式，恢复后短暂确认", async () => {
		vi.useFakeTimers();
		try {
			const wrapper = mount(TeamChat, { props: { task_id: "project-1" } });
			await vi.advanceTimersByTimeAsync(0);
			// 瞬时断线立即恢复：用户不应看到任何中断提示
			Stream.latest.onerror?.();
			Stream.latest.onopen?.();
			await vi.advanceTimersByTimeAsync(4000);
			expect(wrapper.text()).not.toContain("自动重连");
			// 持续中断：延迟确认后才提示，并说明后台进度不受影响
			Stream.latest.onerror?.();
			await vi.advanceTimersByTimeAsync(2000);
			expect(wrapper.text()).not.toContain("自动重连");
			await vi.advanceTimersByTimeAsync(2500);
			expect(wrapper.text()).toContain("页面同步暂时断开，正在自动重连");
			expect(wrapper.text()).toContain("恢复后自动补齐消息并读取任务状态");
			// 恢复：短暂显示已重新连接，随后回到实时同步
			Stream.latest.onopen?.();
			await vi.advanceTimersByTimeAsync(0);
			expect(wrapper.text()).toContain("已重新连接");
			await vi.advanceTimersByTimeAsync(3500);
			expect(wrapper.text()).not.toContain("已重新连接");
			wrapper.unmount();
		} finally {
			vi.useRealTimers();
		}
	});
});
