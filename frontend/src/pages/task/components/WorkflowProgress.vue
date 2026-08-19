<script setup lang="ts">
import {
	Collapsible,
	CollapsibleContent,
	CollapsibleTrigger,
} from "@/components/ui/collapsible";
import type { ActivityMessage, ProgressMessage } from "@/utils/response";
import {
	Check,
	ChevronDown,
	LoaderCircle,
	PauseCircle,
	TriangleAlert,
	XCircle,
} from "lucide-vue-next";
import { computed, ref } from "vue";

// ---- Props ----

/** 组件属性 */
interface Props {
	/** 最新进度快照；旧任务没有进度消息时为 null */
	progress: ProgressMessage | null;
	/** 任务是否运行中，决定当前步骤是否显示动画 */
	isRunning?: boolean;
	/** 最新活动播报（"现在具体在干嘛"） */
	activity?: ActivityMessage | null;
	/** 有待审批节点时的节点名，显示"等待你的决定" */
	waitingLabel?: string;
}
const props = withDefaults(defineProps<Props>(), {
	isRunning: false,
	activity: null,
	waitingLabel: "",
});

// ---- State ----

const expanded = ref(false);

// ---- Computed ----

const runningStage = computed(
	() =>
		props.progress?.stages.find((stage) => stage.status === "running") ?? null,
);

const statusText = computed(() => {
	if (!props.progress) return "";
	if (props.waitingLabel) return `等待你的决定：${props.waitingLabel}`;
	if (runningStage.value) return `正在进行：${runningStage.value.plain_label}`;
	if (props.progress.percent >= 100) return "全部步骤已完成";
	return "等待下一步开始";
});

const stepText = computed(() => {
	if (!props.progress) return "";
	const currentStep =
		props.progress.completed_count + (runningStage.value ? 1 : 0);
	const total = props.progress.total_known
		? `${props.progress.total_count}`
		: `约${props.progress.total_count}`;
	return `第 ${currentStep}/${total} 步`;
});
</script>

<template>
	<div
		v-if="props.progress"
		class="shrink-0 border-b bg-card px-4 py-2"
		data-testid="workflow-progress"
	>
		<Collapsible v-model:open="expanded">
			<div class="flex items-center gap-3">
				<PauseCircle
					v-if="props.waitingLabel"
					class="h-4 w-4 shrink-0 text-[hsl(var(--warning))]"
					aria-hidden="true"
				/>
				<span v-else class="relative flex h-2 w-2 shrink-0">
					<span
						v-if="props.isRunning && runningStage"
						class="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary/60 motion-reduce:animate-none"
					/>
					<span
						class="relative inline-flex h-2 w-2 rounded-full"
						:class="runningStage ? 'bg-primary' : 'bg-muted-foreground/50'"
					/>
				</span>
				<div class="min-w-0 flex-1">
					<div class="flex items-baseline gap-2">
						<span
							class="truncate text-[11px] font-medium"
							:class="props.waitingLabel ? 'text-[hsl(var(--warning))]' : ''"
						>
							{{ statusText }}
						</span>
						<span
							v-if="!props.waitingLabel && runningStage?.description"
							class="hidden truncate text-[10px] text-muted-foreground sm:inline"
						>
							{{ runningStage.description }}
						</span>
					</div>
					<div class="mt-1 h-1 overflow-hidden rounded-full bg-muted">
						<div
							class="h-full rounded-full transition-all duration-500"
							:class="props.waitingLabel ? 'bg-[hsl(var(--warning))]' : 'bg-primary'"
							:style="{ width: `${props.progress.percent}%` }"
						/>
					</div>
				</div>
				<span class="shrink-0 text-[10px] tabular-nums text-muted-foreground">
					{{ props.progress.percent }}% · {{ stepText }}
				</span>
				<CollapsibleTrigger
					class="inline-flex h-6 shrink-0 items-center gap-1 rounded px-1.5 text-[10px] text-muted-foreground hover:bg-muted"
					data-testid="workflow-progress-toggle"
				>
					{{ expanded ? "收起" : "全部步骤" }}
					<ChevronDown
						class="h-3 w-3 transition-transform"
						:class="expanded ? 'rotate-180' : ''"
						aria-hidden="true"
					/>
				</CollapsibleTrigger>
			</div>
			<div
				v-if="!props.waitingLabel && props.activity"
				class="mt-1.5 flex min-w-0 items-baseline gap-2 pl-5"
				data-testid="workflow-activity"
			>
				<span class="shrink-0 text-[10px] font-medium text-primary/80">
					{{ props.activity.content }}
				</span>
				<span
					v-if="props.activity.detail"
					class="mono-data min-w-0 truncate text-[10px] text-muted-foreground"
				>
					{{ props.activity.detail }}
				</span>
			</div>
			<CollapsibleContent>
				<ol class="mt-2 grid gap-1 sm:grid-cols-2 xl:grid-cols-3">
					<li
						v-for="stage in props.progress.stages"
						:key="stage.node_id"
						class="flex items-start gap-2 rounded-md px-2 py-1"
						:class="stage.status === 'running' ? 'bg-primary/5' : ''"
					>
						<span class="mt-0.5 flex h-3.5 w-3.5 shrink-0 items-center justify-center">
							<Check
								v-if="stage.status === 'completed'"
								class="h-3.5 w-3.5 text-[hsl(var(--success))]"
								aria-hidden="true"
							/>
							<LoaderCircle
								v-else-if="stage.status === 'running'"
								class="h-3.5 w-3.5 animate-spin text-primary motion-reduce:animate-none"
								aria-hidden="true"
							/>
							<TriangleAlert
								v-else-if="stage.status === 'warning'"
								class="h-3.5 w-3.5 text-[hsl(var(--warning))]"
								aria-hidden="true"
							/>
							<XCircle
								v-else-if="stage.status === 'failed'"
								class="h-3.5 w-3.5 text-destructive"
								aria-hidden="true"
							/>
							<span v-else class="h-1.5 w-1.5 rounded-full bg-muted-foreground/40" />
						</span>
						<div class="min-w-0">
							<div
								class="text-[11px]"
								:class="stage.status === 'pending' ? 'text-muted-foreground' : 'font-medium'"
							>
								{{ stage.plain_label }}
							</div>
							<div v-if="stage.description" class="text-[10px] text-muted-foreground">
								{{ stage.description }}
							</div>
						</div>
					</li>
				</ol>
			</CollapsibleContent>
		</Collapsible>
	</div>
</template>
