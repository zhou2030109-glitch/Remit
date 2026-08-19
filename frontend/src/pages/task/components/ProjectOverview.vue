<script setup lang="ts">
import type { ProjectStage } from "@/pages/task/projectWorkspace";
import { useTaskStore } from "@/stores/task";
import type { Message } from "@/utils/response";
import {
	ArrowRight,
	Bot,
	CheckCircle2,
	CircleAlert,
	Clock3,
	FileCode2,
	FileText,
	ListChecks,
	Sparkles,
	TriangleAlert,
} from "lucide-vue-next";
import { computed } from "vue";

const props = defineProps<{
	stages: ProjectStage[];
	runningDuration: string;
}>();

const emit = defineEmits<{
	openApproval: [];
	selectStage: [stage: ProjectStage["key"]];
}>();

const taskStore = useTaskStore();
const latestSummary = computed(
	() =>
		taskStore.executionSummaries[taskStore.executionSummaries.length - 1] ??
		null,
);
const completedStages = computed(
	() => props.stages.filter((stage) => stage.status === "completed").length,
);
const progress = computed(() =>
	Math.round((completedStages.value / Math.max(props.stages.length, 1)) * 100),
);
const recentActivity = computed(() =>
	taskStore.messages
		.filter(
			(message) =>
				message.msg_type !== "progress" && message.msg_type !== "activity",
		)
		.slice(-7)
		.reverse(),
);

const activityMeta = (message: Message) => {
	if (message.msg_type === "approval") {
		return {
			label: `等待审核：${message.node_label}`,
			icon: CircleAlert,
			tone: "text-[hsl(var(--warning))]",
		};
	}
	if (message.msg_type === "execution_summary") {
		return {
			label: `完成运行复核：${message.node_label}`,
			icon: CheckCircle2,
			tone: "text-[hsl(var(--success))]",
		};
	}
	if (message.msg_type === "tool") {
		return {
			label: `调用工具：${message.tool_name}`,
			icon: FileCode2,
			tone: "text-[hsl(var(--info))]",
		};
	}
	if (message.msg_type === "agent") {
		return {
			label: "Agent 更新了项目产物",
			icon: Bot,
			tone: "text-[hsl(var(--info))]",
		};
	}
	if (message.msg_type === "user") {
		return {
			label: "你补充了项目要求",
			icon: FileText,
			tone: "text-foreground",
		};
	}
	if (message.msg_type === "system") {
		return {
			label: message.content || "系统状态已更新",
			icon: message.type === "error" ? TriangleAlert : Clock3,
			tone:
				message.type === "error"
					? "text-[hsl(var(--danger))]"
					: "text-muted-foreground",
		};
	}
	return {
		label: "工作流进度已更新",
		icon: Clock3,
		tone: "text-muted-foreground",
	};
};

const formatTime = (value?: string) => {
	if (!value) return "刚刚";
	const date = new Date(value);
	if (Number.isNaN(date.getTime())) return "时间未知";
	return new Intl.DateTimeFormat("zh-CN", {
		hour: "2-digit",
		minute: "2-digit",
		hour12: false,
	}).format(date);
};
</script>

<template>
  <div class="mx-auto w-full max-w-6xl space-y-5 p-5 lg:p-6">
    <section class="grid gap-4 border-b pb-5 lg:grid-cols-[minmax(0,1fr)_260px]">
      <div>
        <p class="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">当前工作状态</p>
        <div class="mt-2 flex flex-wrap items-center gap-2">
          <h2 class="text-xl font-semibold tracking-tight">
            {{ taskStore.pendingApproval ? `等待验收：${taskStore.pendingApproval.node_label}` : taskStore.isRunning ? 'Agent 正在推进当前阶段' : latestSummary ? '本轮求解已有可复核结果' : '项目已就绪' }}
          </h2>
          <span
            class="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[11px] font-medium"
            :class="taskStore.pendingApproval ? 'bg-[hsl(var(--warning-subtle))] text-[hsl(var(--warning))]' : taskStore.isRunning ? 'bg-[hsl(var(--accent))] text-[hsl(var(--info))]' : 'bg-muted text-muted-foreground'"
          >
            <CircleAlert v-if="taskStore.pendingApproval" class="h-3 w-3" aria-hidden="true" />
            <Bot v-else class="h-3 w-3" aria-hidden="true" />
            {{ taskStore.pendingApproval ? '后续节点已锁定' : taskStore.isRunning ? '实时运行中' : '未在运行' }}
          </span>
        </div>
        <p class="mt-2 max-w-3xl text-xs leading-5 text-muted-foreground">
          {{ taskStore.pendingApproval?.summary || latestSummary?.run_summary || '上传题目后，工作流会按题目理解、模型设计、求解复核和论文写作依次产生可追踪成果。' }}
        </p>
        <button
          v-if="taskStore.pendingApproval"
          type="button"
          class="mt-3 inline-flex items-center gap-1.5 text-xs font-medium text-primary hover:underline"
          @click="emit('openApproval')"
        >
          前往人工审核
          <ArrowRight class="h-3.5 w-3.5" aria-hidden="true" />
        </button>
      </div>

      <dl class="grid grid-cols-3 gap-3 rounded-lg border bg-[hsl(var(--surface-subtle))] p-3 lg:grid-cols-1">
        <div class="flex items-center justify-between gap-2 lg:border-b lg:pb-2">
          <dt class="text-[11px] text-muted-foreground">流程进度</dt>
          <dd class="mono-data text-sm font-semibold">{{ progress }}%</dd>
        </div>
        <div class="flex items-center justify-between gap-2 lg:border-b lg:pb-2">
          <dt class="text-[11px] text-muted-foreground">运行时长</dt>
          <dd class="mono-data text-sm font-semibold">{{ props.runningDuration }}</dd>
        </div>
        <div class="flex items-center justify-between gap-2">
          <dt class="text-[11px] text-muted-foreground">结构化结果</dt>
          <dd class="mono-data text-sm font-semibold">{{ taskStore.executionSummaries.length }}</dd>
        </div>
      </dl>
    </section>

    <section aria-labelledby="flow-heading">
      <div class="mb-2.5 flex items-center justify-between">
        <h2 id="flow-heading" class="text-sm font-semibold">项目进度</h2>
        <span class="text-[11px] text-muted-foreground">每一步都经过独立检查点</span>
      </div>
      <div class="app-panel overflow-hidden">
        <button
          v-for="(stage, index) in props.stages"
          :key="stage.key"
          type="button"
          class="flex w-full items-center gap-3 border-b px-4 py-3 text-left transition-colors last:border-0 hover:bg-muted/35"
          @click="emit('selectStage', stage.key)"
        >
          <span
            class="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-[10px]"
            :class="{
              'border-[hsl(var(--success))] bg-[hsl(var(--success-subtle))] text-[hsl(var(--success))]': stage.status === 'completed',
              'border-[hsl(var(--warning))] bg-[hsl(var(--warning-subtle))] text-[hsl(var(--warning))]': stage.status === 'awaiting_approval' || stage.status === 'warning',
              'border-primary bg-primary text-primary-foreground': stage.status === 'running',
            }"
          >
            <CheckCircle2 v-if="stage.status === 'completed'" class="h-3.5 w-3.5" aria-hidden="true" />
            <CircleAlert v-else-if="stage.status === 'awaiting_approval' || stage.status === 'warning'" class="h-3.5 w-3.5" aria-hidden="true" />
            <span v-else>{{ index + 1 }}</span>
          </span>
          <span class="min-w-0 flex-1">
            <span class="block text-xs font-medium">{{ stage.label }}</span>
            <span class="mt-0.5 block text-[10px] text-muted-foreground">
              {{ stage.status === 'completed' ? '已有可追溯产物' : stage.status === 'awaiting_approval' ? '等待你的确认' : stage.status === 'warning' ? '已有产物，但证据存在缺口' : stage.status === 'failed' ? '本阶段未完成' : stage.status === 'running' ? 'Agent 正在执行' : '等待前置阶段' }}
            </span>
          </span>
          <ArrowRight class="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
        </button>
      </div>
    </section>

    <div class="grid gap-5 xl:grid-cols-[minmax(0,1.2fr)_minmax(280px,0.8fr)]">
      <section aria-labelledby="artifact-heading">
        <div class="mb-2.5 flex items-center gap-2">
          <h2 id="artifact-heading" class="text-sm font-semibold">最近产物</h2>
          <span v-if="latestSummary" class="rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">{{ latestSummary.status }}</span>
        </div>
        <div v-if="latestSummary" class="app-panel p-4">
          <div class="flex items-start justify-between gap-4">
            <div class="min-w-0">
              <p class="text-[11px] font-medium text-muted-foreground">{{ latestSummary.node_label }}</p>
              <h3 class="mt-1 truncate text-sm font-semibold" :title="latestSummary.selected_model">{{ latestSummary.selected_model || '尚未选定模型' }}</h3>
            </div>
            <span class="shrink-0 rounded-md bg-[hsl(var(--success-subtle))] px-2 py-1 text-[10px] font-medium text-[hsl(var(--success))]">已复核</span>
          </div>
          <p class="mt-3 text-xs leading-5 text-secondary">{{ latestSummary.modeler_summary }}</p>
          <div class="mt-3 flex flex-wrap gap-1.5">
            <span v-for="artifact in latestSummary.artifacts.slice(0, 5)" :key="artifact" class="max-w-48 truncate rounded-md border bg-muted/30 px-2 py-1 font-mono text-[10px] text-muted-foreground" :title="artifact">{{ artifact }}</span>
          </div>
          <button type="button" class="mt-3 inline-flex items-center gap-1.5 text-xs font-medium text-primary hover:underline" @click="emit('selectStage', 'results')">
            打开结果分析
            <ArrowRight class="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        </div>
        <div v-else class="app-panel flex min-h-40 items-center justify-center border-dashed p-5 text-center">
          <div>
            <ListChecks class="mx-auto h-5 w-5 text-muted-foreground" aria-hidden="true" />
            <p class="mt-2 text-xs font-medium">尚未生成结构化求解产物</p>
            <p class="mt-1 text-[10px] text-muted-foreground">代码运行并通过质量门禁后会显示在这里。</p>
          </div>
        </div>
      </section>

      <section aria-labelledby="activity-heading">
        <div class="mb-2.5 flex items-center justify-between">
          <h2 id="activity-heading" class="text-sm font-semibold">最近活动</h2>
          <span class="mono-data text-[10px] text-muted-foreground">{{ taskStore.messages.length }}</span>
        </div>
        <div class="app-panel divide-y overflow-hidden">
          <div v-for="message in recentActivity" :key="message.id" class="flex gap-2.5 px-3 py-2.5">
            <component :is="activityMeta(message).icon" class="mt-0.5 h-3.5 w-3.5 shrink-0" :class="activityMeta(message).tone" aria-hidden="true" />
            <div class="min-w-0 flex-1">
              <p class="truncate text-[11px] font-medium" :title="activityMeta(message).label">{{ activityMeta(message).label }}</p>
              <p class="mt-0.5 text-[10px] text-muted-foreground">{{ formatTime(message.created_at) }}</p>
            </div>
          </div>
          <div v-if="!recentActivity.length" class="px-4 py-8 text-center text-xs text-muted-foreground">暂无项目活动</div>
        </div>
      </section>
    </div>

    <section class="app-panel flex items-start gap-3 p-4" aria-labelledby="next-heading">
      <span class="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-[hsl(var(--accent))] text-primary">
        <Sparkles class="h-4 w-4" aria-hidden="true" />
      </span>
      <div class="min-w-0 flex-1">
        <h2 id="next-heading" class="text-xs font-semibold">下一步建议</h2>
        <p class="mt-1 text-xs leading-5 text-muted-foreground">
          {{ taskStore.pendingApproval
            ? '先核对当前节点的成果、指标和产物；只有你批准后，工作流才会继续。'
            : taskStore.isRunning
              ? '可以在右侧 Copilot 补充约束，但当前步骤执行期间不会被强制打断。'
              : latestSummary
                ? '进入结果分析核对误差、稳健性和模型局限，再决定是否推进论文写作。'
                : '从题目理解开始检查子问题拆解，确认目标、约束和数据需求是否完整。' }}
        </p>
      </div>
    </section>
  </div>
</template>
