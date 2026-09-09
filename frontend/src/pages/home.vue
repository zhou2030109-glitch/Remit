<script setup lang="ts">
import { getApiConfigStatus } from "@/apis/apiKeyApi";
import {
	type ResumeNode,
	type TaskSummary,
	getResumeOptions,
} from "@/apis/commonApi";
import CreateProjectSheet from "@/components/CreateProjectSheet.vue";
import GlobalCommandPalette from "@/components/GlobalCommandPalette.vue";
import ServiceStatus from "@/components/ServiceStatus.vue";
import ThemeToggle from "@/components/ThemeToggle.vue";
import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogClose,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { useToast } from "@/components/ui/toast";
import ApiDialog from "@/pages/chat/components/ApiDialog.vue";
import { useTaskStore } from "@/stores/task";
import { displayTitle } from "@/utils/title";
import { isAxiosError } from "axios";
import {
	AlertCircle,
	Bell,
	Bot,
	CheckCircle2,
	ChevronRight,
	CircleDot,
	Command,
	FileText,
	FolderKanban,
	Gauge,
	Home,
	KeyRound,
	LoaderCircle,
	Network,
	Play,
	Plus,
	Search,
	Settings2,
	Trash2,
} from "lucide-vue-next";
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { RouterLink, useRoute } from "vue-router";

const taskStore = useTaskStore();
const route = useRoute();
const { toast } = useToast();
const createProjectOpen = ref(false);
const commandPaletteOpen = ref(false);
const settingsOpen = ref(false);
const isLoading = ref(true);
const taskPendingDelete = ref<TaskSummary | null>(null);
const isDeleting = ref(false);
const clearHistoryDialogOpen = ref(false);
const isClearingHistory = ref(false);

// 状态只陈述事实：没有真实进度数据，就绝不渲染百分比或"当前阶段"。
const statusConfig: Record<
	TaskSummary["status"],
	{ label: string; hint: string }
> = {
	running: { label: "运行中", hint: "正在执行工作流" },
	awaiting_approval: { label: "待确认", hint: "等你审阅后继续" },
	completed: { label: "已完成", hint: "全部节点已通过检查" },
	failed: { label: "需处理", hint: "执行中断，进入项目查看原因" },
	stopped: { label: "已暂停", hint: "可从检查点继续" },
};

const displayResumeNodes = computed(() => resumeNodes.value.slice(0, 5));
const resumeOverflow = computed(() =>
	Math.max(resumeNodes.value.length - displayResumeNodes.value.length, 0),
);

function nodeStateLabel(status: ResumeNode["status"]): string {
	return status === "completed"
		? "已完成"
		: status === "interrupted"
			? "已中断"
			: "待执行";
}

const continueActionLabel = computed(() => {
	if (taskStore.taskHistoryLoadError) return "重新加载";
	if (!recentTask.value) return "开始建模";
	switch (recentTask.value.status) {
		case "failed":
			return "查看原因";
		case "stopped":
			return "恢复建模";
		case "awaiting_approval":
			return "去审阅";
		case "completed":
			return "查看成果";
		default:
			return "跟踪进度";
	}
});

const failedCount = computed(
	() => taskStore.taskHistory.filter((task) => task.status === "failed").length,
);
const stoppedCount = computed(
	() =>
		taskStore.taskHistory.filter((task) => task.status === "stopped").length,
);
const heroTitle = computed(() => {
	if (taskStore.taskHistoryLoadError) return "暂时读不到项目";
	if (recentTask.value) return displayTitle(recentTask.value.title);
	if (isLoading.value) return "正在载入项目…";
	return "创建第一个建模项目";
});
type PaletteFilter = "awaiting_approval" | "failed";
const commandPaletteStatusFilter = ref<PaletteFilter | null>(null);

function openFilteredPalette(filter: PaletteFilter): void {
	commandPaletteStatusFilter.value = filter;
	commandPaletteOpen.value = true;
}

watch(commandPaletteOpen, (open) => {
	if (!open) commandPaletteStatusFilter.value = null;
});

async function retryLoadHistory(): Promise<void> {
	await refreshActiveTaskStatus();
}

function statsOrDash(value: number): string | number {
	return taskStore.taskHistoryLoadError ? "—" : value;
}

const recentTask = computed(() => taskStore.taskHistory[0] ?? null);
const recentTasks = computed(() => taskStore.taskHistory.slice(0, 4));
const reviewTasks = computed(() =>
	taskStore.taskHistory
		.filter((task) => task.status === "awaiting_approval")
		.slice(0, 3),
);

// 四个核心角色的真实配置就绪状态：不展示模型名，只告诉队员"哪一环还没接上"。
type ReadinessPhase = "loading" | "ready" | "error";
const agentReadinessPhase = ref<ReadinessPhase>("loading");
const agentReadiness = ref<Record<string, boolean>>({});

async function loadAgentReadiness(): Promise<void> {
	agentReadinessPhase.value = "loading";
	try {
		const response = await getApiConfigStatus();
		const agents = response.data?.agents ?? {};
		agentReadiness.value = Object.fromEntries(
			Object.entries(agents).map(([key, status]) => [
				key,
				Boolean(status?.configured),
			]),
		);
		agentReadinessPhase.value = "ready";
	} catch {
		agentReadinessPhase.value = "error";
		agentReadiness.value = {};
	}
}

function agentState(key: string): { label: string; state: string } {
	if (agentReadinessPhase.value === "loading")
		return { label: "查询中", state: "loading" };
	if (agentReadinessPhase.value === "error")
		return { label: "状态未知", state: "unknown" };
	return agentReadiness.value[key]
		? { label: "就绪", state: "ready" }
		: { label: "未配置", state: "unset" };
}

onMounted(() => {
	void loadAgentReadiness();
});

watch(settingsOpen, (open, wasOpen) => {
	if (wasOpen && !open) void loadAgentReadiness();
});

const resumeNodes = ref<ResumeNode[]>([]);

async function loadResumeNodes(taskId: string): Promise<void> {
	try {
		const response = await getResumeOptions(taskId);
		resumeNodes.value = response.data?.nodes ?? [];
	} catch {
		// 拿不到真实节点就整块隐藏，绝不回退到假流水线。
		resumeNodes.value = [];
	}
}

// 存在活跃任务时轮询刷新，避免死监控屏；空闲时完全停止。
const hasActiveTask = computed(() =>
	taskStore.taskHistory.some((task) =>
		["running", "awaiting_approval"].includes(task.status),
	),
);
let historyPollTimer: number | undefined;
let historyPollInFlight = false;

async function refreshActiveTaskStatus(): Promise<void> {
	if (historyPollInFlight) return;
	historyPollInFlight = true;
	try {
		await taskStore.loadTaskHistory();
		if (!taskStore.taskHistoryLoadError && recentTask.value) {
			await loadResumeNodes(recentTask.value.task_id);
		}
	} finally {
		historyPollInFlight = false;
	}
}

watch(
	hasActiveTask,
	(active) => {
		if (active && historyPollTimer === undefined) {
			historyPollTimer = window.setInterval(() => {
				void refreshActiveTaskStatus();
			}, 30_000);
		} else if (!active && historyPollTimer !== undefined) {
			window.clearInterval(historyPollTimer);
			historyPollTimer = undefined;
		}
	},
	{ immediate: true },
);
onBeforeUnmount(() => {
	if (historyPollTimer !== undefined) window.clearInterval(historyPollTimer);
});

watch(
	() => recentTask.value?.task_id,
	(taskId) => {
		resumeNodes.value = [];
		if (taskId) void loadResumeNodes(taskId);
	},
	{ immediate: true },
);
const runningCount = computed(
	() =>
		taskStore.taskHistory.filter((task) => task.status === "running").length,
);
const approvalCount = computed(
	() =>
		taskStore.taskHistory.filter((task) => task.status === "awaiting_approval")
			.length,
);
const completedCount = computed(
	() =>
		taskStore.taskHistory.filter((task) => task.status === "completed").length,
);
const activeTaskCount = computed(
	() =>
		taskStore.taskHistory.filter((task) =>
			["running", "awaiting_approval"].includes(task.status),
		).length,
);

function formatTaskTime(value: string | number): string {
	const date = new Date(value);
	if (Number.isNaN(date.getTime())) return "未知";
	const delta = Date.now() - date.getTime();
	const minute = 60_000;
	const hour = 60 * minute;
	const day = 24 * hour;
	if (delta < minute) return "刚刚";
	if (delta < hour) return `${Math.floor(delta / minute)} 分钟前`;
	if (delta < day) return `${Math.floor(delta / hour)} 小时前`;
	return new Intl.DateTimeFormat("zh-CN", {
		month: "2-digit",
		day: "2-digit",
		hour: "2-digit",
		minute: "2-digit",
		hour12: false,
	}).format(date);
}

function handleDeleteDialogOpen(open: boolean): void {
	if (!open && !isDeleting.value) taskPendingDelete.value = null;
}

async function confirmTaskDeletion(): Promise<void> {
	const task = taskPendingDelete.value;
	if (!task || isDeleting.value) return;

	isDeleting.value = true;
	try {
		await taskStore.deleteTask(task.task_id);
		taskPendingDelete.value = null;
		toast({
			title: "项目已删除",
			description: `“${displayTitle(task.title, 60)}”已永久删除。`,
		});
	} catch (error) {
		const responseData = isAxiosError(error)
			? (error.response?.data as { detail?: string } | undefined)
			: undefined;
		toast({
			variant: "destructive",
			title: "无法删除项目",
			description: responseData?.detail || "删除失败，请稍后重试。",
		});
	} finally {
		isDeleting.value = false;
	}
}

function handleClearHistoryDialogOpen(open: boolean): void {
	if (!isClearingHistory.value) clearHistoryDialogOpen.value = open;
}

async function confirmClearHistory(): Promise<void> {
	if (
		isClearingHistory.value ||
		taskStore.taskHistory.length === 0 ||
		activeTaskCount.value > 0
	) {
		return;
	}

	isClearingHistory.value = true;
	try {
		const result = await taskStore.clearTaskHistory();
		clearHistoryDialogOpen.value = false;
		toast({
			title: "记录已清空",
			description: `已删除 ${result.deleted_count} 个历史项目。`,
		});
	} catch (error) {
		const responseData = isAxiosError(error)
			? (error.response?.data as { detail?: string } | undefined)
			: undefined;
		toast({
			variant: "destructive",
			title: "无法清空记录",
			description: responseData?.detail || "清空失败，请稍后重试。",
		});
	} finally {
		isClearingHistory.value = false;
	}
}

onMounted(async () => {
	if (route.query.new === "1") createProjectOpen.value = true;
	await taskStore.loadTaskHistory();
	isLoading.value = false;
});
</script>

<template>
	<div class="remit-home">
		<a href="#home-main" class="skip-link">跳到主要内容</a>

		<aside class="home-sidebar" aria-label="应用导航">
			<RouterLink to="/home" class="sidebar-brand" aria-label="Remit 主页">
				<img src="@/assets/remit-icon.png" alt="" />
				<span>Remit</span>
			</RouterLink>

			<nav class="sidebar-nav" aria-label="主要导航">
				<RouterLink to="/home" class="sidebar-item sidebar-active" aria-current="page">
					<Home aria-hidden="true" />
					<span>工作台</span>
				</RouterLink>
				<button type="button" class="sidebar-item" @click="createProjectOpen = true">
					<Plus aria-hidden="true" />
					<span>新建项目</span>
				</button>
				<button type="button" class="sidebar-item" @click="commandPaletteOpen = true">
					<FolderKanban aria-hidden="true" />
					<span>项目列表</span>
				</button>
				<RouterLink
					v-if="recentTask"
					:to="`/project/${recentTask.task_id}/overview`"
					class="sidebar-item"
				>
					<Play aria-hidden="true" />
					<span>继续项目</span>
				</RouterLink>
				<button type="button" class="sidebar-item" @click="commandPaletteOpen = true">
					<Command aria-hidden="true" />
					<span>命令面板</span>
				</button>
				<button type="button" class="sidebar-item" @click="settingsOpen = true">
					<KeyRound aria-hidden="true" />
					<span>模型连接</span>
				</button>
			</nav>

			<button type="button" class="sidebar-profile" @click="settingsOpen = true">
				<span class="profile-mark">R</span>
				<span>
					<strong>本地工作台</strong>
					<small>本地运行</small>
				</span>
				<Settings2 aria-hidden="true" />
			</button>
		</aside>

		<div class="home-page">
			<header class="liquid-nav liquid-surface">
				<RouterLink to="/home" class="mobile-brand" aria-label="Remit 主页">
					<img src="@/assets/remit-icon.png" alt="" />
					<span>Remit</span>
				</RouterLink>
				<div class="page-heading">
					<strong>数学建模工作台</strong>
				</div>

				<button type="button" class="nav-search" @click="commandPaletteOpen = true">
					<Search aria-hidden="true" />
					<span>搜索项目或命令</span>
					<kbd>Ctrl/⌘ K</kbd>
				</button>

				<div class="nav-actions">
					<ServiceStatus class="service-status" />
					<ThemeToggle />
					<button
						type="button"
						class="icon-button"
						aria-label="待确认事项"
						@click="openFilteredPalette('awaiting_approval')"
					>
						<Bell aria-hidden="true" />
						<span v-if="approvalCount" class="notification-dot">{{ approvalCount }}</span>
					</button>
					<button
						type="button"
						class="icon-button"
						aria-label="模型设置"
						@click="settingsOpen = true"
					>
						<Settings2 aria-hidden="true" />
					</button>
					<button type="button" class="create-button" aria-label="创建项目" @click="createProjectOpen = true">
						<span>创建项目</span>
						<Plus aria-hidden="true" />
					</button>
				</div>
			</header>

			<main id="home-main" class="home-main">
				<section class="hero-grid" aria-label="项目工作台概览">
					<article class="current-project-card">
						<div class="project-copy">
							<p>当前项目</p>
							<h1>{{ heroTitle }}</h1>
							<div class="project-progress">
							<template v-if="taskStore.taskHistoryLoadError">
								<span class="status-meta">本地后端暂时不可达，现有项目记录未被清空</span>
							</template>
							<template v-else-if="recentTask">
								<strong class="status-word" :data-status="recentTask.status">{{
									statusConfig[recentTask.status].label
								}}</strong>
								<span class="status-meta">
									{{ statusConfig[recentTask.status].hint }} · 上次更新
									{{ formatTaskTime(recentTask.updated_at) }}
								</span>
							</template>
							<template v-else>
								<span class="status-meta">上传题面后，工作流会从这里开始推进</span>
							</template>
							</div>
						</div>

						<div class="project-graphic" aria-hidden="true">
							<svg viewBox="0 0 420 190" role="img">
								<g class="graphic-grid">
									<path d="M18 154H400M48 124H400M82 94H400M118 64H400" />
									<path d="M120 36L72 170M174 36L142 170M228 36L212 170M282 36L282 170M336 36L352 170" />
								</g>
								<g class="model-solid">
									<path d="M86 68L145 42L181 84L149 137L84 120Z" />
									<path d="M86 68L149 137L84 120Z" />
									<path d="M145 42L149 137L181 84Z" />
									<circle cx="86" cy="68" r="3" />
									<circle cx="145" cy="42" r="3" />
									<circle cx="181" cy="84" r="3" />
									<circle cx="149" cy="137" r="3" />
								</g>
								
							</svg>
						</div>

						<div
							v-if="displayResumeNodes.length"
							class="workflow-line"
							aria-label="工作流节点状态"
						>
							<div
								v-for="node in displayResumeNodes"
								:key="node.node_id"
								class="workflow-chip"
								:class="node.status === 'interrupted' ? 'workflow-chip-interrupted' : ''"
								:data-state="node.status"
								:title="nodeStateLabel(node.status)"
							>
								<span class="workflow-dot" aria-hidden="true" />
								<strong>{{ node.label }}</strong>
								<small>{{ nodeStateLabel(node.status) }}</small>
							</div>
							<span v-if="resumeOverflow" class="workflow-overflow mono-data">
								+{{ resumeOverflow }}
							</span>
						</div>

						<button
							v-if="taskStore.taskHistoryLoadError"
							type="button"
							class="continue-action"
							aria-label="重新加载项目列表"
							@click="retryLoadHistory"
						>
							<span>{{ continueActionLabel }}</span>
							<ChevronRight aria-hidden="true" />
						</button>
						<RouterLink
							v-else-if="recentTask"
							:to="`/project/${recentTask.task_id}/overview`"
							class="continue-action"
						>
							<span>{{ continueActionLabel }}</span>
							<ChevronRight aria-hidden="true" />
						</RouterLink>
						<button v-else type="button" class="continue-action" @click="createProjectOpen = true">
							<span>{{ continueActionLabel }}</span>
							<ChevronRight aria-hidden="true" />
						</button>
					</article>

					<div class="quick-actions" aria-label="快捷操作">
						<button type="button" class="quick-orb liquid-surface" aria-label="新建项目" @click="createProjectOpen = true">
							<Plus aria-hidden="true" />
							<span>新建项目</span>
						</button>
						<button type="button" class="quick-orb liquid-surface" aria-label="打开全局命令" @click="commandPaletteOpen = true">
							<Command aria-hidden="true" />
							<span>全局命令</span>
						</button>
					</div>

					<section class="review-card soft-glass" aria-labelledby="review-title">
						<header>
							<h2 id="review-title">待人工确认</h2>
							<span class="count-mark mono-data">{{ approvalCount }}</span>
						</header>
						<div v-if="reviewTasks.length" class="review-list">
							<RouterLink
								v-for="task in reviewTasks"
								:key="task.task_id"
								:to="`/project/${task.task_id}/overview`"
								class="review-row"
							>
								<span class="review-icon"><AlertCircle aria-hidden="true" /></span>
								<strong>{{ displayTitle(task.title, 30) }}</strong>
								<small>{{ formatTaskTime(task.updated_at) }}</small>
								<ChevronRight aria-hidden="true" />
							</RouterLink>
						</div>
						<div v-else class="review-empty">
							<CheckCircle2 aria-hidden="true" />
							<strong>暂无待确认</strong>
						</div>
						<button type="button" class="review-more" @click="openFilteredPalette('awaiting_approval')">
							<span>查看全部</span>
							<ChevronRight aria-hidden="true" />
						</button>
					</section>
				</section>

				<section class="insight-grid" aria-label="项目数据概览">
					<article class="overview-card solid-card">
						<header>
							<div>
								<p>项目概览</p>
								<strong class="mono-data">{{ taskStore.taskHistory.length }}</strong>
							</div>
							<span>全部项目</span>
						</header>
						
						<dl>
							<div><dt>运行</dt><dd class="mono-data">{{ statsOrDash(runningCount) }}</dd></div>
							<div><dt>待确认</dt><dd class="mono-data">{{ statsOrDash(approvalCount) }}</dd></div>
							<div><dt>完成</dt><dd class="mono-data">{{ statsOrDash(completedCount) }}</dd></div>
						</dl>
					</article>

					<article class="resource-card solid-card">
						<header>
							<h2>运行状态</h2>
							<Gauge aria-hidden="true" />
						</header>
						<div class="resource-list">
							<div><span>需处理</span><strong class="mono-data">{{ statsOrDash(failedCount) }}</strong></div>
							<div><span>已暂停</span><strong class="mono-data">{{ statsOrDash(stoppedCount) }}</strong></div>
							<div><span>历史项目</span><strong class="mono-data">{{ statsOrDash(taskStore.taskHistory.length) }}</strong></div>
						</div>
						<button
							type="button"
							@click="failedCount ? openFilteredPalette('failed') : (settingsOpen = true)"
						>
							<span>{{ failedCount ? "查看需处理" : "模型连接" }}</span>
							<ChevronRight aria-hidden="true" />
						</button>
					</article>

					<article class="agent-card solid-card">
						<header>
							<h2>Agent 协作链</h2>
							<span>{{ recentTask ? statusConfig[recentTask.status].label : "待命" }}</span>
						</header>
						<ul class="agent-layout">
							<li><CircleDot aria-hidden="true" /><span>Coordinator</span><small>编排</small><span class="agent-state" :data-state="agentState('coordinator').state">{{ agentState('coordinator').label }}</span></li>
							<li><Network aria-hidden="true" /><span>Modeler</span><small>建模</small><span class="agent-state" :data-state="agentState('modeler').state">{{ agentState('modeler').label }}</span></li>
							<li><Command aria-hidden="true" /><span>Coder</span><small>计算</small><span class="agent-state" :data-state="agentState('coder').state">{{ agentState('coder').label }}</span></li>
							<li><FileText aria-hidden="true" /><span>Writer</span><small>写作</small><span class="agent-state" :data-state="agentState('writer').state">{{ agentState('writer').label }}</span></li>
						</ul>
					</article>
				</section>

				<section class="recent-section solid-card" aria-labelledby="recent-title">
					<header>
						<div>
							<h2 id="recent-title">最近项目</h2>
							<span class="mono-data">{{ taskStore.taskHistory.length }}</span>
						</div>
						<div class="recent-controls">
							<button type="button" @click="commandPaletteOpen = true"><Search aria-hidden="true" />搜索</button>
							<button
								type="button"
								:disabled="taskStore.taskHistory.length === 0"
								@click="clearHistoryDialogOpen = true"
							>
								<Trash2 aria-hidden="true" />清空
							</button>
						</div>
					</header>

					<div v-if="isLoading" class="recent-loading" aria-label="正在加载项目">
						<span v-for="index in 4" :key="index" />
					</div>
					<div v-else-if="taskStore.taskHistoryLoadError" class="recent-error">
						<strong>项目列表加载失败</strong>
						<span>请确认后端服务正在运行，然后重试。</span>
						<button type="button" @click="retryLoadHistory">重新加载</button>
					</div>
					<div v-else-if="recentTasks.length" class="recent-cards">
						<article v-for="task in recentTasks" :key="task.task_id" class="recent-project-card" :data-status="task.status">
							<div class="recent-card-top">
								<span>{{ statusConfig[task.status].label }}</span>
								<button
									type="button"
									:aria-label="`删除项目：${displayTitle(task.title, 60)}`"
									@click="taskPendingDelete = task"
								>
									<Trash2 aria-hidden="true" />
								</button>
							</div>
							<RouterLink :to="`/project/${task.task_id}/overview`">
								<h3>{{ displayTitle(task.title, 36) }}</h3>
								<p class="recent-hint">{{ statusConfig[task.status].hint }}</p>
								<footer>
									<small>{{ formatTaskTime(task.updated_at) }}</small>
									<ChevronRight aria-hidden="true" />
								</footer>
							</RouterLink>
						</article>
					</div>
					<div v-else class="recent-empty">
						<Bot aria-hidden="true" />
						<strong>还没有项目</strong>
						<button type="button" @click="createProjectOpen = true">立即创建</button>
					</div>
				</section>
			</main>
		</div>

		<CreateProjectSheet v-model="createProjectOpen" />
		<ApiDialog v-model:open="settingsOpen" />
		<GlobalCommandPalette
			v-model="commandPaletteOpen"
			:status-filter="commandPaletteStatusFilter"
			:tasks="taskStore.taskHistory"
			@new-project="createProjectOpen = true"
			@settings="settingsOpen = true"
		/>

		<Dialog :open="taskPendingDelete !== null" @update:open="handleDeleteDialogOpen">
			<DialogContent class="max-w-md">
				<DialogHeader>
					<DialogTitle>永久删除这个项目？</DialogTitle>
					<DialogDescription class="pt-1 leading-6">
						<span class="block font-medium text-foreground">{{ displayTitle(taskPendingDelete?.title, 60) }}</span>
						<span class="mt-1 block">消息、附件和生成文件都会删除，无法撤销。</span>
					</DialogDescription>
				</DialogHeader>
				<DialogFooter class="mt-2 gap-2 sm:gap-0">
					<DialogClose as-child>
						<Button type="button" variant="outline" :disabled="isDeleting">取消</Button>
					</DialogClose>
					<Button type="button" variant="destructive" :disabled="isDeleting" @click="confirmTaskDeletion">
						<LoaderCircle v-if="isDeleting" class="h-4 w-4 animate-spin" />
						{{ isDeleting ? "正在删除…" : "永久删除" }}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>

		<Dialog :open="clearHistoryDialogOpen" @update:open="handleClearHistoryDialogOpen">
			<DialogContent class="max-w-md">
				<DialogHeader>
					<DialogTitle>永久清空全部项目？</DialogTitle>
					<DialogDescription class="pt-1 leading-6">
						将删除 {{ taskStore.taskHistory.length }} 个项目的消息、附件和生成文件，无法撤销。
					</DialogDescription>
				</DialogHeader>
				<div v-if="activeTaskCount > 0" class="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/35 dark:text-amber-200">
					仍有 {{ activeTaskCount }} 个任务正在运行或等待确认。
				</div>
				<DialogFooter class="mt-2 gap-2 sm:gap-0">
					<DialogClose as-child>
						<Button type="button" variant="outline" :disabled="isClearingHistory">取消</Button>
					</DialogClose>
					<Button
						type="button"
						variant="destructive"
						:disabled="isClearingHistory || activeTaskCount > 0"
						@click="confirmClearHistory"
					>
						<LoaderCircle v-if="isClearingHistory" class="h-4 w-4 animate-spin" />
						{{ isClearingHistory ? "正在清空…" : "永久清空" }}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	</div>
</template>

<style scoped>
.remit-home {
	--acid: #e7ff2f;
	--acid-deep: #b9d500;
	--graphite: #121513;
	--graphite-soft: #1b201d;
	--bone: #f4f3ec;
	--home-canvas: #f4f3ec;
	--page-cut: #f4f3ec;
	--home-ink: #111310;
	--home-muted: #575c55;
	--panel-muted: #575c55;
	--panel: rgb(255 255 255 / 0.84);
	--panel-border: rgb(20 24 20 / 0.1);
	--desktop-content-max: 1440px;
	min-height: 100vh;
	background: var(--home-canvas);
	color: var(--home-ink);
}

:global(.dark .remit-home) {
	--home-canvas: #0b0e0c;
	--page-cut: #f0efe8;
	--home-ink: #f4f3ed;
	--home-muted: #a0a69f;
	--panel: #f0efe8;
	--panel-border: rgb(255 255 255 / 0.12);
}

button,
a {
	-webkit-tap-highlight-color: transparent;
}

.skip-link {
	position: fixed;
	left: 12px;
	top: 12px;
	z-index: 200;
	transform: translateY(-160%);
	visibility: hidden;
	opacity: 0;
	pointer-events: none;
	border-radius: 10px;
	background: var(--acid);
	color: #111;
	padding: 10px 14px;
	font-size: 13px;
	font-weight: 700;
	transition: transform 160ms ease, opacity 160ms ease;
}

.skip-link:focus-visible {
	transform: translateY(0);
	visibility: visible;
	opacity: 1;
	pointer-events: auto;
}

.home-sidebar {
	position: fixed;
	inset: 0 auto 0 0;
	z-index: 50;
	display: flex;
	width: 184px;
	flex-direction: column;
	background:
		radial-gradient(circle at 30% 16%, rgb(231 255 47 / 0.08), transparent 24%),
		linear-gradient(180deg, #151916, #0f1210 68%, #141815);
	color: #eef0ea;
	box-shadow: inset -1px 0 rgb(255 255 255 / 0.08);
}

.sidebar-brand {
	display: flex;
	height: 112px;
	flex: 0 0 auto;
	align-items: center;
	gap: 11px;
	padding: 0 28px;
	color: white;
	text-decoration: none;
}

.sidebar-brand img {
	width: 30px;
	height: 30px;
}

.sidebar-brand span {
	font-size: 21px;
	font-weight: 720;
	letter-spacing: -0.03em;
}

.sidebar-nav {
	display: flex;
	flex: 1;
	flex-direction: column;
	gap: 7px;
	padding: 12px 0 18px 10px;
}

.sidebar-item {
	position: relative;
	display: flex;
	height: 48px;
	width: calc(100% - 10px);
	align-items: center;
	gap: 13px;
	border: 0;
	border-radius: 14px;
	background: transparent;
	color: rgb(238 240 234 / 0.72);
	padding: 0 18px;
	font-size: 13px;
	font-weight: 560;
	text-decoration: none;
	transition: color 160ms ease, background-color 160ms ease;
}

.sidebar-item svg {
	width: 19px;
	height: 19px;
	flex: 0 0 auto;
	stroke-width: 1.7;
}

.sidebar-item:not(.sidebar-active):hover {
	background: rgb(255 255 255 / 0.07);
	color: white;
}

.sidebar-active {
	z-index: 2;
	width: calc(100% - 0px);
	border-radius: 18px 0 0 18px;
	background: var(--page-cut);
	color: #141714;
}

.sidebar-active::before,
.sidebar-active::after {
	position: absolute;
	right: 0;
	width: 24px;
	height: 24px;
	content: "";
	pointer-events: none;
}

.sidebar-active::before {
	top: -24px;
	border-bottom-right-radius: 24px;
	box-shadow: 8px 8px 0 8px var(--page-cut);
}

.sidebar-active::after {
	bottom: -24px;
	border-top-right-radius: 24px;
	box-shadow: 8px -8px 0 8px var(--page-cut);
}

:global(.dark .sidebar-active::before),
:global(.dark .sidebar-active::after) {
	display: none;
}

.sidebar-active svg {
	color: #a9c400;
	stroke-width: 2.2;
}

.sidebar-profile {
	display: grid;
	grid-template-columns: 38px minmax(0, 1fr) 18px;
	align-items: center;
	gap: 10px;
	margin: 12px;
	border: 1px solid rgb(255 255 255 / 0.11);
	border-radius: 18px;
	background: rgb(255 255 255 / 0.045);
	color: white;
	padding: 10px;
	text-align: left;
}

.profile-mark {
	display: grid;
	width: 38px;
	height: 38px;
	place-items: center;
	border-radius: 50%;
	background: #050605;
	box-shadow: inset 0 0 0 1px rgb(255 255 255 / 0.15);
	font-weight: 700;
}

.sidebar-profile strong,
.sidebar-profile small {
	display: block;
}

.sidebar-profile strong {
	font-size: 12px;
}

.sidebar-profile small {
	margin-top: 2px;
	color: rgb(255 255 255 / 0.66);
	font-size: 11px;
}

.sidebar-profile > svg {
	width: 16px;
	height: 16px;
	color: rgb(255 255 255 / 0.5);
}

.home-page {
	position: relative;
	min-height: 100vh;
	margin-left: 184px;
	overflow-x: clip;
	background: var(--home-canvas);
}

.home-page::before {
	position: fixed;
	inset: -10% -10% auto 36%;
	z-index: 0;
	height: 72vh;
	background:
		radial-gradient(circle at 62% 25%, rgb(192 226 218 / 0.42), transparent 26%),
		radial-gradient(circle at 84% 8%, rgb(231 255 47 / 0.18), transparent 21%),
		radial-gradient(circle at 32% 16%, rgb(203 219 239 / 0.38), transparent 26%);
	content: "";
	filter: blur(38px);
	pointer-events: none;
}

:global(.dark .home-page::before) {
	background:
		radial-gradient(circle at 70% 18%, rgb(56 84 79 / 0.24), transparent 28%),
		radial-gradient(circle at 88% 5%, rgb(231 255 47 / 0.08), transparent 20%),
		radial-gradient(circle at 38% 14%, rgb(58 64 92 / 0.22), transparent 30%);
}

.liquid-surface {
	position: relative;
	isolation: isolate;
	overflow: hidden;
	border: 1px solid rgb(255 255 255 / 0.74);
	background: linear-gradient(145deg, rgb(255 255 255 / 0.68), rgb(255 255 255 / 0.34));
	box-shadow:
		inset 0 1px 0 rgb(255 255 255 / 0.84),
		inset 0 -1px 0 rgb(255 255 255 / 0.25),
		0 18px 46px -28px rgb(41 48 42 / 0.48);
	backdrop-filter: blur(28px) saturate(1.28);
}

:global(.dark .liquid-surface) {
	border-color: rgb(255 255 255 / 0.18);
	background: linear-gradient(145deg, rgb(32 38 35 / 0.74), rgb(10 12 11 / 0.5));
	box-shadow:
		inset 0 1px 0 rgb(255 255 255 / 0.21),
		inset 0 -1px 0 rgb(255 255 255 / 0.05),
		0 22px 54px -30px rgb(0 0 0 / 0.9);
}

.liquid-surface::after {
	position: absolute;
	inset: -80% auto -80% -28%;
	z-index: -1;
	width: 22%;
	transform: rotate(12deg) translateX(-160%);
	background: linear-gradient(90deg, transparent, rgb(255 255 255 / 0.52), transparent);
	content: "";
	filter: blur(8px);
	transition: transform 700ms cubic-bezier(0.2, 0.8, 0.2, 1);
}

@media (hover: hover) {
	.liquid-surface:hover::after {
		transform: rotate(12deg) translateX(780%);
	}
}

.liquid-nav {
	position: sticky;
	top: 16px;
	z-index: 40;
	display: flex;
	width: calc(100% - 44px);
	max-width: var(--desktop-content-max);
	height: 72px;
	align-items: center;
	gap: 20px;
	margin: 16px auto 0;
	border-radius: 26px;
	padding: 0 14px 0 22px;
}

.mobile-brand {
	display: none;
}

.page-heading {
	display: flex;
	min-width: max-content;
	align-items: center;
	gap: 11px;
}

.page-heading strong {
	font-size: clamp(19px, 1.8vw, 29px);
	font-weight: 760;
	letter-spacing: -0.02em;
}

.nav-search {
	display: flex;
	height: 40px;
	min-width: 220px;
	max-width: 380px;
	flex: 1;
	align-items: center;
	gap: 9px;
	margin-left: auto;
	border: 1px solid rgb(22 27 22 / 0.11);
	border-radius: 999px;
	background: rgb(255 255 255 / 0.34);
	color: var(--home-muted);
	padding: 0 12px;
	font-size: 12px;
}

:global(.dark .nav-search) {
	border-color: rgb(255 255 255 / 0.12);
	background: rgb(0 0 0 / 0.18);
}

.nav-search svg {
	width: 15px;
	height: 15px;
}

.nav-search kbd {
	margin-left: auto;
	border: 1px solid currentColor;
	border-radius: 7px;
	padding: 2px 6px;
	font-size: 11px;
}

.nav-actions {
	display: flex;
	align-items: center;
	gap: 7px;
}

.icon-button {
	position: relative;
	display: grid;
	width: 40px;
	height: 40px;
	place-items: center;
	border: 1px solid rgb(22 27 22 / 0.1);
	border-radius: 50%;
	background: rgb(255 255 255 / 0.28);
	color: inherit;
}

:global(.dark .icon-button) {
	border-color: rgb(255 255 255 / 0.12);
	background: rgb(0 0 0 / 0.2);
}

.icon-button svg {
	width: 17px;
	height: 17px;
}

.notification-dot {
	position: absolute;
	right: -2px;
	top: -3px;
	display: grid;
	min-width: 18px;
	height: 18px;
	place-items: center;
	border: 2px solid var(--bone);
	border-radius: 999px;
	background: #c53030;
	color: white;
	font-size: 10px;
	font-weight: 800;
}

.create-button {
	display: flex;
	height: 44px;
	align-items: center;
	gap: 14px;
	border: 0;
	border-radius: 999px;
	background: var(--acid);
	box-shadow: 0 12px 26px -18px rgb(110 130 0 / 0.6);
	color: #111;
	padding: 0 9px 0 18px;
	font-size: 12px;
	font-weight: 750;
}

.create-button svg {
	width: 28px;
	height: 28px;
	border-radius: 50%;
	background: #111;
	color: white;
	padding: 6px;
}

.home-main {
	position: relative;
	z-index: 1;
	margin: 20px auto 0;
	max-width: var(--desktop-content-max);
	padding: 0 22px 30px;
}

.hero-grid {
	display: grid;
	grid-template-columns: minmax(0, 1.8fr) 112px minmax(300px, 0.95fr);
	gap: 16px;
	align-items: stretch;
}

.current-project-card {
	position: relative;
	display: grid;
	min-height: 394px;
	grid-template-columns: minmax(0, 1fr) minmax(260px, 0.9fr);
	grid-template-rows: 1fr auto;
	gap: 16px 30px;
	border: 1px solid rgb(255 255 255 / 0.1);
	border-radius: 28px;
	background:
		linear-gradient(145deg, rgb(255 255 255 / 0.035), transparent 45%),
		var(--graphite-soft);
	box-shadow: 0 32px 80px -54px rgb(0 0 0 / 0.88);
	color: #f4f5ef;
	padding: 30px 36px 24px;
}

:global(.dark .current-project-card) {
	border-color: rgb(16 19 16 / 0.08);
	background:
		linear-gradient(145deg, rgb(255 255 255 / 0.8), rgb(255 255 255 / 0.16)),
		#e9e8e1;
	box-shadow: 0 34px 86px -58px rgb(0 0 0 / 0.9);
	color: #151815;
}

.current-project-card::after {
	position: absolute;
	right: -45px;
	top: 50%;
	bottom: auto;
	z-index: 1;
	width: 100px;
	height: 100px;
	border-radius: 50%;
	background: var(--home-canvas);
	content: "";
	transform: translateY(-50%);
}

.project-copy {
	position: relative;
	z-index: 2;
}

.project-copy > p {
	margin: 0 0 16px;
	color: rgb(244 245 239 / 0.74);
	font-size: 12px;
	font-weight: 650;
}

:global(.dark .project-copy > p) {
	color: rgb(21 24 21 / 0.76);
}

.project-copy h1 {
	max-width: 570px;
	margin: 0;
	font-size: clamp(26px, 2.25vw, 39px);
	font-weight: 780;
	letter-spacing: -0.02em;
	line-height: 1.18;
}

.project-progress {
	display: grid;
	max-width: 430px;
	gap: 16px;
	margin-top: 30px;
}

.status-word {
	font-size: clamp(30px, 3vw, 46px);
	font-weight: 600;
	letter-spacing: -0.02em;
	line-height: 1.15;
}

.status-word[data-status="failed"] {
	color: #f0a294;
}

:global(.dark .status-word[data-status="failed"]) {
	color: #b3402f;
}

.status-meta {
	color: rgb(244 245 239 / 0.74);
	font-size: 12px;
}

:global(.dark .status-meta) {
	color: rgb(21 24 21 / 0.72);
}

.project-graphic {
	display: grid;
	align-items: center;
	min-width: 0;
}

.project-graphic svg {
	width: 100%;
	max-height: 210px;
	overflow: visible;
}

.graphic-grid path {
	fill: none;
	stroke: currentColor;
	stroke-width: 1;
	opacity: 0.11;
}

.model-solid path {
	fill: rgb(255 255 255 / 0.09);
	stroke: currentColor;
	stroke-width: 1.2;
	opacity: 0.76;
}

:global(.dark .model-solid path) {
	fill: rgb(20 24 20 / 0.08);
}

.model-solid circle {
	fill: currentColor;
	opacity: 0.5;
}

.workflow-line {
	position: relative;
	z-index: 2;
	display: flex;
	grid-column: 1 / -1;
	gap: 8px;
	margin-top: 24px;
	overflow-x: auto;
	border: 1px solid rgb(255 255 255 / 0.11);
	border-radius: 22px;
	padding: 13px 16px;
}

:global(.dark .workflow-line) {
	border-color: rgb(18 21 18 / 0.1);
	background: rgb(255 255 255 / 0.18);
}

.workflow-chip {
	display: inline-flex;
	flex: none;
	align-items: center;
	gap: 8px;
	border: 1px solid rgb(255 255 255 / 0.16);
	border-radius: 999px;
	color: inherit;
	padding: 7px 12px;
	font-size: 12px;
}

.workflow-dot {
	width: 8px;
	height: 8px;
	border-radius: 50%;
	background: currentColor;
	opacity: 0.4;
}

.workflow-chip[data-state="completed"] .workflow-dot {
	background: var(--acid);
	opacity: 1;
}

.workflow-chip-interrupted {
	border-color: rgb(240 162 148 / 0.55);
	background: transparent;
}

.workflow-chip-interrupted .workflow-dot {
	background: #f0a294;
	opacity: 1;
}

.workflow-chip small {
	color: rgb(244 245 239 / 0.72);
	font-size: 11px;
}

:global(.dark .workflow-chip) {
	border-color: rgb(20 24 20 / 0.14);
}

:global(.dark .workflow-chip-interrupted) {
	border-color: rgb(179 64 47 / 0.5);
	background: transparent;
}

:global(.dark .workflow-chip-interrupted .workflow-dot) {
	background: #b3402f;
	opacity: 1;
}

:global(.dark .workflow-chip small) {
	color: rgb(21 24 21 / 0.72);
}

.workflow-overflow {
	flex: none;
	align-self: center;
	color: rgb(244 245 239 / 0.72);
	font-size: 12px;
}

:global(.dark .workflow-overflow) {
	color: rgb(21 24 21 / 0.72);
}

/* 焦点环随表面反转：浅色主题的 hero 是深面、暗色主题的卡面是浅面，
   固定环色总有一侧低于 3:1。 */
.current-project-card :focus-visible {
	outline-color: #ecebe5;
}

:global(.dark .current-project-card :focus-visible),
:global(.dark .solid-card :focus-visible),
:global(.dark .review-card :focus-visible) {
	outline-color: #161916;
}

.agent-card :focus-visible {
	outline-color: #ecebe5;
}

.continue-action {
	position: absolute;
	right: -31px;
	top: 50%;
	bottom: auto;
	z-index: 4;
	display: grid;
	width: 78px;
	height: 78px;
	grid-template-rows: auto auto;
	place-items: center;
	align-content: center;
	gap: 7px;
	border-radius: 50%;
	background: var(--acid);
	box-shadow:
		0 0 0 7px var(--graphite),
		0 14px 28px -18px rgb(101 122 0 / 0.5);
	color: #111;
	font-size: 11.5px;
	font-weight: 760;
	text-align: center;
	text-decoration: none;
	transform: translateY(-50%);
	transition: transform 180ms ease;
}

:global(.dark .continue-action) {
	background: #151815;
	box-shadow:
		0 0 0 7px #ecebe5,
		0 14px 28px -18px rgb(0 0 0 / 0.55);
	color: var(--acid);
}

.continue-action:hover {
	transform: translate(3px, -50%);
}

.continue-action svg {
	width: 18px;
	height: 18px;
	margin-top: 0;
}

.quick-actions {
	display: flex;
	flex-direction: column;
	align-items: center;
	justify-content: center;
	gap: 24px;
}

.quick-orb {
	display: grid;
	width: 92px;
	height: 92px;
	place-items: center;
	border-radius: 50%;
	color: var(--home-ink);
	padding: 14px 8px;
	font-size: 11.5px;
	font-weight: 680;
}

.quick-orb svg {
	width: 26px;
	height: 26px;
}

.soft-glass {
	border: 1px solid rgb(255 255 255 / 0.68);
	background: linear-gradient(145deg, rgb(255 255 255 / 0.66), rgb(247 248 241 / 0.4));
	box-shadow:
		inset 0 1px 0 rgb(255 255 255 / 0.72),
		0 24px 58px -42px rgb(38 52 41 / 0.44);
	backdrop-filter: blur(18px) saturate(1.1);
}

:global(.dark .soft-glass) {
	border-color: rgb(231 255 47 / 0.26);
	background: linear-gradient(160deg, #242a20, #161913);
	box-shadow: 0 28px 64px -44px rgb(0 0 0 / 0.62);
	color: #f2f4ec;
	backdrop-filter: none;
}

.review-card {
	display: flex;
	min-height: 394px;
	flex-direction: column;
	border-radius: 28px;
	padding: 24px;
}

.review-card > header,
.solid-card > header {
	display: flex;
	align-items: center;
	justify-content: space-between;
}

.review-card h2,
.solid-card h2,
.recent-section h2 {
	margin: 0;
	font-size: 16px;
	font-weight: 740;
	letter-spacing: -0.025em;
}

.count-mark {
	display: grid;
	width: 29px;
	height: 29px;
	place-items: center;
	border-radius: 50%;
	background: var(--acid);
	color: #111;
	font-size: 11px;
	font-weight: 800;
}

.review-list {
	display: grid;
	gap: 10px;
	margin-top: 18px;
}

.review-row {
	display: grid;
	min-height: 72px;
	grid-template-columns: 42px minmax(0, 1fr) auto 15px;
	align-items: center;
	gap: 10px;
	border: 1px solid rgb(20 24 20 / 0.07);
	border-radius: 18px;
	background: rgb(255 255 255 / 0.65);
	color: #161916;
	padding: 10px 12px;
	text-decoration: none;
}

:global(.dark .review-row) {
	border-color: rgb(255 255 255 / 0.09);
	background: rgb(255 255 255 / 0.05);
	color: #eef0ea;
}

.review-icon {
	display: grid;
	width: 42px;
	height: 42px;
	place-items: center;
	border-radius: 50%;
	background: rgb(17 19 17 / 0.055);
}

:global(.dark .review-icon) {
	background: rgb(231 255 47 / 0.13);
	color: var(--acid);
}

.review-icon svg {
	width: 19px;
	height: 19px;
}

.review-row strong {
	overflow: hidden;
	font-size: 12px;
	text-overflow: ellipsis;
	white-space: nowrap;
}

.review-row small {
	color: rgb(17 19 17 / 0.62);
	font-size: 11px;
	white-space: nowrap;
}

:global(.dark .review-row small) {
	color: rgb(244 245 239 / 0.62);
}

.review-row > svg {
	width: 14px;
	height: 14px;
}

.review-row:hover {
	border-color: #bed700;
	background: rgb(255 255 255 / 0.85);
}

.review-empty {
	display: grid;
	flex: 1;
	place-items: center;
	align-content: center;
	gap: 12px;
	color: var(--home-muted);
}

:global(.dark .review-empty) {
	color: rgb(244 245 239 / 0.66);
}

.review-empty svg {
	width: 34px;
	height: 34px;
	color: #8ba300;
}

:global(.dark .review-empty svg) {
	color: var(--acid);
}

.review-empty strong {
	font-size: 12px;
}

.review-more,
.resource-card > button {
	display: flex;
	align-items: center;
	justify-content: space-between;
	border: 0;
	background: transparent;
	color: inherit;
	padding: 15px 0 0;
	font-size: 11px;
	font-weight: 680;
}

.review-more {
	margin-top: auto;
}

.review-more svg,
.resource-card > button svg {
	width: 16px;
	height: 16px;
}

.insight-grid {
	display: grid;
	grid-template-columns: minmax(0, 1.35fr) minmax(220px, 0.68fr) minmax(300px, 1fr);
	gap: 16px;
	margin-top: 16px;
}

.solid-card {
	border: 1px solid var(--panel-border);
	border-radius: 24px;
	background: var(--panel);
	box-shadow: 0 24px 58px -46px rgb(31 39 32 / 0.48);
	color: #151815;
}

.overview-card,
.resource-card,
.agent-card {
	min-height: 254px;
	padding: 22px;
}

.overview-card > header > div p {
	margin: 0;
	font-size: 12px;
	font-weight: 700;
}

.overview-card > header > div strong {
	display: block;
	margin-top: 8px;
	font-size: 38px;
	font-weight: 520;
	letter-spacing: -0.06em;
}

.overview-card > header > span {
	border-radius: 999px;
	background: var(--acid);
	padding: 6px 9px;
	font-size: 11px;
	font-weight: 700;
}

.overview-card dl {
	display: grid;
	grid-template-columns: repeat(3, 1fr);
	margin: 5px 0 0;
	border-top: 1px solid rgb(20 24 20 / 0.09);
	padding-top: 12px;
}

.overview-card dl div {
	display: flex;
	align-items: baseline;
	justify-content: center;
	gap: 8px;
	border-right: 1px solid rgb(20 24 20 / 0.09);
}

.overview-card dl div:last-child {
	border-right: 0;
}

.overview-card dt {
	color: #565c55;
	font-size: 11px;
}

.overview-card dd {
	margin: 0;
	font-size: 18px;
	font-weight: 650;
}

.resource-card > header svg {
	width: 18px;
	height: 18px;
	color: #6f756e;
}

.resource-list {
	display: grid;
	gap: 15px;
	margin-top: 24px;
}

.resource-list div {
	display: flex;
	align-items: center;
	justify-content: space-between;
	border-bottom: 1px solid rgb(20 24 20 / 0.08);
	padding-bottom: 10px;
	font-size: 12px;
}

.resource-list span {
	color: #575c55;
}

.resource-list strong {
	font-size: 14px;
}

.agent-card {
	position: relative;
	overflow: hidden;
	background: #2e342c;
	color: #f4f5ef;
}

.agent-card::after {
	position: absolute;
	right: -15%;
	bottom: -48%;
	width: 72%;
	aspect-ratio: 1;
	border: 1px solid rgb(255 255 255 / 0.12);
	border-radius: 50%;
	content: "";
}

.agent-card > header span {
	border: 1px solid rgb(255 255 255 / 0.28);
	border-radius: 999px;
	padding: 5px 8px;
	font-size: 11px;
}

.agent-layout {
	display: grid;
	grid-template-columns: minmax(0, 1fr) 42%;
	gap: 10px;
	margin-top: 18px;
}

.agent-layout ul {
	display: grid;
	gap: 9px;
	margin: 0;
	padding: 0;
	list-style: none;
}

.agent-layout li {
	display: grid;
	grid-template-columns: 17px minmax(0, 1fr) auto;
	align-items: center;
	gap: 8px;
	font-size: 11px;
}

.agent-layout li svg {
	width: 15px;
	height: 15px;
	color: var(--acid);
	opacity: 0.92;
}

.agent-layout li small {
	color: rgb(244 245 239 / 0.72);
	font-size: 11px;
}

.agent-layout {
	grid-template-columns: 1fr;
	gap: 10px;
}

.agent-layout li {
	grid-template-columns: 17px minmax(0, 1fr) auto auto;
}

.agent-state {
	border: 1px solid rgb(255 255 255 / 0.18);
	border-radius: 999px;
	padding: 2px 7px;
	font-size: 11px;
}

.agent-state[data-state="ready"] {
	border-color: rgb(231 255 47 / 0.35);
	color: var(--acid);
}

.agent-state[data-state="unset"] {
	border-color: rgb(240 162 148 / 0.4);
	color: #f0a294;
}

.agent-state[data-state="loading"],
.agent-state[data-state="unknown"] {
	border-color: rgb(255 255 255 / 0.18);
	color: rgb(244 245 239 / 0.72);
}

:global(.dark .agent-state[data-state="unset"]) {
	border-color: rgb(179 64 47 / 0.5);
	color: #b3402f;
}

:global(.dark .agent-state[data-state="loading"]),
:global(.dark .agent-state[data-state="unknown"]) {
	color: rgb(21 24 21 / 0.62);
}

.recent-section {
	margin-top: 16px;
	padding: 18px 20px 20px;
}

.recent-section > header > div:first-child {
	display: flex;
	align-items: center;
	gap: 10px;
}

.recent-section > header > div:first-child > span {
	display: grid;
	width: 24px;
	height: 24px;
	place-items: center;
	border-radius: 50%;
	background: rgb(20 24 20 / 0.06);
	font-size: 11px;
}

.recent-controls {
	display: flex;
	gap: 4px;
}

.recent-controls button {
	display: flex;
	height: 30px;
	align-items: center;
	gap: 5px;
	border: 0;
	border-radius: 9px;
	background: transparent;
	color: #575c55;
	padding: 0 8px;
	font-size: 11px;
}

.recent-controls button:hover {
	background: rgb(20 24 20 / 0.055);
}

.recent-controls button:disabled {
	opacity: 0.35;
}

.recent-controls svg {
	width: 13px;
	height: 13px;
}

.recent-cards,
.recent-loading {
	display: grid;
	grid-template-columns: repeat(4, minmax(210px, 1fr));
	gap: 12px;
	margin-top: 14px;
}

.recent-project-card {
	min-width: 0;
	border: 1px solid rgb(20 24 20 / 0.08);
	border-radius: 16px;
	background: rgb(255 255 255 / 0.44);
	padding: 13px;
	transition: border-color 160ms ease, transform 160ms ease;
}

.recent-project-card:hover {
	transform: translateY(-2px);
	border-color: #bed700;
}

.recent-card-top {
	display: flex;
	align-items: center;
	justify-content: space-between;
}

.recent-card-top > span {
	border-radius: 999px;
	background: rgb(173 199 0 / 0.13);
	color: #4f5c07;
	padding: 4px 8px;
	font-size: 11px;
	font-weight: 700;
}

.recent-project-card[data-status="completed"] .recent-card-top > span {
	background: var(--acid);
	color: #111;
}

.recent-card-top button {
	display: grid;
	width: 25px;
	height: 25px;
	place-items: center;
	border: 0;
	border-radius: 8px;
	background: transparent;
	color: #575c55;
}

.recent-card-top button:hover {
	background: rgb(219 68 56 / 0.08);
	color: #c84236;
}

.recent-card-top svg {
	width: 13px;
	height: 13px;
}

.recent-project-card > a {
	color: inherit;
	text-decoration: none;
}

.recent-project-card h3 {
	display: -webkit-box;
	overflow: hidden;
	margin: 9px 0 10px;
	font-size: 12px;
	font-weight: 650;
	-webkit-box-orient: vertical;
	-webkit-line-clamp: 2;
}

.recent-hint {
	margin: 0 0 10px;
	color: var(--panel-muted);
	font-size: 11px;
}

.recent-error {
	display: grid;
	place-items: center;
	align-content: center;
	gap: 10px;
	margin-top: 14px;
	border: 1px dashed rgb(20 24 20 / 0.18);
	border-radius: 16px;
	padding: 34px 16px;
	text-align: center;
}

.recent-error strong {
	font-size: 13px;
}

.recent-error span {
	color: var(--panel-muted);
	font-size: 12px;
}

.recent-error button {
	border: 0;
	border-radius: 999px;
	background: var(--graphite);
	color: #f4f3ec;
	padding: 9px 18px;
	font-size: 12px;
	font-weight: 650;
}

.recent-error button:hover {
	background: var(--graphite-soft);
}

.recent-project-card footer {
	display: grid;
	grid-template-columns: 1fr auto;
	align-items: center;
	gap: 6px;
	margin-top: 12px;
	color: #575c55;
	font-size: 11px;
}

.recent-project-card footer small {
	font-size: 11px;
	text-align: right;
}

.recent-project-card footer svg {
	width: 13px;
	height: 13px;
}

.recent-loading span {
	height: 128px;
	border-radius: 16px;
	background: rgb(20 24 20 / 0.06);
	animation: loading-pulse 1.4s ease-in-out infinite alternate;
}

.recent-empty {
	display: flex;
	min-height: 126px;
	align-items: center;
	justify-content: center;
	gap: 12px;
	color: #747a73;
}

.recent-empty svg {
	width: 22px;
	height: 22px;
}

.recent-empty strong {
	font-size: 11px;
}

.recent-empty button {
	border: 0;
	border-radius: 999px;
	background: var(--acid);
	color: #111;
	padding: 7px 12px;
	font-size: 11px;
	font-weight: 700;
}

@keyframes loading-pulse {
	to {
		opacity: 0.45;
	}
}

@media (max-width: 1320px) {
	.service-status {
		display: none !important;
	}

	.hero-grid {
		grid-template-columns: minmax(0, 1.6fr) 96px minmax(280px, 0.9fr);
	}

	.current-project-card {
		grid-template-columns: 1fr;
	}

	.project-graphic {
		position: absolute;
		right: 24px;
		top: 30px;
		width: 46%;
		opacity: 0.55;
	}
}

@media (max-width: 1120px) {
	.home-sidebar {
		display: none;
	}

	.home-page {
		margin-left: 0;
	}

	.mobile-brand {
		display: flex;
		align-items: center;
		gap: 8px;
		color: inherit;
		font-size: 15px;
		font-weight: 750;
		text-decoration: none;
	}

	.mobile-brand img {
		width: 26px;
		height: 26px;
	}

	.page-heading strong {
		font-size: 20px;
	}

	.hero-grid {
		grid-template-columns: minmax(0, 1fr) 92px minmax(280px, 0.8fr);
	}

	.insight-grid {
		grid-template-columns: 1fr 0.7fr;
	}

	.agent-card {
		grid-column: 1 / -1;
	}

	.recent-cards,
	.recent-loading {
		overflow-x: auto;
		grid-template-columns: repeat(4, minmax(240px, 1fr));
		padding-bottom: 4px;
	}
}

@media (max-width: 900px) {
	.liquid-nav {
		width: calc(100% - 24px);
		height: 64px;
		margin: 10px auto 0;
		border-radius: 22px;
		padding-inline: 14px;
	}

	.page-heading,
	.nav-search,
	.nav-actions > button[aria-label="模型设置"] {
		display: none;
	}

	.nav-actions {
		margin-left: auto;
	}

	.home-main {
		margin-top: 12px;
		padding: 0 12px 24px;
	}

	.hero-grid {
		grid-template-columns: 1fr;
	}

	.current-project-card,
	.review-card {
		min-height: 360px;
	}

	.current-project-card::after {
		right: -24px;
	}

	.continue-action {
		right: 8px;
	}

	.quick-actions {
		position: fixed;
		right: 16px;
		bottom: 16px;
		z-index: 35;
		flex-direction: row;
	}

	.quick-orb {
		width: 58px;
		height: 58px;
		padding: 10px;
	}

	.quick-orb span {
		display: none;
	}

	.review-card {
		min-height: 300px;
	}
}

@media (max-width: 680px) {
	.create-button span,
	.nav-actions > button[aria-label="模型设置"] {
		display: none;
	}

	.create-button {
		width: 42px;
		padding: 0 7px;
	}

	.project-graphic {
		display: none;
	}

	.current-project-card {
		min-height: 420px;
		grid-template-columns: 1fr;
		padding: 24px 22px;
	}

	.current-project-card::after {
		display: none;
	}

	.continue-action {
		right: 18px;
		top: auto;
		bottom: 20px;
		width: 68px;
		height: 68px;
		box-shadow: 0 15px 32px -18px rgb(0 0 0 / 0.55);
		transform: none;
	}

	.continue-action:hover {
		transform: translateX(3px);
	}

	.insight-grid {
		grid-template-columns: 1fr;
	}

	.agent-card {
		grid-column: auto;
	}

	.recent-section {
		padding-inline: 14px;
	}
}

@media (prefers-reduced-motion: reduce) {
	.liquid-surface::after,
	.recent-loading span,
	.recent-project-card,
	.continue-action {
		animation: none;
		transition: none;
	}
}
</style>
