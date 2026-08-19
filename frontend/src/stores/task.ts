import {
	type TaskSummary,
	cancelTask as cancelTaskAPI,
	clearTaskHistory as clearTaskHistoryAPI,
	deleteTask as deleteTaskAPI,
	getPendingApproval,
	getTaskHistory,
	getTaskMessages,
	getTaskWorkspace,
	requestTaskCopilot,
	resumeTask as resumeTaskAPI,
	sendTaskMessage as sendTaskMessageAPI,
	submitApproval as submitApprovalAPI,
} from "@/apis/commonApi";
import type { CopilotAction } from "@/apis/commonApi";
import { AgentType } from "@/utils/enum";
import type {
	ActivityMessage,
	ApprovalMessage,
	CoderMessage,
	CoordinatorMessage,
	ExecutionSummaryMessage,
	InterpreterMessage,
	Message,
	ModelerMessage,
	ProgressMessage,
	TaskWorkspaceSnapshot,
	WriterMessage,
} from "@/utils/response";
import { TaskWebSocket } from "@/utils/websocket";
import { defineStore } from "pinia";
import { computed, ref } from "vue";

type ConnectionState =
	| "connecting"
	| "connected"
	| "disconnected"
	| "reconnecting";

/** 后端目前会推送的消息大类 */
const KNOWN_MSG_TYPES = new Set([
	"system",
	"agent",
	"user",
	"tool",
	"approval",
	"execution_summary",
	"progress",
	"activity",
]);

/** 当前任务的持久化键（用于刷新后恢复选中态） */
const CURRENT_TASK_STORAGE_KEY = "currentTaskId";

/** 类型守卫：判断是否为有效的消息对象 */
function isMessagePayload(payload: unknown): payload is Message {
	if (payload === null || typeof payload !== "object") {
		return false;
	}
	const id = Reflect.get(payload, "id");
	const msgType = Reflect.get(payload, "msg_type");
	return (
		typeof id === "string" &&
		typeof msgType === "string" &&
		KNOWN_MSG_TYPES.has(msgType)
	);
}

function isApprovalMessage(message: Message): message is ApprovalMessage {
	return message.msg_type === "approval";
}

/** 只有整个任务的完成/停止/失败消息才是终态。 */
function isTerminalTaskMessage(message: Message): boolean {
	if (message.msg_type !== "system") {
		return false;
	}
	const content = message.content ?? "";
	if (message.type === "success") {
		return content === "任务处理完成";
	}
	if (message.type === "error") {
		return content.startsWith("任务执行失败");
	}
	if (message.type === "warning") {
		return content.includes("任务已停止") || content.includes("服务重启");
	}
	return false;
}

function isTaskStartMessage(message: Message): boolean {
	if (message.msg_type !== "system") {
		return false;
	}
	const content = message.content ?? "";
	return (
		content === "任务开始处理" ||
		content.startsWith("任务从节点 ") ||
		content.startsWith("任务继续处理")
	);
}

/** 解析消息时间戳；缺失或非法时返回 null */
function parseTimestamp(message: Message): number | null {
	if (!message.created_at) {
		return null;
	}
	const ts = Date.parse(message.created_at);
	return Number.isNaN(ts) ? null : ts;
}

/** 按时间戳升序排列；无法解析时间戳的消息保持相对顺序 */
function orderByTimestamp(items: Message[]): Message[] {
	return [...items].sort((a, b) => {
		const ta = parseTimestamp(a);
		const tb = parseTimestamp(b);
		if (ta === null || tb === null) {
			return 0;
		}
		return ta - tb;
	});
}

/** 播放短促提示音；失败时静默降级 */
function playAttentionSound(): void {
	try {
		const Ctor =
			window.AudioContext ??
			(window as unknown as { webkitAudioContext?: typeof AudioContext })
				.webkitAudioContext;
		if (!Ctor) {
			return;
		}
		const ctx = new Ctor();
		const osc = ctx.createOscillator();
		const gain = ctx.createGain();
		osc.frequency.value = 880;
		gain.gain.setValueAtTime(0.08, ctx.currentTime);
		gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.5);
		osc.connect(gain);
		gain.connect(ctx.destination);
		osc.onended = () => void ctx.close();
		osc.start();
		osc.stop(ctx.currentTime + 0.5);
	} catch {
		// 声音提示失败不影响功能
	}
}

/** 需要人工出手时主动提醒：声音 + 桌面通知。 */
function notifyUser(title: string, body: string): void {
	playAttentionSound();
	try {
		if (typeof Notification === "undefined") {
			return;
		}
		if (Notification.permission === "granted") {
			new Notification(title, { body: body.slice(0, 120) });
		}
	} catch {
		// 桌面通知失败不影响功能
	}
}

/** 任务管理 Store */
export const useTaskStore = defineStore("task", () => {
	// ---- State ----

	/** 按任务ID分组的消息记录 */
	const messagesByTask = ref<Record<string, Message[]>>({});

	/** 当前活跃的任务ID */
	const currentTaskId = ref<string | null>(null);

	/** 后端持久化的任务历史索引 */
	const taskHistory = ref<TaskSummary[]>([]);

	/** 已处理的消息ID集合（用于去重） */
	const seenMessageIdsByTask = new Map<string, Set<string>>();

	/** WebSocket 实例 */
	let socket: TaskWebSocket | null = null;

	/** WebSocket 连接状态 */
	const wsStatus = ref<ConnectionState>("disconnected");

	/** 任务是否正在运行 */
	const isRunning = ref(false);

	/** 每个任务当前唯一有效的待审核节点。 */
	const pendingApprovalsByTask = ref<Record<string, ApprovalMessage | null>>(
		{},
	);

	/** 主页面直接消费的工作流产物快照，不能从 Copilot 消息反推。 */
	const workspaceSnapshotsByTask = ref<
		Record<string, TaskWorkspaceSnapshot | null>
	>({});

	/** 候选否决等操作预填的退回意见草稿，打开返修对话框时消费。 */
	const reviseDraft = ref("");

	// ---- 基础派生态 ----

	/** 当前任务的消息列表 */
	const messages = computed<Message[]>(() =>
		currentTaskId.value
			? (messagesByTask.value[currentTaskId.value] ?? [])
			: [],
	);

	const pendingApproval = computed<ApprovalMessage | null>(() =>
		currentTaskId.value
			? (pendingApprovalsByTask.value[currentTaskId.value] ?? null)
			: null,
	);

	const workspaceSnapshot = computed<TaskWorkspaceSnapshot | null>(() =>
		currentTaskId.value
			? (workspaceSnapshotsByTask.value[currentTaskId.value] ?? null)
			: null,
	);

	// ---- 消息桶维护 ----

	/** 设置当前活跃任务 */
	function setCurrentTask(taskId: string): void {
		currentTaskId.value = taskId;
		if (typeof window !== "undefined") {
			window.localStorage.setItem(CURRENT_TASK_STORAGE_KEY, taskId);
		}
	}

	function clearCurrentTaskSelection(): void {
		currentTaskId.value = null;
		if (
			typeof window !== "undefined" &&
			window.localStorage.getItem(CURRENT_TASK_STORAGE_KEY)
		) {
			window.localStorage.removeItem(CURRENT_TASK_STORAGE_KEY);
		}
	}

	/** 确保任务的消息桶存在 */
	function ensureTaskBucket(taskId: string): void {
		if (!messagesByTask.value[taskId]) {
			messagesByTask.value[taskId] = [];
		}
		if (!seenMessageIdsByTask.has(taskId)) {
			seenMessageIdsByTask.set(taskId, new Set());
		}
	}

	/** 追加消息；同 ID 视为更新（WebSocket 回显与 REST 返回可能重复） */
	function appendMessage(taskId: string, message: Message): void {
		ensureTaskBucket(taskId);
		const bucket = messagesByTask.value[taskId];
		const seen = seenMessageIdsByTask.get(taskId);

		if (message.id && seen?.has(message.id)) {
			const index = bucket.findIndex((existing) => existing.id === message.id);
			if (index >= 0) {
				bucket[index] = message;
			}
			messagesByTask.value[taskId] = orderByTimestamp(bucket);
			return;
		}
		if (message.id) {
			seen?.add(message.id);
		}
		messagesByTask.value[taskId] = orderByTimestamp([...bucket, message]);
	}

	/** 合并历史消息（用于加载历史记录） */
	function mergeMessages(taskId: string, incoming: Message[]): void {
		ensureTaskBucket(taskId);
		const byId = new Map<string, Message>();
		for (const message of [...messagesByTask.value[taskId], ...incoming]) {
			if (message.id) {
				byId.set(message.id, message);
			}
		}
		const merged = orderByTimestamp(Array.from(byId.values()));
		messagesByTask.value[taskId] = merged;
		seenMessageIdsByTask.set(taskId, new Set(merged.map((m) => m.id)));
	}

	/** 依据当前消息流重算“是否运行中”（从尾部向前找最近的状态事件） */
	function syncRunningState(taskId: string): void {
		if (currentTaskId.value !== taskId) {
			return;
		}
		const bucket = messagesByTask.value[taskId] ?? [];
		const lastSignal = [...bucket]
			.reverse()
			.find(
				(m) =>
					isApprovalMessage(m) ||
					isTerminalTaskMessage(m) ||
					isTaskStartMessage(m),
			);
		isRunning.value = lastSignal ? isTaskStartMessage(lastSignal) : false;
	}

	// ---- 实时通道 ----

	/** 连接 WebSocket 接收实时消息 */
	function connectWebSocket(taskId: string): void {
		closeWebSocket();
		setCurrentTask(taskId);
		ensureTaskBucket(taskId);

		const url = `${import.meta.env.VITE_WS_URL}/task/${taskId}`;

		const handlePayload = (data: unknown) => {
			if (!isMessagePayload(data)) {
				console.warn("忽略非标准任务消息:", data);
				return;
			}
			appendMessage(taskId, data);
			if (data.msg_type === "progress" || data.msg_type === "approval") {
				void loadTaskWorkspace(taskId);
			}
			if (isApprovalMessage(data)) {
				pendingApprovalsByTask.value[taskId] = data;
				isRunning.value = false;
				notifyUser(`等待你的审核：${data.node_label}`, data.summary);
				void loadTaskHistory();
				return;
			}
			if (isTerminalTaskMessage(data)) {
				pendingApprovalsByTask.value[taskId] = null;
				isRunning.value = false;
				if (data.msg_type === "system" && data.type === "error") {
					notifyUser("任务需要你处理", data.content ?? "任务执行失败");
				}
				void loadTaskHistory();
				return;
			}
			if (isTaskStartMessage(data)) {
				// 任务重新跑起来说明审批已被处理（可能在别处批准），清掉残留横幅
				pendingApprovalsByTask.value[taskId] = null;
				isRunning.value = true;
				void loadTaskHistory();
			}
		};

		socket = new TaskWebSocket(url, handlePayload, (status) => {
			wsStatus.value = status;
		});
		socket.connect();
	}

	/** 关闭 WebSocket 连接 */
	function closeWebSocket(): void {
		if (socket) {
			socket.close();
			socket = null;
		}
	}

	// ---- 数据加载 ----

	/** 加载任务的历史消息 */
	async function loadTaskMessages(taskId: string): Promise<void> {
		setCurrentTask(taskId);
		ensureTaskBucket(taskId);
		try {
			const response = await getTaskMessages(taskId);
			mergeMessages(taskId, (response.data ?? []).filter(isMessagePayload));
			syncRunningState(taskId);
			await Promise.all([
				loadPendingApproval(taskId),
				loadTaskWorkspace(taskId),
			]);
		} catch (error) {
			console.error("加载任务历史消息失败:", error);
		}
	}

	/** 加载每个阶段的冻结产物和真实完成状态。 */
	async function loadTaskWorkspace(taskId: string) {
		try {
			const response = await getTaskWorkspace(taskId);
			workspaceSnapshotsByTask.value[taskId] = response.data;
			return response.data;
		} catch (error) {
			console.error("加载项目阶段产物失败:", error);
			workspaceSnapshotsByTask.value[taskId] = null;
			return null;
		}
	}

	/** 从检查点恢复当前待审核节点，不能只依赖实时消息。 */
	async function loadPendingApproval(taskId: string): Promise<void> {
		try {
			const response = await getPendingApproval(taskId);
			const pending = response.data.pending;
			pendingApprovalsByTask.value[taskId] = pending
				? {
						...pending,
						id: pending.checkpoint_id,
						msg_type: "approval",
						content: `“${pending.node_label}”已生成结果，等待你的审核`,
						options: ["approve", "revise"],
					}
				: null;
			if (currentTaskId.value === taskId && pending) {
				isRunning.value = false;
			}
		} catch (error) {
			console.error("加载人工审核状态失败:", error);
		}
	}

	/** 加载可跨刷新、跨后端重启恢复的任务列表 */
	async function loadTaskHistory(): Promise<void> {
		try {
			const response = await getTaskHistory();
			taskHistory.value = response.data ?? [];
		} catch (error) {
			console.error("加载任务历史失败:", error);
		}
	}

	// ---- 任务生命周期操作 ----

	/** 永久删除历史任务，并同步清理本地缓存。 */
	async function deleteTask(taskId: string) {
		const response = await deleteTaskAPI(taskId);
		taskHistory.value = taskHistory.value.filter((t) => t.task_id !== taskId);
		delete messagesByTask.value[taskId];
		delete pendingApprovalsByTask.value[taskId];
		delete workspaceSnapshotsByTask.value[taskId];
		seenMessageIdsByTask.delete(taskId);

		if (currentTaskId.value === taskId) {
			closeWebSocket();
			isRunning.value = false;
			clearCurrentTaskSelection();
		}
		return response.data;
	}

	/** 永久清空全部历史任务，并同步清理当前选择和本地缓存。 */
	async function clearTaskHistory() {
		const response = await clearTaskHistoryAPI();
		closeWebSocket();
		taskHistory.value = [];
		messagesByTask.value = {};
		pendingApprovalsByTask.value = {};
		workspaceSnapshotsByTask.value = {};
		seenMessageIdsByTask.clear();
		isRunning.value = false;
		clearCurrentTaskSelection();
		return response.data;
	}

	/** 保存用户补充消息；成功后立即合并，WebSocket 回显会按 ID 去重。 */
	async function sendUserMessage(taskId: string, content: string) {
		const text = content.trim();
		if (!text) {
			return null;
		}
		const response = await sendTaskMessageAPI(taskId, text);
		if (isMessagePayload(response.data)) {
			appendMessage(taskId, response.data);
		}
		await loadTaskHistory();
		return response.data;
	}

	/** 主动请求建模手解释冻结证据；与普通“补充消息”语义分离。 */
	async function requestCopilot(taskId: string, action: CopilotAction) {
		const response = await requestTaskCopilot(taskId, action);
		for (const payload of [response.data.request, response.data.response]) {
			if (isMessagePayload(payload)) {
				appendMessage(taskId, payload);
			}
		}
		await loadTaskHistory();
		return response.data;
	}

	/** 取消正在运行的任务 */
	async function stopTask(taskId: string) {
		try {
			const res = await cancelTaskAPI(taskId);
			if (res.data.success) {
				isRunning.value = false;
				await loadTaskHistory();
			} else {
				await loadTaskMessages(taskId);
			}
			return res.data;
		} catch (error) {
			console.error("取消任务失败:", error);
			return { success: false, message: "取消请求失败" };
		}
	}

	/** 从用户选择的持久化节点继续运行原任务。 */
	async function resumeTask(taskId: string, nodeId: string) {
		const response = await resumeTaskAPI(taskId, nodeId);
		if (response.data.success) {
			markTaskRunning(taskId);
		}
		return response.data;
	}

	/** 提交人工审核决定；只有后端成功持久化后才移除审阅台。 */
	async function decideApproval(
		taskId: string,
		decision: "approve" | "revise",
		feedback = "",
		targetNodeId?: string,
	) {
		const approval = pendingApprovalsByTask.value[taskId];
		if (!approval) {
			throw new Error("当前没有待审核节点");
		}
		const response = await submitApprovalAPI(taskId, {
			checkpoint_id: approval.checkpoint_id,
			decision,
			feedback,
			target_node_id: targetNodeId,
		});
		pendingApprovalsByTask.value[taskId] = null;
		markTaskRunning(taskId);
		return response.data;
	}

	/** 任务进入运行态后的统一收尾：本地置位、历史刷新、重连实时通道 */
	function markTaskRunning(taskId: string): void {
		setCurrentTask(taskId);
		isRunning.value = true;
		taskHistory.value = taskHistory.value.map((t) =>
			t.task_id === taskId ? { ...t, status: "running" } : t,
		);
		connectWebSocket(taskId);
		void loadTaskHistory();
	}

	/** 下载消息为 JSON 文件 */
	function downloadMessages(): void {
		const href = `data:text/json;charset=utf-8,${encodeURIComponent(JSON.stringify(messages.value, null, 2))}`;
		const anchor = document.createElement("a");
		anchor.href = href;
		anchor.download = `${currentTaskId.value ?? "task"}-messages.json`;
		document.body.appendChild(anchor);
		anchor.click();
		anchor.remove();
	}

	// ---- 展示层派生态 ----

	/** Copilot 时间线展示所有 Agent 回复、用户补充和系统事件。 */
	const chatMessages = computed(() =>
		messages.value.filter((msg) => {
			switch (msg.msg_type) {
				case "agent":
					return msg.content != null && msg.content !== "";
				case "user":
				case "system":
					return true;
				default:
					return false;
			}
		}),
	);

	/** 按角色过滤 Agent 消息 */
	function agentMessagesOf<T extends Message>(agentType: AgentType) {
		return computed(() =>
			messages.value.filter(
				(msg): msg is T =>
					msg.msg_type === "agent" &&
					msg.agent_type === agentType &&
					msg.content != null,
			),
		);
	}

	/** 协调者消息列表 */
	const coordinatorMessages = agentMessagesOf<CoordinatorMessage>(
		AgentType.COORDINATOR,
	);
	/** 建模者消息列表 */
	const modelerMessages = agentMessagesOf<ModelerMessage>(AgentType.MODELER);
	/** 代码手消息列表 */
	const coderMessages = agentMessagesOf<CoderMessage>(AgentType.CODER);
	/** 论文手消息列表 */
	const writerMessages = agentMessagesOf<WriterMessage>(AgentType.WRITER);

	/** 代码执行工具消息列表 */
	const interpreterMessage = computed(() =>
		messages.value.filter(
			(msg): msg is InterpreterMessage =>
				msg.msg_type === "tool" &&
				"tool_name" in msg &&
				msg.tool_name === "execute_code",
		),
	);

	/** 每个求解节点的紧凑运行记录，不再从代码输出文本中猜测结论。 */
	const executionSummaries = computed(() =>
		messages.value.filter(
			(msg): msg is ExecutionSummaryMessage =>
				msg.msg_type === "execution_summary",
		),
	);

	/** 从尾部取最近一条满足条件的消息 */
	function lastMessageOfType<T extends Message>(msgType: string) {
		return computed<T | null>(() => {
			const hit = [...messages.value]
				.reverse()
				.find((m) => m.msg_type === msgType);
			return (hit as T | undefined) ?? null;
		});
	}

	/** 最新的工作流进度快照（后端固定 id 推送，取最后一条即可）。 */
	const latestProgress = computed<ProgressMessage | null>(
		() => workspaceSnapshot.value?.progress ?? lastProgressMessage.value,
	);
	const lastProgressMessage = lastMessageOfType<ProgressMessage>("progress");

	/** 最新的实时活动播报（不落盘，只在任务运行时出现）。 */
	const latestActivity = lastMessageOfType<ActivityMessage>("activity");

	/** 从最新代码手消息中提取文件列表 */
	const files = computed<string[]>(() => {
		const hit = [...coderMessages.value]
			.reverse()
			.find(
				(msg) =>
					"files" in msg && Array.isArray(msg.files) && msg.files.length > 0,
			);
		if (hit && "files" in hit && Array.isArray(hit.files)) {
			return hit.files as string[];
		}
		return [];
	});

	return {
		messages,
		taskHistory,
		wsStatus,
		isRunning,
		pendingApproval,
		workspaceSnapshot,
		chatMessages,
		coordinatorMessages,
		modelerMessages,
		coderMessages,
		writerMessages,
		interpreterMessage,
		executionSummaries,
		latestProgress,
		latestActivity,
		reviseDraft,
		files,
		loadTaskMessages,
		loadPendingApproval,
		loadTaskWorkspace,
		loadTaskHistory,
		deleteTask,
		clearTaskHistory,
		sendUserMessage,
		requestCopilot,
		connectWebSocket,
		closeWebSocket,
		stopTask,
		resumeTask,
		decideApproval,
		downloadMessages,
	};
});
