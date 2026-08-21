<script setup lang="ts">
import type { TaskSummary } from "@/apis/commonApi";
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
import { isAxiosError } from "axios";
import {
	AlertCircle,
	BarChart3,
	Bell,
	Bot,
	Box,
	CheckCircle2,
	ChevronRight,
	CircleDot,
	Command,
	Database,
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
	Sparkles,
	Trash2,
} from "lucide-vue-next";
import { computed, onMounted, ref } from "vue";
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

const statusConfig: Record<
	TaskSummary["status"],
	{
		label: string;
		stage: string;
		progress: number;
		activeStep: number;
	}
> = {
	running: {
		label: "运行中",
		stage: "模型设计",
		progress: 62,
		activeStep: 2,
	},
	awaiting_approval: {
		label: "待确认",
		stage: "结果分析",
		progress: 78,
		activeStep: 3,
	},
	completed: {
		label: "已完成",
		stage: "结果交付",
		progress: 100,
		activeStep: 3,
	},
	failed: {
		label: "需处理",
		stage: "执行异常",
		progress: 52,
		activeStep: 2,
	},
	stopped: {
		label: "已暂停",
		stage: "等待继续",
		progress: 46,
		activeStep: 1,
	},
};

const workflowSteps = [
	{ label: "题目理解", icon: FileText },
	{ label: "数据处理", icon: Database },
	{ label: "模型设计", icon: Network },
	{ label: "结果分析", icon: BarChart3 },
];

const recentTask = computed(() => taskStore.taskHistory[0] ?? null);
const recentTasks = computed(() => taskStore.taskHistory.slice(0, 4));
const reviewTasks = computed(() =>
	taskStore.taskHistory
		.filter((task) => task.status === "awaiting_approval")
		.slice(0, 3),
);
const runningCount = computed(
	() =>
		taskStore.taskHistory.filter((task) => task.status === "running").length,
);
const approvalCount = computed(() => reviewTasks.value.length);
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
const currentProgress = computed(() =>
	recentTask.value ? statusConfig[recentTask.value.status].progress : 0,
);
const currentStep = computed(() =>
	recentTask.value ? statusConfig[recentTask.value.status].activeStep : -1,
);
const activityPolyline = computed(() => {
	const tasks = [...recentTasks.value].reverse();
	if (tasks.length < 2) return "0,58 260,58";
	const values = tasks.map((task) => Math.max(task.message_count, 1));
	const max = Math.max(...values);
	return values
		.map((value, index) => {
			const x = (index / (values.length - 1)) * 260;
			const y = 68 - (value / max) * 52;
			return `${x.toFixed(1)},${y.toFixed(1)}`;
		})
		.join(" ");
});

function workflowState(index: number): "done" | "active" | "idle" {
	if (!recentTask.value) return "idle";
	if (recentTask.value.status === "completed" || index < currentStep.value) {
		return "done";
	}
	if (index === currentStep.value) return "active";
	return "idle";
}

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
		toast({ title: "项目已删除", description: `“${task.title}”已永久删除。` });
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

			<nav class="sidebar-nav">
				<RouterLink to="/home" class="sidebar-item sidebar-active">
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
					<small>已连接</small>
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
					<span>V6</span>
				</div>

				<button type="button" class="nav-search" @click="commandPaletteOpen = true">
					<Search aria-hidden="true" />
					<span>搜索项目、数据集或命令</span>
					<kbd>⌘K</kbd>
				</button>

				<div class="nav-actions">
					<ServiceStatus class="service-status" />
					<ThemeToggle />
					<button
						type="button"
						class="icon-button"
						aria-label="待确认事项"
						@click="commandPaletteOpen = true"
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
					<button type="button" class="create-button" @click="createProjectOpen = true">
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
							<h1>{{ recentTask?.title || "创建第一个建模项目" }}</h1>
							<div class="project-progress">
								<strong class="mono-data">{{ currentProgress }}%</strong>
								<div class="progress-track" aria-hidden="true">
									<span :style="{ width: `${currentProgress}%` }" />
								</div>
								<span>{{ recentTask ? statusConfig[recentTask.status].label : "尚未开始" }}</span>
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
								<polyline class="project-line" points="214,142 254,116 290,124 326,84 360,98 400,52" />
								<g class="project-points">
									<circle cx="214" cy="142" r="5" />
									<circle cx="254" cy="116" r="5" />
									<circle cx="290" cy="124" r="5" />
									<circle cx="326" cy="84" r="5" />
									<circle cx="360" cy="98" r="5" />
									<circle cx="400" cy="52" r="5" />
								</g>
							</svg>
						</div>

						<div class="workflow-line">
							<div
								v-for="(step, index) in workflowSteps"
								:key="step.label"
								class="workflow-step"
								:data-state="workflowState(index)"
							>
								<span class="workflow-icon"><component :is="step.icon" aria-hidden="true" /></span>
								<strong>{{ step.label }}</strong>
								<small>{{ workflowState(index) === "done" ? "完成" : workflowState(index) === "active" ? "进行中" : "待开始" }}</small>
							</div>
						</div>

						<RouterLink
							v-if="recentTask"
							:to="`/project/${recentTask.task_id}/overview`"
							class="continue-action"
						>
							<span>继续建模</span>
							<ChevronRight aria-hidden="true" />
						</RouterLink>
						<button v-else type="button" class="continue-action" @click="createProjectOpen = true">
							<span>开始建模</span>
							<ChevronRight aria-hidden="true" />
						</button>
					</article>

					<div class="quick-actions" aria-label="快捷操作">
						<button type="button" class="quick-orb liquid-surface" @click="createProjectOpen = true">
							<Database aria-hidden="true" />
							<span>新建数据集</span>
						</button>
						<button type="button" class="quick-orb liquid-surface" @click="commandPaletteOpen = true">
							<Sparkles aria-hidden="true" />
							<span>快速运行</span>
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
								<strong>{{ task.title }}</strong>
								<small>{{ formatTaskTime(task.updated_at) }}</small>
								<ChevronRight aria-hidden="true" />
							</RouterLink>
						</div>
						<div v-else class="review-empty">
							<CheckCircle2 aria-hidden="true" />
							<strong>暂无待确认</strong>
						</div>
						<button type="button" class="review-more" @click="commandPaletteOpen = true">
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
						<svg viewBox="0 0 260 80" preserveAspectRatio="none" aria-label="最近项目消息量趋势">
							<polyline :points="activityPolyline" />
						</svg>
						<dl>
							<div><dt>运行</dt><dd class="mono-data">{{ runningCount }}</dd></div>
							<div><dt>待确认</dt><dd class="mono-data">{{ approvalCount }}</dd></div>
							<div><dt>完成</dt><dd class="mono-data">{{ completedCount }}</dd></div>
						</dl>
					</article>

					<article class="resource-card solid-card">
						<header>
							<h2>运行状态</h2>
							<Gauge aria-hidden="true" />
						</header>
						<div class="resource-list">
							<div><span>进行中</span><strong class="mono-data">{{ runningCount }}</strong></div>
							<div><span>人工节点</span><strong class="mono-data">{{ approvalCount }}</strong></div>
							<div><span>历史项目</span><strong class="mono-data">{{ taskStore.taskHistory.length }}</strong></div>
						</div>
						<button type="button" @click="settingsOpen = true">
							<span>模型连接</span>
							<ChevronRight aria-hidden="true" />
						</button>
					</article>

					<article class="agent-card solid-card">
						<header>
							<h2>Agent 协作链</h2>
							<span>{{ recentTask ? "已连接" : "待命" }}</span>
						</header>
						<div class="agent-layout">
							<ul>
								<li><CircleDot aria-hidden="true" /><span>Coordinator</span><small>编排</small></li>
								<li><Network aria-hidden="true" /><span>Modeler</span><small>建模</small></li>
								<li><Command aria-hidden="true" /><span>Coder</span><small>计算</small></li>
								<li><FileText aria-hidden="true" /><span>Writer</span><small>写作</small></li>
							</ul>
							<div class="agent-visual" aria-hidden="true">
								<Box />
								<span v-for="index in 6" :key="index" :style="{ '--i': index }" />
							</div>
						</div>
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
					<div v-else-if="recentTasks.length" class="recent-cards">
						<article v-for="task in recentTasks" :key="task.task_id" class="recent-project-card" :data-status="task.status">
							<div class="recent-card-top">
								<span>{{ statusConfig[task.status].label }}</span>
								<button
									type="button"
									:aria-label="`删除项目：${task.title}`"
									@click="taskPendingDelete = task"
								>
									<Trash2 aria-hidden="true" />
								</button>
							</div>
							<RouterLink :to="`/project/${task.task_id}/overview`">
								<h3>{{ task.title }}</h3>
								<div class="recent-progress">
									<strong class="mono-data">{{ statusConfig[task.status].progress }}%</strong>
									<span><i :style="{ width: `${statusConfig[task.status].progress}%` }" /></span>
								</div>
								<footer>
									<span>{{ statusConfig[task.status].stage }}</span>
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
			:tasks="taskStore.taskHistory"
			@new-project="createProjectOpen = true"
			@settings="settingsOpen = true"
		/>

		<Dialog :open="taskPendingDelete !== null" @update:open="handleDeleteDialogOpen">
			<DialogContent class="max-w-md">
				<DialogHeader>
					<DialogTitle>永久删除这个项目？</DialogTitle>
					<DialogDescription class="pt-1 leading-6">
						<span class="block font-medium text-foreground">{{ taskPendingDelete?.title }}</span>
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
	--sage: #8f998d;
	--home-canvas: #f4f3ec;
	--page-cut: #f4f3ec;
	--home-ink: #111310;
	--home-muted: #6d726c;
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
	color: rgb(255 255 255 / 0.48);
	font-size: 10px;
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
	overflow: hidden;
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
	letter-spacing: -0.055em;
}

.page-heading > span {
	border: 1px solid rgb(154 179 0 / 0.64);
	border-radius: 9px;
	color: #879e00;
	padding: 4px 7px;
	font-family: ui-monospace, monospace;
	font-size: 12px;
	font-weight: 700;
}

:global(.dark .page-heading > span) {
	color: var(--acid);
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
	font-size: 11px;
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
	opacity: 0.55;
	padding: 2px 5px;
	font-size: 9px;
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
	background: #ef4939;
	color: white;
	font-size: 9px;
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
	box-shadow: 0 14px 32px -20px rgb(143 167 0 / 0.9);
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
	color: rgb(244 245 239 / 0.64);
	font-size: 12px;
	font-weight: 650;
}

:global(.dark .project-copy > p) {
	color: rgb(21 24 21 / 0.56);
}

.project-copy h1 {
	max-width: 570px;
	margin: 0;
	font-size: clamp(26px, 2.25vw, 39px);
	font-weight: 780;
	letter-spacing: -0.055em;
	line-height: 1.15;
}

.project-progress {
	display: grid;
	max-width: 430px;
	grid-template-columns: auto 1fr auto;
	align-items: center;
	gap: 14px;
	margin-top: 28px;
}

.project-progress strong {
	font-size: clamp(30px, 3vw, 48px);
	font-weight: 560;
	letter-spacing: -0.07em;
}

.project-progress > span {
	color: rgb(244 245 239 / 0.52);
	font-size: 10px;
}

:global(.dark .project-progress > span) {
	color: rgb(21 24 21 / 0.48);
}

.progress-track {
	height: 6px;
	overflow: hidden;
	border-radius: 999px;
	background: rgb(0 0 0 / 0.52);
}

:global(.dark .progress-track) {
	background: rgb(20 22 20 / 0.14);
}

.progress-track span {
	display: block;
	height: 100%;
	border-radius: inherit;
	background: var(--acid);
	transition: width 500ms ease;
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

.project-line {
	fill: none;
	stroke: var(--acid);
	stroke-width: 2.2;
}

.project-points circle {
	fill: var(--acid);
	stroke: #111;
	stroke-width: 2;
}

.workflow-line {
	position: relative;
	z-index: 2;
	display: grid;
	grid-column: 1 / -1;
	grid-template-columns: repeat(4, minmax(0, 1fr));
	gap: 12px;
	border: 1px solid rgb(255 255 255 / 0.11);
	border-radius: 22px;
	padding: 15px 18px;
}

:global(.dark .workflow-line) {
	border-color: rgb(18 21 18 / 0.1);
	background: rgb(255 255 255 / 0.18);
}

.workflow-step {
	position: relative;
	display: grid;
	grid-template-columns: 42px minmax(0, 1fr);
	grid-template-rows: auto auto;
	column-gap: 10px;
	align-items: center;
}

.workflow-step:not(:last-child)::after {
	position: absolute;
	right: -3px;
	top: 20px;
	width: 20%;
	height: 1px;
	background: currentColor;
	content: "";
	opacity: 0.22;
}

.workflow-step[data-state="done"]::after,
.workflow-step[data-state="active"]::after {
	background: var(--acid);
	opacity: 0.9;
}

.workflow-icon {
	display: grid;
	grid-row: 1 / 3;
	width: 42px;
	height: 42px;
	place-items: center;
	border: 1px solid currentColor;
	border-radius: 50%;
	opacity: 0.54;
}

.workflow-icon svg {
	width: 20px;
	height: 20px;
}

.workflow-step[data-state="active"] .workflow-icon {
	border-color: var(--acid);
	background: var(--acid);
	color: #111;
	opacity: 1;
}

.workflow-step[data-state="done"] .workflow-icon {
	border-color: var(--acid);
	opacity: 1;
}

.workflow-step strong {
	font-size: 11px;
	font-weight: 680;
}

.workflow-step small {
	margin-top: 3px;
	opacity: 0.48;
	font-size: 9px;
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
	border: 1px solid rgb(255 255 255 / 0.35);
	border-radius: 50%;
	background: var(--acid);
	box-shadow:
		0 0 0 7px var(--graphite),
		0 18px 36px -20px rgb(101 122 0 / 0.72);
	color: #111;
	font-size: 10px;
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
		0 18px 36px -22px rgb(0 0 0 / 0.72);
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
	font-size: 10px;
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
	border-color: rgb(255 255 255 / 0.06);
	background: var(--acid);
	box-shadow: 0 28px 64px -44px rgb(194 225 0 / 0.36);
	color: #111;
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

:global(.dark .count-mark) {
	background: rgb(17 19 16 / 0.1);
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
	background: rgb(255 255 255 / 0.82);
}

.review-icon {
	display: grid;
	width: 42px;
	height: 42px;
	place-items: center;
	border-radius: 50%;
	background: rgb(17 19 17 / 0.055);
}

.review-icon svg {
	width: 19px;
	height: 19px;
}

.review-row strong {
	overflow: hidden;
	font-size: 11px;
	text-overflow: ellipsis;
	white-space: nowrap;
}

.review-row small {
	color: rgb(17 19 17 / 0.56);
	font-size: 9px;
	white-space: nowrap;
}

.review-row > svg {
	width: 14px;
	height: 14px;
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
	color: #30342f;
}

.review-empty svg {
	width: 34px;
	height: 34px;
	color: #8ba300;
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
	font-size: 9px;
	font-weight: 700;
}

.overview-card svg {
	width: 100%;
	height: 70px;
	margin-top: 8px;
	overflow: visible;
}

.overview-card polyline {
	fill: none;
	stroke: #b1cc00;
	stroke-linecap: round;
	stroke-linejoin: round;
	stroke-width: 2.4;
	filter: drop-shadow(0 5px 8px rgb(174 202 0 / 0.2));
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
	color: #777d76;
	font-size: 9px;
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
	font-size: 10px;
}

.resource-list span {
	color: #717770;
}

.resource-list strong {
	font-size: 14px;
}

.agent-card {
	position: relative;
	overflow: hidden;
	background: #879185;
	color: white;
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
	border: 1px solid rgb(255 255 255 / 0.2);
	border-radius: 999px;
	padding: 5px 8px;
	font-size: 9px;
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
	font-size: 10px;
}

.agent-layout li svg {
	width: 15px;
	height: 15px;
	opacity: 0.74;
}

.agent-layout li small {
	opacity: 0.56;
	font-size: 8px;
}

.agent-visual {
	position: relative;
	display: grid;
	place-items: center;
}

.agent-visual > svg {
	position: relative;
	z-index: 2;
	width: 58px;
	height: 58px;
	filter: drop-shadow(0 14px 18px rgb(20 25 21 / 0.28));
	stroke-width: 1;
}

.agent-visual span {
	--angle: calc(var(--i) * 60deg);
	position: absolute;
	left: calc(50% + cos(var(--angle)) * 54px);
	top: calc(50% + sin(var(--angle)) * 54px);
	width: 8px;
	height: 8px;
	border: 1px solid rgb(255 255 255 / 0.7);
	border-radius: 50%;
	background: rgb(255 255 255 / 0.58);
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
	font-size: 9px;
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
	color: #697068;
	padding: 0 8px;
	font-size: 9px;
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
	color: #6f8100;
	padding: 4px 7px;
	font-size: 8px;
	font-weight: 700;
}

.recent-card-top button {
	display: grid;
	width: 25px;
	height: 25px;
	place-items: center;
	border: 0;
	border-radius: 8px;
	background: transparent;
	color: #8a8f89;
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
	overflow: hidden;
	margin: 9px 0 12px;
	font-size: 11px;
	font-weight: 650;
	text-overflow: ellipsis;
	white-space: nowrap;
}

.recent-progress {
	display: grid;
	grid-template-columns: auto 1fr;
	align-items: center;
	gap: 9px;
}

.recent-progress strong {
	font-size: 20px;
	font-weight: 520;
}

.recent-progress > span {
	height: 4px;
	overflow: hidden;
	border-radius: 999px;
	background: rgb(20 24 20 / 0.1);
}

.recent-progress i {
	display: block;
	height: 100%;
	border-radius: inherit;
	background: #b5cf00;
}

.recent-project-card footer {
	display: grid;
	grid-template-columns: auto 1fr 14px;
	align-items: center;
	gap: 6px;
	margin-top: 12px;
	color: #747a73;
	font-size: 8px;
}

.recent-project-card footer small {
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
	padding: 7px 10px;
	font-size: 9px;
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
	.nav-actions > .icon-button:nth-of-type(2) {
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
	.nav-actions > .icon-button,
	.workflow-step small,
	.workflow-step::after {
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

	.workflow-line {
		grid-template-columns: repeat(2, 1fr);
		padding: 12px;
	}

	.workflow-step {
		grid-template-columns: 36px 1fr;
	}

	.workflow-icon {
		width: 36px;
		height: 36px;
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
	.recent-loading span {
		animation: none;
		transition: none;
	}
}
</style>
