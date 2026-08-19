import request from "@/utils/request";
import type {
	ApprovalMessage,
	Message,
	TaskWorkspaceSnapshot,
} from "@/utils/response";

export interface TaskSummary {
	task_id: string;
	title: string;
	updated_at: string | number;
	status: "running" | "awaiting_approval" | "completed" | "failed" | "stopped";
	message_count: number;
}

/** 可恢复的工作流节点。 */
export interface ResumeNode {
	node_id: string;
	label: string;
	status: "completed" | "interrupted" | "available";
}

/** 停止任务的续跑状态。 */
export interface ResumeOptions {
	task_id: string;
	status: "running" | "awaiting_approval" | "stopped" | "failed" | "completed";
	resumable: boolean;
	current_node: string | null;
	nodes: ResumeNode[];
}

/** 后端持久化的当前人工审核状态。 */
export interface ApprovalStatus {
	task_id: string;
	status: string;
	pending:
		| (Omit<ApprovalMessage, "id" | "msg_type" | "content" | "options"> & {
				requested_at: string;
		  })
		| null;
}

/** 健康检查 */
export function getHelloWorld() {
	return request.get<{ message: string }>("/");
}

/** 获取论文写作顺序 */
export function getWriterSeque() {
	return request.get<{ writer_seque: string[] }>("/writer_seque");
}

/**
 * 获取任务的历史消息
 * @param task_id 任务ID
 */
export function getTaskMessages(task_id: string) {
	return request.get<Message[]>("/messages", {
		params: {
			task_id,
		},
	});
}

/** 获取主工作区逐阶段真实产物，不从聊天消息反推。 */
export function getTaskWorkspace(task_id: string) {
	return request.get<TaskWorkspaceSnapshot>(
		`/tasks/${encodeURIComponent(task_id)}/workspace`,
	);
}

/** 保存任务页中的用户补充消息。 */
export function sendTaskMessage(task_id: string, content: string) {
	return request.post<Message>(`/tasks/${task_id}/messages`, { content });
}

export type CopilotAction = "解释当前模型" | "分析当前结果" | "检查模型局限";

/** 请求主建模模型只读分析当前冻结产物，不改变工作流。 */
export function requestTaskCopilot(task_id: string, action: CopilotAction) {
	return request.post<{ request: Message; response: Message }>(
		`/tasks/${task_id}/copilot`,
		{ action },
		{ timeout: 180000 },
	);
}

/** 获取跨浏览器刷新和服务重启保留的任务历史。 */
export function getTaskHistory() {
	return request.get<TaskSummary[]>("/tasks");
}

/** 永久删除一个已结束的历史任务及其生成文件。 */
export function deleteTask(task_id: string) {
	return request.delete<{
		success: boolean;
		task_id: string;
		message: string;
	}>(`/tasks/${encodeURIComponent(task_id)}`);
}

/** 永久清空全部已结束的历史任务及其生成文件。 */
export function clearTaskHistory() {
	return request.delete<{
		success: boolean;
		deleted_count: number;
		message: string;
	}>("/tasks");
}

/**
 * 打开工作目录
 * @param task_id 任务ID
 */
export function openFolderAPI(task_id: string) {
	return request.get<{ message: string }>("/open_folder", {
		params: {
			task_id,
		},
	});
}

/**
 * 提交样例任务
 * @param example_id 样例ID
 * @param source 来源
 */
export function exampleAPI(example_id: string, source: string) {
	return request.post<{
		task_id: string;
		status: string;
	}>("/example", {
		example_id,
		source,
	});
}

/** 获取后端和 Redis 服务状态 */
export function getServiceStatus() {
	return request.get<{
		backend: { status: string; message: string };
		redis: { status: string; message: string };
	}>("/status");
}

/**
 * 取消正在运行的任务
 * @param task_id 任务ID
 */
export function cancelTask(task_id: string) {
	return request.post<{ success: boolean; message: string }>(
		`/modeling/${task_id}/cancel`,
	);
}

/** 获取停止任务当前具备完整前置成果的续跑节点。 */
export function getResumeOptions(task_id: string) {
	return request.get<ResumeOptions>(
		`/modeling/${encodeURIComponent(task_id)}/resume-options`,
	);
}

/** 从指定节点重新执行任务。 */
export function resumeTask(task_id: string, node_id: string) {
	return request.post<{
		success: boolean;
		task_id: string;
		node_id: string;
		message: string;
	}>(`/modeling/${encodeURIComponent(task_id)}/resume`, { node_id });
}

/** 获取刷新后仍有效的待人工审核节点。 */
export function getPendingApproval(task_id: string) {
	return request.get<ApprovalStatus>(
		`/modeling/${encodeURIComponent(task_id)}/approval`,
	);
}

/** 批准节点，或带具体意见退回当前节点重做。 */
export function submitApproval(
	task_id: string,
	payload: {
		checkpoint_id: string;
		decision: "approve" | "revise";
		feedback?: string;
		target_node_id?: string;
	},
) {
	return request.post<{
		success: boolean;
		task_id: string;
		decision: "approve" | "revise";
		node_id: string;
		message: string;
	}>(`/modeling/${encodeURIComponent(task_id)}/approval`, payload);
}
