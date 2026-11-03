import { mount, flushPromises, type VueWrapper } from "@vue/test-utils";
import { beforeEach, expect, it, vi } from "vitest";
import ModelBudgetRecovery from "@/pages/team/ModelBudgetRecovery.vue";
import TaskFailureNotice from "@/pages/team/TaskFailureNotice.vue";
import type { TeamState } from "@/apis/teamApi";

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("@/utils/request", () => ({ default: api }));
const snapshot = { stage_key: "a".repeat(64), used: 24, limit: 24, remaining: 0, elapsed_seconds: 900, seconds_limit: 900, remaining_seconds: 0, minimum_request_seconds: 60, reserved_calls: 4, reserved_seconds: 240, can_resume: false, phase: "review", node_id: "solve:eda", label: "数据检查" };
beforeEach(() => { vi.clearAllMocks(); api.get.mockResolvedValue({ data: snapshot }); api.post.mockResolvedValue({ data: { success: true } }); });
const create = () => mount(ModelBudgetRecovery, { props: { taskId: "fixture", nodeId: "solve:eda", sending: false }, global: { stubs: { FloatingPanel: { props: ["open"], template: '<div v-if="open"><slot /></div>' } } } });
function button(wrapper: VueWrapper, label: string) {
    const found = wrapper.findAll("button").find(x => x.text() === label);
    if (!found) throw new Error(`Missing button: ${label}`);
    return found;
}

it("先展示实际消耗和待审查位置，确认后才追加有界额度", async () => {
    const wrapper = create();
    await wrapper.get("button").trigger("click"); await flushPromises();
    expect(wrapper.text()).toContain("待审查结果");
    expect(wrapper.text()).toContain("追加 6 次调用和 600 秒");
    expect(api.post).not.toHaveBeenCalled();
    await button(wrapper, "确认追加并继续").trigger("click"); await flushPromises();
    expect(api.post).toHaveBeenCalledWith("/modeling/fixture/resume", { node_id: "solve:eda", model_budget_extension: { confirmed: true, request_id: expect.stringMatching(/^[0-9a-f]{32}$/), stage_key: snapshot.stage_key, expected_used: 24, expected_limit: 24, expected_seconds: 900, additional_calls: 6, additional_seconds: 600 } });
    expect(wrapper.emitted("resumed")).toHaveLength(1); wrapper.unmount();
});

it("额度充足只恢复，不再追加", async () => {
    api.get.mockResolvedValue({ data: { ...snapshot, can_resume: true, limit: 30, remaining: 6, seconds_limit: 1500, remaining_seconds: 600 } });
    const wrapper = create(); await wrapper.get("button").trigger("click"); await flushPromises();
    await button(wrapper, "继续当前阶段").trigger("click"); await flushPromises();
    expect(api.post).toHaveBeenCalledWith("/modeling/fixture/resume", { node_id: "solve:eda" }); wrapper.unmount();
});

it("调用次数仍有剩余但时间不足，也必须展示追加确认", async () => {
    api.get.mockResolvedValue({ data: { ...snapshot, used: 20, remaining: 4, remaining_seconds: 44 } });
    const wrapper = create(); await wrapper.get("button").trigger("click"); await flushPromises();
    expect(wrapper.text()).toContain("确认追加并继续"); expect(api.post).not.toHaveBeenCalled(); wrapper.unmount();
});

it.each([
    { label: "工作调用用完、剩余额度用于审查", used: 27, remaining: 0, reserved_calls: 4, remaining_seconds: 300, callText: "总额度还剩 3 次，须保留给结果审查", timeBlocked: false },
    { label: "总调用用完且设有审查预留", used: 30, remaining: 0, reserved_calls: 4, remaining_seconds: 300, callText: "本阶段模型调用次数已用完", timeBlocked: false },
    { label: "总调用用完且无审查预留", used: 30, remaining: 0, reserved_calls: 0, remaining_seconds: 300, callText: "本阶段模型调用次数已用完", timeBlocked: false },
    { label: "有审查预留、工作调用仍足够但时间不足", used: 20, remaining: 6, reserved_calls: 4, remaining_seconds: 44, callText: "", timeBlocked: true },
    { label: "无审查预留、时间不足", used: 20, remaining: 10, reserved_calls: 0, remaining_seconds: 44, callText: "", timeBlocked: true },
    { label: "次数与时间同时不足", used: 30, remaining: 0, reserved_calls: 0, remaining_seconds: 44, callText: "本阶段模型调用次数已用完", timeBlocked: true },
])("准确说明暂停原因：$label", async ({ label: _label, callText, timeBlocked, ...limits }) => {
    api.get.mockResolvedValue({ data: { ...snapshot, ...limits, phase: "work", limit: 30, elapsed_seconds: 556, seconds_limit: 900 } });
    const wrapper = create();
    try {
        await wrapper.get("button").trigger("click"); await flushPromises();
        if (callText) expect(wrapper.text()).toContain(callText);
        else {
            expect(wrapper.text()).not.toContain("调用次数已用完");
            expect(wrapper.text()).not.toContain("须保留给结果审查");
        }
        if (timeBlocked) expect(wrapper.text()).toContain("可用模型等待时间还剩 44 秒，发起一次新请求至少需要 60 秒");
        else expect(wrapper.text()).not.toContain("发起一次新请求至少需要");
        expect(wrapper.text()).toContain("本阶段的本地运行额度");
        expect(wrapper.text()).toContain("是否收费取决于你配置的服务");
        expect(button(wrapper, "确认追加并继续").exists()).toBe(true);
        expect(api.post).not.toHaveBeenCalled();
    } finally { wrapper.unmount(); }
});

it("审查阶段可以使用为审查保留的剩余调用，不误报工作额度用完", async () => {
    api.get.mockResolvedValue({ data: { ...snapshot, used: 27, limit: 30, remaining: 3, remaining_seconds: 300, can_resume: true } });
    const wrapper = create();
    try {
        await wrapper.get("button").trigger("click"); await flushPromises();
        expect(wrapper.text()).toContain("待审查结果");
        expect(wrapper.text()).not.toContain("调用次数已用完");
        expect(wrapper.text()).not.toContain("确认追加并继续");
        expect(button(wrapper, "继续当前阶段").exists()).toBe(true);
        expect(api.post).not.toHaveBeenCalled();
    } finally { wrapper.unmount(); }
});

it("后台取消自动重试后重新显示手动恢复，界面不自行重启任务", async () => {
    const failure = { code: "TRANSPORT", layer: "transport", reason: "模型请求中断", technical_detail: "ReadError", retrying: true, attempts_used: 1, saved_result_count: 0 };
    const state = { task_id: "fixture", status: "failed", current_node: "modeler", failure } as unknown as TeamState;
    const wrapper = mount(TaskFailureNotice, { props: { state, sending: false } });
    try {
        expect(wrapper.text()).not.toContain("重试当前步骤");
        await wrapper.setProps({ state: { ...state, failure: { ...failure, retrying: false } } });
        expect(button(wrapper, "重试当前步骤").exists()).toBe(true);
        expect(wrapper.emitted("resume")).toBeUndefined();
        await button(wrapper, "重试当前步骤").trigger("click");
        expect(wrapper.emitted("resume")).toHaveLength(1);
        expect(api.post).not.toHaveBeenCalled();
    } finally { wrapper.unmount(); }
});

it("丢失响应后使用同一确认编号，双击不重复提交", async () => {
    const wrapper = create(); await wrapper.get("button").trigger("click"); await flushPromises();
    let reject: (error: unknown) => void = () => {};
    api.post.mockImplementationOnce(() => new Promise((_, fail) => { reject = fail; }));
    const confirm = button(wrapper, "确认追加并继续");
    await confirm.trigger("click"); await confirm.trigger("click");
    expect(api.post).toHaveBeenCalledTimes(1);
    const id = api.post.mock.calls[0][1].model_budget_extension.request_id;
    reject(new Error("response lost")); await flushPromises();
    await confirm.trigger("click"); await flushPromises();
    expect(api.post.mock.calls[1][1].model_budget_extension.request_id).toBe(id); wrapper.unmount();
});

it("切换项目后丢弃旧额度响应", async () => {
    let finish: (value: unknown) => void = () => {};
    api.get.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    const wrapper = create(); await wrapper.get("button").trigger("click");
    await wrapper.setProps({ taskId: "other" }); finish({ data: snapshot }); await flushPromises();
    expect(wrapper.text()).not.toContain("确认追加"); expect(api.post).not.toHaveBeenCalled(); wrapper.unmount();
});

it.each(["failed", "stopped"])("%s 状态都显示额度恢复，不提供无效普通重试", status => {
    const state = { task_id: "fixture", status, current_node: "solve:eda", failure: { code: "MODEL_STAGE_BUDGET", reason: "预算不足" } } as unknown as TeamState;
    const wrapper = mount(TaskFailureNotice, { props: { state, sending: false } });
    expect(wrapper.text()).toContain("额度暂停"); expect(wrapper.text()).toContain("查看模型额度并继续");
    expect(wrapper.text()).not.toContain("重试当前步骤"); expect(wrapper.text()).not.toContain("修改模型配置"); wrapper.unmount();
});
