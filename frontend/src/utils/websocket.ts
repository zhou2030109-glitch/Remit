/** 收到一条服务端消息时的回调 */
type MessageHandler = (data: unknown) => void;

/** 连接状态机取值 */
type LinkStatus = "connecting" | "connected" | "disconnected" | "reconnecting";

/** 状态变化回调 */
type StatusHandler = (status: LinkStatus) => void;

const MAX_RETRIES = 10;
const INITIAL_DELAY_MS = 1_000;
const MAX_DELAY_MS = 30_000;
/** 服务端明确拒绝（非法或不存在的任务），重连无意义 */
const POLICY_REJECT_CODE = 1008;

/** 单任务的 WebSocket 通道，带指数退避自动重连 */
export class TaskWebSocket {
	private socket: WebSocket | null = null;
	private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
	private attempts = 0;
	private manualClose = false;

	constructor(
		private readonly url: string,
		private readonly onMessage: MessageHandler,
		private readonly onStatus: StatusHandler | null = null,
	) {}

	/** 建立连接（重连时复用同一入口） */
	connect(): void {
		this.manualClose = false;
		this.emit("connecting");

		this.socket = new WebSocket(this.url);
		this.socket.onopen = () => {
			this.attempts = 0;
			this.emit("connected");
		};
		this.socket.onmessage = (event) => {
			this.onMessage(JSON.parse(event.data));
		};
		this.socket.onerror = (error) => {
			console.error("WebSocket 错误:", error);
		};
		this.socket.onclose = (event) => {
			this.emit("disconnected");
			if (!this.manualClose && event.code !== POLICY_REJECT_CODE) {
				this.scheduleReconnect();
			}
		};
	}

	/** 向服务端发送 JSON 消息 */
	send(data: Record<string, unknown>): void {
		if (this.socket?.readyState === WebSocket.OPEN) {
			this.socket.send(JSON.stringify(data));
		}
	}

	/** 主动关闭并放弃重连 */
	close(): void {
		this.manualClose = true;
		this.cancelTimer();
		this.socket?.close();
		this.socket = null;
		this.emit("disconnected");
	}

	private scheduleReconnect(): void {
		if (this.attempts >= MAX_RETRIES) {
			console.error(`WebSocket 重连已达上限 ${MAX_RETRIES} 次，放弃`);
			return;
		}
		this.emit("reconnecting");
		const delay = Math.min(INITIAL_DELAY_MS * 2 ** this.attempts, MAX_DELAY_MS);
		this.reconnectTimer = setTimeout(() => {
			this.attempts += 1;
			this.connect();
		}, delay);
	}

	private cancelTimer(): void {
		if (this.reconnectTimer !== null) {
			clearTimeout(this.reconnectTimer);
			this.reconnectTimer = null;
		}
	}

	private emit(status: LinkStatus): void {
		this.onStatus?.(status);
	}
}
