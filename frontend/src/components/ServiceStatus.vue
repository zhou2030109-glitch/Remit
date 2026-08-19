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

const { toast } = useToast();

/** 服务状态数据 */
const services = ref<HealthReport>({
	backend: { status: "unknown", message: "Checking..." },
	redis: { status: "unknown", message: "Checking..." },
});

let pollTimer: ReturnType<typeof setInterval> | undefined;

/** 状态对应的容器样式 */
const CONTAINER_CLASSES: Record<ServiceHealth["status"], string> = {
	running: "bg-green-100 text-green-800",
	error: "bg-red-100 text-red-800",
	unknown: "bg-gray-100 text-gray-800",
};

/** 状态对应的指示点颜色 */
const DOT_CLASSES: Record<ServiceHealth["status"], string> = {
	running: "bg-green-500",
	error: "bg-red-500",
	unknown: "bg-gray-400",
};

/** 轮询服务状态；服务由正常转为错误时弹一次提醒 */
async function pollServices(): Promise<void> {
	try {
		const response = await getServiceStatus();
		const report = response.data as HealthReport;
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
	} catch (error) {
		console.error("Failed to check service status:", error);
		toast({
			title: "状态检查失败",
			description: "无法获取服务状态，请检查网络连接",
			variant: "destructive",
		});
	}
}

onMounted(() => {
	void pollServices();
	pollTimer = setInterval(pollServices, POLL_INTERVAL_MS);
});

onUnmounted(() => {
	if (pollTimer !== undefined) {
		clearInterval(pollTimer);
	}
});
</script>

<template>
  <div class="flex items-center gap-2">
    <div
      v-for="(health, name) in services"
      :key="name"
      class="flex items-center gap-1 px-2 py-1 rounded-md text-xs"
      :class="CONTAINER_CLASSES[health.status]"
    >
      <div class="w-2 h-2 rounded-full" :class="DOT_CLASSES[health.status]"></div>
      <span class="capitalize">{{ name }}</span>
    </div>
  </div>
</template>
