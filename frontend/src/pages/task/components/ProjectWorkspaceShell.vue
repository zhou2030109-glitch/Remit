<script setup lang="ts">
import CreateProjectSheet from "@/components/CreateProjectSheet.vue";
import GlobalCommandPalette from "@/components/GlobalCommandPalette.vue";
import { Button } from "@/components/ui/button";
import {
	ResizableHandle,
	ResizablePanel,
	ResizablePanelGroup,
} from "@/components/ui/resizable";
import ApiDialog from "@/pages/chat/components/ApiDialog.vue";
import AICopilot from "@/pages/task/components/AICopilot.vue";
import ProjectCodeAssets from "@/pages/task/components/ProjectCodeAssets.vue";
import ProjectDataView from "@/pages/task/components/ProjectDataView.vue";
import ProjectHeader from "@/pages/task/components/ProjectHeader.vue";
import ProjectLiteratureView from "@/pages/task/components/ProjectLiteratureView.vue";
import ProjectModelView from "@/pages/task/components/ProjectModelView.vue";
import ProjectOverview from "@/pages/task/components/ProjectOverview.vue";
import ProjectPaperView from "@/pages/task/components/ProjectPaperView.vue";
import ProjectProblemView from "@/pages/task/components/ProjectProblemView.vue";
import ProjectResultsView from "@/pages/task/components/ProjectResultsView.vue";
import ProjectStageSidebar from "@/pages/task/components/ProjectStageSidebar.vue";
import WorkflowProgress from "@/pages/task/components/WorkflowProgress.vue";
import type {
	ProjectAssetCount,
	ProjectStage,
	StageKey,
	StageStatus,
} from "@/pages/task/projectWorkspace";
import { useTaskStore } from "@/stores/task";
import { AgentType } from "@/utils/enum";
import { useMediaQuery } from "@vueuse/core";
import {
	Check,
	CircleAlert,
	FileCode2,
	ListChecks,
	LoaderCircle,
} from "lucide-vue-next";
import { computed, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

const props = defineProps<{
	taskId: string;
	writerSequence: string[];
	runningDuration: string;
	resumeAvailable: boolean;
	isStopping: boolean;
	isDecidingApproval: boolean;
}>();

const emit = defineEmits<{
	stop: [];
	resume: [];
	approve: [];
	revise: [];
}>();

const taskStore = useTaskStore();
const route = useRoute();
const router = useRouter();
const wideWorkspace = useMediaQuery("(min-width: 1024px)");
const sidebarCollapsed = ref(false);
const copilotOpen = ref(wideWorkspace.value);
const showCodeAssets = ref(false);
const createProjectOpen = ref(false);
const settingsOpen = ref(false);
const commandPaletteOpen = ref(false);

const stageKeys: StageKey[] = [
	"overview",
	"problem",
	"data",
	"literature",
	"model",
	"solve",
	"results",
	"paper",
];
const routeStage = String(
	route.params.stage ?? route.query.stage ?? "overview",
) as StageKey;
const activeStage = ref<StageKey>(
	stageKeys.includes(routeStage) ? routeStage : "overview",
);

const taskSummary = computed(() =>
	taskStore.taskHistory.find((task) => task.task_id === props.taskId),
);
const projectTitle = computed(() => taskSummary.value?.title || "数学建模项目");
const latestSummary = computed(
	() =>
		taskStore.executionSummaries[taskStore.executionSummaries.length - 1] ??
		null,
);
const selectedModel = computed(() => latestSummary.value?.selected_model ?? "");

function stageForNode(nodeId = "", nodeLabel = ""): StageKey {
	const value = `${nodeId} ${nodeLabel}`.toLowerCase();
	if (/^coordinator\b|^analysis\b|题目理解|题意识别/.test(value))
		return "problem";
	if (/^research\b|数据侦察|文献调研/.test(value)) return "data";
	if (/writer|paper|论文/.test(value)) return "paper";
	if (/result|quality|sensitivity|robust|analysis|结果|检验|稳健/.test(value))
		return "results";
	if (/ques\d|solve|coder|code|求解|代码/.test(value)) return "solve";
	if (/model|assumption|建模|模型/.test(value)) return "model";
	if (/data|eda|clean|数据|清洗/.test(value)) return "data";
	return "problem";
}

const approvalStage = computed(() =>
	taskStore.pendingApproval
		? stageForNode(
				taskStore.pendingApproval.node_id,
				taskStore.pendingApproval.node_label,
			)
		: null,
);

const runningStage = computed<StageKey>(() => {
	for (let index = taskStore.messages.length - 1; index >= 0; index--) {
		const message = taskStore.messages[index];
		if (message.msg_type !== "agent") continue;
		if (message.agent_type === AgentType.WRITER) return "paper";
		if (message.agent_type === AgentType.CODER) return "solve";
		if (message.agent_type === AgentType.MODELER) return "model";
		return "problem";
	}
	return "problem";
});

const failedStage = computed<StageKey | null>(() => {
	if (taskStore.isRunning || taskStore.pendingApproval) return null;
	const lastError = [...taskStore.messages]
		.reverse()
		.find(
			(message) => message.msg_type === "system" && message.type === "error",
		);
	return lastError ? runningStage.value : null;
});

function statusForStage(key: StageKey): StageStatus {
	if (taskSummary.value?.status === "completed") return "completed";
	if (approvalStage.value === key) return "awaiting_approval";
	if (failedStage.value === key) return "failed";
	if (taskStore.isRunning && runningStage.value === key) return "running";
	if (key === "overview")
		return taskStore.messages.length
			? "completed"
			: taskStore.isRunning
				? "running"
				: "not_started";
	if (key === "problem")
		return taskStore.workspaceSnapshot?.refined_analysis.outcome.status ===
			"warning"
			? "warning"
			: taskStore.workspaceSnapshot?.refined_analysis.outcome.status ===
					"failed"
				? "failed"
				: Object.keys(
							taskStore.workspaceSnapshot?.refined_analysis.question_analyses ??
								{},
						).length || taskStore.coordinatorMessages.length
					? "completed"
					: "not_started";
	if (key === "data")
		return taskStore.workspaceSnapshot?.research.outcome.status === "completed"
			? "completed"
			: taskStore.workspaceSnapshot?.research.outcome.status === "warning"
				? "warning"
				: taskStore.workspaceSnapshot?.research.outcome.status === "failed"
					? "failed"
					: "not_started";
	if (key === "literature") {
		const cards =
			taskStore.workspaceSnapshot?.method_evidence?.method_cards ?? [];
		if (!cards.length) return "not_started";
		// 方法卡已有但尚未裁决引用，说明证据链还没走完
		return taskStore.workspaceSnapshot?.method_evidence?.citation_entries
			?.length
			? "completed"
			: "warning";
	}
	if (key === "model")
		return taskStore.modelerMessages.length ? "completed" : "not_started";
	if (key === "solve")
		return taskStore.executionSummaries.length ? "completed" : "not_started";
	if (key === "results") {
		return taskStore.executionSummaries.some(
			(summary) => summary.status === "passed" || summary.status === "refined",
		)
			? "completed"
			: "not_started";
	}
	return taskStore.writerMessages.length ? "completed" : "not_started";
}

const stageLabels: Record<StageKey, string> = {
	overview: "项目概览",
	problem: "题目理解",
	data: "数据处理",
	literature: "文献与方法",
	model: "模型设计",
	solve: "模型求解",
	results: "结果分析",
	paper: "论文写作",
};

const stages = computed<ProjectStage[]>(() =>
	stageKeys.map((key) => ({
		key,
		label: stageLabels[key],
		status: statusForStage(key),
	})),
);

const assets = computed<ProjectAssetCount[]>(() => [
	{
		key: "datasets",
		label: "数据与附件",
		count: Array.isArray(
			taskStore.workspaceSnapshot?.research.data_profile.files,
		)
			? taskStore.workspaceSnapshot.research.data_profile.files.length
			: taskStore.files.length,
	},
	{
		key: "code",
		label: "代码文件",
		count: taskStore.executionSummaries.reduce(
			(total, summary) => total + summary.code_locations.length,
			0,
		),
	},
	{
		key: "charts",
		label: "图表资产",
		count: taskStore.executionSummaries.reduce(
			(total, summary) => total + summary.paper_ready_images.length,
			0,
		),
	},
	{
		key: "experiments",
		label: "实验记录",
		count: taskStore.executionSummaries.length,
	},
	{ key: "paper", label: "论文版本", count: taskStore.writerMessages.length },
	{
		key: "references",
		label: "文献方法卡",
		count:
			taskStore.workspaceSnapshot?.method_evidence?.method_cards?.length ??
			Number(
				taskStore.workspaceSnapshot?.research.literature_review.paper_count ??
					0,
			),
	},
]);

const approvalBannerText = computed(() => {
	const approval = taskStore.pendingApproval;
	if (!approval) return "";
	const status = String(approval.quality_report?.status ?? "");
	if (status === "warning" || status === "failed") {
		return `${approval.node_label} 已生成，但证据核验尚未完整，请仔细核对后决定`;
	}
	return `${approval.node_label} 已生成可核对产物，后续流程等待你验收`;
});

const projectCommands = computed(() => {
	const commands = [
		{ id: "problem", label: "打开题目理解", hint: "1" },
		{ id: "literature", label: "打开文献与方法", hint: "3" },
		{ id: "results", label: "打开结果分析", hint: "5" },
		{ id: "code", label: "打开代码与文件" },
		{ id: "paper", label: "打开论文写作", hint: "6" },
		{ id: "download", label: "导出项目消息" },
		{ id: "theme", label: "切换浅色 / 深色主题" },
	];
	if (taskStore.pendingApproval) {
		commands.unshift({ id: "approve", label: "批准当前建议并继续" });
	}
	if (taskStore.isRunning) {
		commands.unshift({ id: "stop", label: "停止当前任务" });
	} else if (props.resumeAvailable) {
		commands.unshift({ id: "resume", label: "从节点续跑" });
	}
	return commands;
});

function selectStage(stage: StageKey) {
	activeStage.value = stage;
	showCodeAssets.value = false;
	if (route.path.startsWith("/project/")) {
		void router.replace(
			`/project/${encodeURIComponent(props.taskId)}/${stage}`,
		);
	} else {
		void router.replace({ path: route.path, query: { ...route.query, stage } });
	}
}

function openApproval() {
	copilotOpen.value = true;
	if (approvalStage.value) selectStage(approvalStage.value);
}

function selectAsset(asset: ProjectAssetCount["key"]) {
	if (asset === "references") {
		selectStage("literature");
		return;
	}
	if (asset === "datasets") {
		selectStage("data");
		return;
	}
	if (asset === "code") {
		selectStage("solve");
		showCodeAssets.value = true;
		return;
	}
	if (asset === "paper") {
		selectStage("paper");
		return;
	}
	selectStage("results");
}

function handleCommand(id: string) {
	if (stageKeys.includes(id as StageKey)) {
		selectStage(id as StageKey);
		return;
	}
	if (id === "code") {
		selectStage("solve");
		showCodeAssets.value = true;
		return;
	}
	if (id === "stop") emit("stop");
	if (id === "resume") emit("resume");
	if (id === "approve") emit("approve");
	if (id === "download") taskStore.downloadMessages();
	if (id === "theme") {
		const isDark = document.documentElement.classList.toggle("dark");
		window.localStorage.setItem("remit-theme", isDark ? "dark" : "light");
	}
}

watch(
	() => route.params.stage,
	(value) => {
		const stage = String(value ?? route.query.stage ?? "") as StageKey;
		if (stageKeys.includes(stage)) activeStage.value = stage;
	},
);

watch(wideWorkspace, (isWide) => {
	if (!isWide) copilotOpen.value = false;
});
</script>

<template>
  <div class="fixed inset-0 flex min-h-0 flex-col bg-background">
    <ProjectHeader
      :task-id="props.taskId"
      :title="projectTitle"
      :stages="stages"
      :active-stage="activeStage"
      :running-duration="props.runningDuration"
      :ws-status="taskStore.wsStatus"
      :is-running="taskStore.isRunning"
      :is-stopping="props.isStopping"
      :can-resume="props.resumeAvailable"
      :selected-model="selectedModel"
      :copilot-open="copilotOpen"
      @select-stage="selectStage"
      @stop="emit('stop')"
      @resume="emit('resume')"
      @download="taskStore.downloadMessages"
      @toggle-copilot="copilotOpen = !copilotOpen"
    />

    <div class="flex min-h-0 flex-1">
      <div class="hidden h-full lg:block">
        <ProjectStageSidebar
          :stages="stages"
          :active-stage="activeStage"
          :assets="assets"
          :collapsed="sidebarCollapsed"
          environment-label="MATLAB 优先 · Python 备用"
          @select="selectStage"
          @select-asset="selectAsset"
          @toggle="sidebarCollapsed = !sidebarCollapsed"
          @settings="settingsOpen = true"
        />
      </div>

      <ResizablePanelGroup direction="horizontal" class="min-w-0 flex-1">
        <ResizablePanel :default-size="copilotOpen ? 75 : 100" :min-size="52" class="min-w-0">
          <main class="flex h-full min-h-0 min-w-0 flex-col" :aria-label="`${stageLabels[activeStage]}工作区`">
            <WorkflowProgress
              :progress="taskStore.latestProgress"
              :is-running="taskStore.isRunning"
              :activity="taskStore.isRunning ? taskStore.latestActivity : null"
              :waiting-label="taskStore.pendingApproval?.node_label ?? ''"
            />

            <div v-if="taskStore.pendingApproval" class="flex shrink-0 flex-wrap items-center gap-2 border-b border-[hsl(var(--warning)/0.28)] bg-[hsl(var(--warning-subtle))] px-4 py-2">
              <CircleAlert class="h-3.5 w-3.5 text-[hsl(var(--warning))]" aria-hidden="true" />
              <span class="min-w-0 flex-1 truncate text-[11px] font-medium text-[hsl(var(--warning))]">
                {{ approvalBannerText }}
              </span>
              <Button type="button" variant="outline" size="sm" class="h-7 bg-card px-2 text-[10px]" :disabled="props.isDecidingApproval" @click="emit('revise')">提修改意见</Button>
              <Button type="button" size="sm" class="h-7 px-2 text-[10px]" :disabled="props.isDecidingApproval" @click="emit('approve')">
                <LoaderCircle v-if="props.isDecidingApproval" class="mr-1 h-3 w-3 animate-spin motion-reduce:animate-none" aria-hidden="true" />
                <Check v-else class="mr-1 h-3 w-3" aria-hidden="true" />
                批准并继续
              </Button>
            </div>

            <div v-if="activeStage === 'solve' || activeStage === 'results'" class="flex h-10 shrink-0 items-center justify-between border-b bg-card px-4">
              <div class="flex items-center gap-2">
                <h2 class="text-xs font-semibold">{{ stageLabels[activeStage] }}</h2>
                <span class="text-[10px] text-muted-foreground">结构化运行记录与真实文件索引</span>
              </div>
              <div class="flex rounded-md border bg-muted/40 p-0.5">
                <button type="button" class="inline-flex h-6 items-center gap-1 rounded px-2 text-[10px]" :class="!showCodeAssets ? 'bg-card font-medium shadow-sm' : 'text-muted-foreground'" @click="showCodeAssets = false">
                  <ListChecks class="h-3 w-3" aria-hidden="true" />
                  运行摘要
                </button>
                <button type="button" class="inline-flex h-6 items-center gap-1 rounded px-2 text-[10px]" :class="showCodeAssets ? 'bg-card font-medium shadow-sm' : 'text-muted-foreground'" @click="showCodeAssets = true">
                  <FileCode2 class="h-3 w-3" aria-hidden="true" />
                  代码与文件
                </button>
              </div>
            </div>

            <div class="min-h-0 flex-1 overflow-hidden">
              <ProjectOverview
                v-if="activeStage === 'overview'"
                class="h-full overflow-y-auto"
                :stages="stages"
                :running-duration="props.runningDuration"
                @open-approval="openApproval"
                @select-stage="selectStage"
              />
              <ProjectProblemView v-else-if="activeStage === 'problem'" :task-id="props.taskId" />
              <ProjectDataView v-else-if="activeStage === 'data'" :task-id="props.taskId" />
              <ProjectLiteratureView v-else-if="activeStage === 'literature'" />
              <ProjectModelView v-else-if="activeStage === 'model'" />
              <ProjectCodeAssets v-else-if="(activeStage === 'solve' || activeStage === 'results') && showCodeAssets" class="h-full overflow-y-auto" />
              <ProjectResultsView v-else-if="activeStage === 'solve' || activeStage === 'results'" :view="activeStage" />
              <ProjectPaperView v-else :messages="taskStore.writerMessages" :writer-sequence="props.writerSequence" />
            </div>
          </main>
        </ResizablePanel>

        <template v-if="copilotOpen">
          <ResizableHandle with-handle />
          <ResizablePanel :default-size="25" :min-size="20" :max-size="36" class="min-w-[280px] max-xl:min-w-[260px]">
            <AICopilot
              :task-id="props.taskId"
              :selected-model="selectedModel"
              :deciding-approval="props.isDecidingApproval"
              @close="copilotOpen = false"
              @approve="emit('approve')"
              @revise="emit('revise')"
            />
          </ResizablePanel>
        </template>
      </ResizablePanelGroup>
    </div>

    <CreateProjectSheet v-model="createProjectOpen" />
    <ApiDialog v-model:open="settingsOpen" />
    <GlobalCommandPalette
      v-model="commandPaletteOpen"
      :tasks="taskStore.taskHistory"
      :context-actions="projectCommands"
      @new-project="createProjectOpen = true"
      @settings="settingsOpen = true"
      @command="handleCommand"
    />
  </div>
</template>
