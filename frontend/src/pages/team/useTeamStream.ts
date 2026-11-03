import { type TeamEvent, type TeamState, teamStreamUrl } from "@/apis/teamApi";
import { onScopeDispose, ref } from "vue";

function record(value: unknown): value is Record<string, unknown> {
	return value !== null && typeof value === "object" && !Array.isArray(value);
}

/** Reject a broken frame before it can enter reactive state or the replay buffer. */
function frame(
	value: unknown,
): value is { events: TeamEvent[]; state: TeamState } {
	if (!record(value) || !record(value.state) || !Array.isArray(value.events))
		return false;
	const state = value.state;
	return (
		typeof state.task_id === "string" &&
		typeof state.title === "string" &&
		typeof state.status === "string" &&
		Array.isArray(state.steps) &&
		state.steps.every(
			(step) =>
				record(step) &&
				typeof step.id === "string" &&
				typeof step.role === "string" &&
				typeof step.status === "string",
		) &&
		record(state.writing) &&
		(state.commands === undefined ||
			(Array.isArray(state.commands) &&
				state.commands.every(
					(command) => record(command) && typeof command.status === "string",
				))) &&
		value.events.every(
			(event) =>
				record(event) &&
				Number.isSafeInteger(event.seq) &&
				typeof event.at === "string" &&
				typeof event.role === "string" &&
				typeof event.kind === "string" &&
				typeof event.content === "string" &&
				record(event.data),
		)
	);
}

/** Own the connection, replay batching and timers in the same lifecycle. */
export function useTeamStream(
	apply: (events: TeamEvent[], state: TeamState) => void,
	deleted: () => void,
) {
	const connection = ref("连接中");
	let stream: EventSource | null = null;
	let disposed = false;
	let interrupted = false;
	let invalidFrame = false;
	let cursor = 0;
	let reconnectAttempts = 0;
	let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
	let noticeTimer: ReturnType<typeof setTimeout> | undefined;
	let flashTimer: ReturnType<typeof setTimeout> | undefined;
	let longTimer: ReturnType<typeof setTimeout> | undefined;
	let batchTimer: ReturnType<typeof setTimeout> | undefined;
	let pendingState: TeamState | null = null;
	const pendingEvents = new Map<number, TeamEvent>();

	function clearConnectionTimers() {
		clearTimeout(noticeTimer);
		clearTimeout(flashTimer);
		clearTimeout(longTimer);
		noticeTimer = flashTimer = longTimer = undefined;
	}
	function close() {
		clearConnectionTimers();
		clearTimeout(reconnectTimer);
		reconnectTimer = undefined;
		clearTimeout(batchTimer);
		batchTimer = undefined;
		pendingState = null;
		pendingEvents.clear();
		const previous = stream;
		stream = null;
		previous?.close();
	}
	function flush() {
		clearTimeout(batchTimer);
		batchTimer = undefined;
		const state = pendingState;
		const events = [...pendingEvents.values()];
		pendingEvents.clear();
		pendingState = null;
		if (!disposed && stream && state) {
			apply(events, state);
			for (const event of events) cursor = Math.max(cursor, event.seq);
		}
	}
	function open(taskId: string) {
		close();
		if (disposed) return;
		interrupted = invalidFrame = false;
		cursor = reconnectAttempts = 0;
		connection.value = "连接中";
		connect(taskId);
	}
	function connect(taskId: string) {
		const current = new EventSource(teamStreamUrl(taskId, cursor));
		stream = current;
		const active = () => !disposed && stream === current;
		current.addEventListener("deleted", () => {
			if (!active()) return;
			close();
			deleted();
		});
		current.onopen = () => {
			if (!active()) return;
			clearConnectionTimers();
			reconnectAttempts = 0;
			connection.value = interrupted ? "已重新连接" : "实时同步";
			if (interrupted)
				flashTimer = setTimeout(() => {
					connection.value = "实时同步";
				}, 3000);
			interrupted = false;
		};
		current.onerror = () => {
			if (!active()) return;
			clearTimeout(flashTimer);
			if (current.readyState === EventSource.CLOSED) {
				flush();
				stream = null;
				current.close();
				reconnectTimer = setTimeout(() => {
					reconnectTimer = undefined;
					if (!disposed) connect(taskId);
				}, Math.min(1000 * 2 ** reconnectAttempts++, 30000));
			}
			// Repeated native retries must not postpone the first disconnect notice.
			if (noticeTimer !== undefined || interrupted) return;
			noticeTimer = setTimeout(() => {
				noticeTimer = undefined;
				interrupted = true;
				connection.value =
					"页面同步暂时断开，正在自动重连；恢复后自动补齐消息并读取任务状态";
				longTimer = setTimeout(() => {
					connection.value =
						"同步仍未恢复，正在继续重连；请确认项目仍存在、本地服务正在运行";
				}, 30000);
			}, 4000);
		};
		current.onmessage = (message) => {
			if (!active()) return;
			try {
				const data: unknown = JSON.parse(message.data);
				if (!frame(data) || data.state.task_id !== taskId)
					throw new Error("Invalid stream frame");
				if (invalidFrame) {
					invalidFrame = false;
					connection.value = "实时同步";
				}
				for (const event of data.events) pendingEvents.set(event.seq, event);
				pendingState = data.state;
				let latest = 0;
				for (const seq of pendingEvents.keys()) latest = Math.max(latest, seq);
				if (
					!data.events.length ||
					!Number.isFinite(data.state.sequence) ||
					latest >= data.state.sequence
				)
					flush();
				else if (batchTimer === undefined) batchTimer = setTimeout(flush, 50);
			} catch {
				invalidFrame = true;
				connection.value = "同步数据异常，请刷新";
			}
		};
	}
	onScopeDispose(() => {
		disposed = true;
		close();
	});
	return { connection, open, close };
}
