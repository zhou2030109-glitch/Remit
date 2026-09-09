<script setup lang="ts">
import { getServiceStatus } from "@/apis/commonApi";
import { useToast } from "@/components/ui/toast/use-toast";
import { onMounted, onUnmounted, ref } from "vue";

/** 单个服务状态 */
interface ServiceHealth {
	status: "running" | "error" | "unknown";
	message: string;
}

type ServiceName = "backend" | "redis";
type HealthReport = Record<ServiceName, ServiceHealth>;

const POLL_INTERVAL_MS = 30_000;
const FAILURE_RETRY_INTERVAL_MS = 2_000;
const FAILURES_BEFORE_ALERT = 3;

const { toast } = useToast();

/** 服务状态数据 */
const services = ref<HealthReport>({
	backend: { status: "unknown", message: "Checking..." },
	redis: { status: "unknown", message: "Checking..." },
});

let pollTimer: ReturnType<typeof setTimeout> | undefined;
let consecutiveFailures = 0;
let outageAlertShown = false;

function schedulePoll(delay: number): void {
	pollTimer = setTimeout(() => {
		void pollServices();
	}, delay);
}

function describeStatusError(error: unknown): string {
	const candidate = error as {
		code?: string;
		message?: string;
		response?: { status?: number };
	};
	if (candidate.response?.status) {
		return `本地状态接口返回 HTTP ${candidate.response.status}，Remit 将自动重试`;
	}
	if (candidate.code === "ECONNABORTED") {
		return "本地状态接口响应超时，请确认 Remit 仍在托盘运行";
	}
	return "本地后端暂时不可达，请确认 Remit 仍在托盘运行";
}

/** 状态中文标签 */
const STATUS_LABELS: Record<ServiceHealth["status"], string> = {
	running: "正常",
	error: "异常",
	unknown: "检查中",
};

const CONTAINER_CLASSES: Record<ServiceHealth["status"], string> = {
	running: "bg-[hsl(var(--success-subtle))] text-[hsl(var(--success))]",
	error: "bg-[hsl(var(--warning-subtle))] text-[hsl(var(--danger))]",
	unknown: "bg-muted text-muted-foreground",
};

/** 状态对应的指示点颜色 */
const DOT_CLASSES: Record<ServiceHealth["status"], string> = {
	running: "bg-[hsl(var(--success))]",
	error: "bg-[hsl(var(--danger))]",
	unknown: "bg-muted-foreground",
};

/** 轮询服务状态；服务由正常转为错误时弹一次提醒 */
async function pollServices(): Promise<void> {
	try {
		const response = await getServiceStatus();
		const report = response.data as HealthReport;
		const recoveredFromOutage = outageAlertShown;
		consecutiveFailures = 0;
		outageAlertShown = false;
		for (const name of Object.keys(report) as ServiceName[]) {
			const wasHealthy = services.value[name].status !== "error";
			if (report[name].status === "error" && wasHealthy) {
				toast({
					title: "服务警告",
					description: `${name.toUpperCase()} 服务连接失败: ${report[name].message}`,
					variant: "destructive",
				});
			}
		}
		services.value = report;
		if (recoveredFromOutage) {
			toast({
				title: "服务连接已恢复",
				description: "本地后端状态检查已恢复正常",
			});
		}
		schedulePoll(POLL_INTERVAL_MS);
	} catch (error) {
		console.error("Failed to check service status:", error);
		consecutiveFailures += 1;
		services.value = {
			backend: { status: "unknown", message: "Waiting for retry..." },
			redis: { status: "unknown", message: "Waiting for retry..." },
		};
		if (consecutiveFailures >= FAILURES_BEFORE_ALERT && !outageAlertShown) {
			outageAlertShown = true;
			toast({
				title: "本地服务暂时不可达",
				description: describeStatusError(error),
				variant: "destructive",
			});
		}
		schedulePoll(FAILURE_RETRY_INTERVAL_MS);
	}
}

onMounted(() => {
	void pollServices();
});

onUnmounted(() => {
	if (pollTimer !== undefined) {
		clearTimeout(pollTimer);
	}
});
</script>

<template>
  <div class="flex items-center gap-2" role="status" aria-live="polite" aria-atomic="true">
    <div
      v-for="(health, name) in services"
      :key="name"
      class="flex items-center gap-1.5 rounded-md px-2 py-1 text-xs"
      :class="CONTAINER_CLASSES[health.status]"
      :title="health.message"
      :aria-label="`${name}：${STATUS_LABELS[health.status]}。${health.message}`"
    >
      <div class="h-2 w-2 rounded-full" :class="DOT_CLASSES[health.status]" aria-hidden="true"></div>
      <span class="capitalize">{{ name }}</span>
      <span class="text-[10px] opacity-80">{{ STATUS_LABELS[health.status] }}</span>
    </div>
  </div>
</template>
