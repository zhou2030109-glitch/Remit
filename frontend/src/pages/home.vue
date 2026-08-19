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
	Bell,
	CheckCircle2,
	ChevronRight,
	CircleDot,
	Clock3,
	Command,
	FileText,
	FolderKanban,
	Gauge,
	History,
	Home,
	KeyRound,
	LoaderCircle,
	PauseCircle,
	Plus,
	Search,
	Settings2,
	Trash2,
	UploadCloud,
	XCircle,
} from "lucide-vue-next";
import { computed, onMounted, ref } from "vue";
import { RouterLink, useRoute } from "vue-router";

// ---- State ----

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

// ---- Status configuration ----

const statusConfig: Record<
	TaskSummary["status"],
	{
		label: string;
		stage: string;
		badge: string;
		dot: string;
		icon: typeof CircleDot;
	}
> = {
	running: {
		label: "正在运行",
		stage: "Agent 执行中",
		badge: "bg-[hsl(var(--accent))] text-[hsl(var(--info))]",
		dot: "bg-[hsl(var(--info))]",
		icon: LoaderCircle,
	},
	awaiting_approval: {
		label: "等待确认",
		stage: "人工审核闸门",
		badge: "bg-[hsl(var(--warning-subtle))] text-[hsl(var(--warning))]",
		dot: "bg-[hsl(var(--warning))]",
		icon: AlertCircle,
	},
	completed: {
		label: "已完成",
		stage: "全部流程完成",
		badge: "bg-[hsl(var(--success-subtle))] text-[hsl(var(--success))]",
		dot: "bg-[hsl(var(--success))]",
		icon: CheckCircle2,
	},
	failed: {
		label: "执行失败",
		stage: "需要检查错误",
		badge: "bg-red-50 text-[hsl(var(--danger))] dark:bg-red-950/30",
		dot: "bg-[hsl(var(--danger))]",
		icon: XCircle,
	},
	stopped: {
		label: "已暂停",
		stage: "可从节点续跑",
		badge: "bg-[hsl(var(--warning-subtle))] text-[hsl(var(--warning))]",
		dot: "bg-[hsl(var(--warning))]",
		icon: PauseCircle,
	},
};

// ---- Computed ----

const recentTask = computed(() => taskStore.taskHistory[0] ?? null);
const recentTasks = computed(() => taskStore.taskHistory.slice(0, 8));
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
const greeting = computed(() => {
	const hour = new Date().getHours();
	if (hour < 6) return "夜深了";
	if (hour < 12) return "早上好";
	if (hour < 18) return "下午好";
	return "晚上好";
});

// ---- Methods ----

const formatTaskTime = (value: string | number) => {
	const date = new Date(value);
	if (Number.isNaN(date.getTime())) return "时间未知";
	const delta = Date.now() - date.getTime();
	const minute = 60_000;
	const hour = 60 * minute;
	const day = 24 * hour;
	if (delta < minute) return "刚刚更新";
	if (delta < hour) return `${Math.floor(delta / minute)} 分钟前`;
	if (delta < day) return `${Math.floor(delta / hour)} 小时前`;
	return new Intl.DateTimeFormat("zh-CN", {
		month: "2-digit",
		day: "2-digit",
		hour: "2-digit",
		minute: "2-digit",
		hour12: false,
	}).format(date);
};

function handleDeleteDialogOpen(open: boolean) {
	if (!open && !isDeleting.value) taskPendingDelete.value = null;
}

async function confirmTaskDeletion() {
	const task = taskPendingDelete.value;
	if (!task || isDeleting.value) return;

	isDeleting.value = true;
	try {
		await taskStore.deleteTask(task.task_id);
		taskPendingDelete.value = null;
		toast({
			title: "历史项目已删除",
			description: `“${task.title}”及其消息、附件和生成文件已永久删除。`,
		});
	} catch (error) {
		const responseData = isAxiosError(error)
			? (error.response?.data as { detail?: string } | undefined)
			: undefined;
		toast({
			variant: "destructive",
			title: "无法删除历史项目",
			description: responseData?.detail || "删除失败，请稍后重试。",
		});
	} finally {
		isDeleting.value = false;
	}
}

function handleClearHistoryDialogOpen(open: boolean) {
	if (!isClearingHistory.value) clearHistoryDialogOpen.value = open;
}

async function confirmClearHistory() {
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
			title: "历史项目已清空",
			description: `已永久删除 ${result.deleted_count} 条历史项目及其生成文件。`,
		});
	} catch (error) {
		const responseData = isAxiosError(error)
			? (error.response?.data as { detail?: string } | undefined)
			: undefined;
		toast({
			variant: "destructive",
			title: "无法清空历史项目",
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
  <div class="min-h-screen bg-background">
    <a
      href="#home-main"
      class="sr-only z-[100] rounded-md bg-primary px-3 py-2 text-primary-foreground focus:not-sr-only focus:fixed focus:left-3 focus:top-3"
    >
      跳到主要内容
    </a>

    <header class="app-glass fixed inset-x-0 top-0 z-40 flex h-[var(--app-header-height)] items-center border-x-0 border-t-0">
      <RouterLink to="/home" class="flex h-full w-[var(--app-sidebar-width)] shrink-0 items-center gap-2.5 border-r px-4" aria-label="Remit 主页">
        <img src="@/assets/remit-icon.png" alt="" class="h-8 w-8" />
        <span class="hidden text-sm font-semibold tracking-tight sm:inline">Remit</span>
      </RouterLink>

      <div class="flex min-w-0 flex-1 items-center gap-3 px-3 sm:px-4">
        <Button
          type="button"
          variant="outline"
          class="hidden h-8 w-full max-w-md justify-start gap-2 bg-background/70 text-xs font-normal text-muted-foreground shadow-none md:flex"
          @click="commandPaletteOpen = true"
        >
          <Search class="h-3.5 w-3.5" aria-hidden="true" />
          搜索项目或执行命令
          <kbd class="ml-auto rounded border bg-muted px-1.5 py-0.5 text-[10px]">Ctrl K</kbd>
        </Button>

        <div class="ml-auto flex items-center gap-1">
          <ServiceStatus class="hidden xl:flex" />
          <Button
            type="button"
            variant="ghost"
            size="icon"
            class="relative h-9 w-9 text-muted-foreground"
            aria-label="待确认事项"
            title="待确认事项"
          >
            <Bell class="h-4 w-4" aria-hidden="true" />
            <span v-if="approvalCount" class="absolute right-1.5 top-1.5 h-1.5 w-1.5 rounded-full bg-[hsl(var(--warning))]" />
          </Button>
          <ThemeToggle />
          <Button
            type="button"
            variant="ghost"
            size="icon"
            class="h-9 w-9 text-muted-foreground"
            aria-label="模型与 API 设置"
            title="模型与 API 设置"
            @click="settingsOpen = true"
          >
            <Settings2 class="h-4 w-4" aria-hidden="true" />
          </Button>
          <Button class="ml-1 h-9 gap-2 px-3" @click="createProjectOpen = true">
            <Plus class="h-4 w-4" aria-hidden="true" />
            <span class="hidden sm:inline">新建项目</span>
          </Button>
        </div>
      </div>
    </header>

    <div class="grid min-h-screen pt-[var(--app-header-height)] lg:grid-cols-[var(--app-sidebar-width)_minmax(0,1fr)]">
      <aside class="fixed bottom-0 left-0 top-[var(--app-header-height)] hidden w-[var(--app-sidebar-width)] flex-col border-r bg-[hsl(var(--sidebar-background))] lg:flex">
        <nav class="flex-1 space-y-1 p-3" aria-label="应用导航">
          <RouterLink to="/home" class="flex h-9 items-center gap-3 rounded-md bg-sidebar-accent px-3 text-sm font-medium text-sidebar-accent-foreground">
            <Home class="h-4 w-4" aria-hidden="true" />
            项目主页
          </RouterLink>
          <button type="button" class="flex h-9 w-full items-center gap-3 rounded-md px-3 text-sm text-sidebar-foreground transition-colors hover:bg-sidebar-accent" @click="createProjectOpen = true">
            <Plus class="h-4 w-4" aria-hidden="true" />
            创建项目
          </button>
          <button type="button" class="flex h-9 w-full items-center gap-3 rounded-md px-3 text-sm text-sidebar-foreground transition-colors hover:bg-sidebar-accent" @click="commandPaletteOpen = true">
            <Command class="h-4 w-4" aria-hidden="true" />
            命令面板
            <kbd class="ml-auto text-[10px] text-muted-foreground">⌘K</kbd>
          </button>

          <p class="px-3 pb-1 pt-6 text-[11px] font-medium text-muted-foreground">工作区</p>
          <RouterLink v-if="recentTask" :to="`/project/${recentTask.task_id}/overview`" class="flex h-9 items-center gap-3 rounded-md px-3 text-sm text-sidebar-foreground transition-colors hover:bg-sidebar-accent">
            <FolderKanban class="h-4 w-4" aria-hidden="true" />
            <span class="truncate">最近工作台</span>
          </RouterLink>
        </nav>

        <div class="border-t p-3">
          <button type="button" class="flex h-10 w-full items-center gap-3 rounded-md px-2 text-left transition-colors hover:bg-sidebar-accent" @click="settingsOpen = true">
            <span class="flex h-7 w-7 items-center justify-center rounded-md border bg-card text-muted-foreground">
              <KeyRound class="h-3.5 w-3.5" aria-hidden="true" />
            </span>
            <span class="min-w-0 flex-1">
              <span class="block text-xs font-medium text-sidebar-foreground">模型连接</span>
              <span class="block truncate text-[10px] text-muted-foreground">API 与推理设置</span>
            </span>
          </button>
        </div>
      </aside>

      <main id="home-main" class="min-w-0 lg:col-start-2">
        <div class="mx-auto max-w-[1480px] px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
          <div class="flex flex-col gap-4 border-b pb-6 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <p class="text-xs font-medium text-muted-foreground">项目控制台</p>
              <h1 class="mt-1 text-2xl font-semibold tracking-tight">{{ greeting }}，继续你的建模工作</h1>
              <p class="mt-1.5 text-sm text-muted-foreground">运行状态、人工审核和最近产物集中在这里。</p>
            </div>
            <div class="flex flex-wrap items-center gap-2 text-xs">
              <span class="inline-flex items-center gap-1.5 rounded-md border bg-card px-2.5 py-1.5">
                <CircleDot class="h-3.5 w-3.5 text-[hsl(var(--info))]" aria-hidden="true" />
                <strong class="mono-data">{{ runningCount }}</strong> 个运行中
              </span>
              <span class="inline-flex items-center gap-1.5 rounded-md border bg-card px-2.5 py-1.5">
                <AlertCircle class="h-3.5 w-3.5 text-[hsl(var(--warning))]" aria-hidden="true" />
                <strong class="mono-data">{{ approvalCount }}</strong> 个待确认
              </span>
              <span class="inline-flex items-center gap-1.5 rounded-md border bg-card px-2.5 py-1.5">
                <CheckCircle2 class="h-3.5 w-3.5 text-[hsl(var(--success))]" aria-hidden="true" />
                <strong class="mono-data">{{ completedCount }}</strong> 个已完成
              </span>
            </div>
          </div>

          <div class="mt-6 grid gap-6 xl:grid-cols-[minmax(0,1fr)_300px]">
            <div class="min-w-0 space-y-6">
              <section aria-labelledby="continue-title">
                <div class="mb-2.5 flex items-center justify-between">
                  <h2 id="continue-title" class="text-sm font-semibold">继续最近项目</h2>
                  <span class="text-xs text-muted-foreground">自动保存</span>
                </div>

                <div v-if="isLoading" class="app-panel h-40 animate-pulse bg-muted/50" aria-label="正在加载最近项目" />
                <RouterLink
                  v-else-if="recentTask"
                  :to="`/project/${recentTask.task_id}/overview`"
                  class="group app-panel block overflow-hidden transition-[border-color,box-shadow] duration-150 hover:border-[hsl(var(--border-strong))] hover:shadow-[var(--shadow-float)]"
                >
                  <div class="grid min-h-40 gap-5 p-5 md:grid-cols-[minmax(0,1fr)_180px] md:items-center">
                    <div class="min-w-0">
                      <div class="flex flex-wrap items-center gap-2">
                        <span class="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[11px] font-medium" :class="statusConfig[recentTask.status].badge">
                          <span class="h-1.5 w-1.5 rounded-full" :class="statusConfig[recentTask.status].dot" />
                          {{ statusConfig[recentTask.status].label }}
                        </span>
                        <span class="text-xs text-muted-foreground">{{ statusConfig[recentTask.status].stage }}</span>
                      </div>
                      <h3 class="mt-3 truncate text-lg font-semibold" :title="recentTask.title">{{ recentTask.title }}</h3>
                      <p class="mt-1 text-xs text-muted-foreground">
                        {{ recentTask.message_count }} 条可追溯记录 · {{ formatTaskTime(recentTask.updated_at) }}
                      </p>
                      <div class="mt-4 flex items-center gap-2 text-xs font-medium text-primary">
                        打开项目工作台
                        <ChevronRight class="h-3.5 w-3.5 transition-transform duration-150 group-hover:translate-x-0.5" aria-hidden="true" />
                      </div>
                    </div>
                    <div class="border-t pt-4 md:border-l md:border-t-0 md:pl-5 md:pt-0">
                      <p class="text-[11px] font-medium text-muted-foreground">当前动作</p>
                      <p class="mt-1.5 text-sm font-medium">
                        {{ recentTask.status === 'awaiting_approval' ? '审核本步成果' : recentTask.status === 'stopped' ? '选择节点继续运行' : recentTask.status === 'failed' ? '查看错误并恢复' : recentTask.status === 'completed' ? '查看论文与产物' : '查看实时执行状态' }}
                      </p>
                      <p class="mt-2 text-xs leading-5 text-muted-foreground">所有已有审核、停止和续跑能力保持可用。</p>
                    </div>
                  </div>
                </RouterLink>
                <div v-else class="app-panel flex min-h-40 items-center justify-between gap-6 p-5">
                  <div>
                    <h3 class="text-sm font-semibold">还没有项目</h3>
                    <p class="mt-1 text-xs leading-5 text-muted-foreground">上传赛题 PDF 和数据集，创建第一个可追踪的建模任务。</p>
                  </div>
                  <Button class="shrink-0 gap-2" @click="createProjectOpen = true">
                    <Plus class="h-4 w-4" aria-hidden="true" />
                    创建项目
                  </Button>
                </div>
              </section>

              <section aria-labelledby="recent-title">
                <div class="mb-2.5 flex items-center justify-between">
                  <div class="flex items-center gap-2">
                    <h2 id="recent-title" class="text-sm font-semibold">最近项目</h2>
                    <span class="rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">{{ taskStore.taskHistory.length }}</span>
                  </div>
                  <div class="flex items-center gap-1">
                    <Button variant="ghost" size="sm" class="h-7 gap-1.5 px-2 text-xs text-muted-foreground" @click="commandPaletteOpen = true">
                      <Search class="h-3.5 w-3.5" aria-hidden="true" />
                      搜索
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      class="h-7 gap-1.5 px-2 text-xs text-muted-foreground hover:text-[hsl(var(--danger))]"
                      :disabled="taskStore.taskHistory.length === 0"
                      title="清空全部历史项目"
                      data-testid="home-clear-history-button"
                      @click="clearHistoryDialogOpen = true"
                    >
                      <Trash2 class="h-3.5 w-3.5" aria-hidden="true" />
                      清空记录
                    </Button>
                  </div>
                </div>

                <div class="app-panel overflow-hidden">
                  <div v-if="isLoading" class="space-y-px" aria-label="正在加载项目列表">
                    <div v-for="index in 4" :key="index" class="h-14 animate-pulse border-b bg-muted/40 last:border-0" />
                  </div>
                  <div v-else-if="recentTasks.length" class="overflow-x-auto">
                    <table class="w-full min-w-[760px] text-left text-xs">
                      <thead class="border-b bg-[hsl(var(--surface-subtle))] text-[11px] font-medium text-muted-foreground">
                        <tr>
                          <th class="px-4 py-2.5 font-medium">项目</th>
                          <th class="px-3 py-2.5 font-medium">当前阶段</th>
                          <th class="px-3 py-2.5 font-medium">状态</th>
                          <th class="px-3 py-2.5 text-right font-medium">运行记录</th>
                          <th class="px-3 py-2.5 font-medium">更新时间</th>
                          <th class="w-20 px-2 py-2.5"><span class="sr-only">项目操作</span></th>
                        </tr>
                      </thead>
                      <tbody class="divide-y">
                        <tr v-for="task in recentTasks" :key="task.task_id" class="group transition-colors hover:bg-muted/35">
                          <td class="max-w-[360px] px-4 py-3">
                            <RouterLink :to="`/project/${task.task_id}/overview`" class="block truncate font-medium hover:text-primary" :title="task.title">
                              {{ task.title }}
                            </RouterLink>
                            <span class="mt-0.5 block truncate font-mono text-[10px] text-muted-foreground">{{ task.task_id }}</span>
                          </td>
                          <td class="px-3 py-3 text-secondary">{{ statusConfig[task.status].stage }}</td>
                          <td class="px-3 py-3">
                            <span class="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[11px] font-medium" :class="statusConfig[task.status].badge">
                              <span class="h-1.5 w-1.5 rounded-full" :class="statusConfig[task.status].dot" />
                              {{ statusConfig[task.status].label }}
                            </span>
                          </td>
                          <td class="mono-data px-3 py-3 text-right text-secondary">{{ task.message_count }}</td>
                          <td class="whitespace-nowrap px-3 py-3 text-muted-foreground">{{ formatTaskTime(task.updated_at) }}</td>
                          <td class="px-2 py-3">
                            <div class="flex items-center justify-end gap-0.5">
                              <Button
                                type="button"
                                variant="ghost"
                                size="icon"
                                class="h-7 w-7 text-muted-foreground opacity-60 hover:text-[hsl(var(--danger))] group-hover:opacity-100"
                                :aria-label="`删除项目：${task.title}`"
                                :title="`删除项目：${task.title}`"
                                data-testid="home-delete-task-button"
                                @click="taskPendingDelete = task"
                              >
                                <Trash2 class="h-3.5 w-3.5" aria-hidden="true" />
                              </Button>
                              <Button as-child variant="ghost" size="icon" class="h-7 w-7 opacity-60 group-hover:opacity-100">
                              <RouterLink :to="`/project/${task.task_id}/overview`" :aria-label="`打开项目：${task.title}`">
                                <ChevronRight class="h-3.5 w-3.5" aria-hidden="true" />
                              </RouterLink>
                              </Button>
                            </div>
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                  <div v-else class="px-5 py-10 text-center">
                    <History class="mx-auto h-5 w-5 text-muted-foreground" aria-hidden="true" />
                    <p class="mt-2 text-sm font-medium">暂无历史项目</p>
                    <p class="mt-1 text-xs text-muted-foreground">首次提交后，项目会自动出现在这里。</p>
                  </div>
                </div>
              </section>
            </div>

            <aside class="space-y-4" aria-label="快捷操作和状态">
              <section class="app-panel p-4" aria-labelledby="quick-title">
                <h2 id="quick-title" class="text-sm font-semibold">快速开始</h2>
                <div class="mt-3 space-y-1.5">
                  <button type="button" class="flex w-full items-center gap-3 rounded-md border bg-card px-3 py-2.5 text-left transition-colors hover:bg-muted/50" @click="createProjectOpen = true">
                    <span class="flex h-8 w-8 items-center justify-center rounded-md bg-primary text-primary-foreground">
                      <UploadCloud class="h-4 w-4" aria-hidden="true" />
                    </span>
                    <span class="min-w-0 flex-1">
                      <span class="block text-xs font-medium">上传赛题创建</span>
                      <span class="mt-0.5 block text-[10px] text-muted-foreground">PDF 自动解析 + 数据附件</span>
                    </span>
                    <ChevronRight class="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
                  </button>
                  <button type="button" class="flex w-full items-center gap-3 rounded-md px-3 py-2.5 text-left transition-colors hover:bg-muted/50" @click="settingsOpen = true">
                    <span class="flex h-8 w-8 items-center justify-center rounded-md border bg-card text-muted-foreground">
                      <KeyRound class="h-4 w-4" aria-hidden="true" />
                    </span>
                    <span class="min-w-0 flex-1">
                      <span class="block text-xs font-medium">配置模型连接</span>
                      <span class="mt-0.5 block text-[10px] text-muted-foreground">管理主 Agent 与模型评审组</span>
                    </span>
                    <ChevronRight class="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
                  </button>
                </div>
              </section>

              <section class="app-panel p-4" aria-labelledby="review-title">
                <div class="flex items-center justify-between">
                  <h2 id="review-title" class="text-sm font-semibold">待人工确认</h2>
                  <span class="mono-data text-xs text-muted-foreground">{{ approvalCount }}</span>
                </div>
                <div v-if="approvalCount" class="mt-3 space-y-2">
                  <RouterLink
                    v-for="task in taskStore.taskHistory.filter((item) => item.status === 'awaiting_approval').slice(0, 3)"
                    :key="task.task_id"
                    :to="`/project/${task.task_id}/overview`"
                    class="block rounded-md border border-[hsl(var(--warning)/0.28)] bg-[hsl(var(--warning-subtle))] px-3 py-2.5"
                  >
                    <p class="truncate text-xs font-medium">{{ task.title }}</p>
                    <p class="mt-1 text-[10px] text-[hsl(var(--warning))]">Agent 后续节点已锁定 · 立即审核</p>
                  </RouterLink>
                </div>
                <div v-else class="mt-3 rounded-md border border-dashed px-3 py-5 text-center">
                  <CheckCircle2 class="mx-auto h-4 w-4 text-[hsl(var(--success))]" aria-hidden="true" />
                  <p class="mt-2 text-xs font-medium">没有等待处理的节点</p>
                  <p class="mt-1 text-[10px] leading-4 text-muted-foreground">Agent 到达审核闸门后会显示在这里。</p>
                </div>
              </section>

              <section class="app-panel p-4" aria-labelledby="delivery-title">
                <h2 id="delivery-title" class="text-sm font-semibold">真实交付状态</h2>
                <dl class="mt-3 space-y-2 text-xs">
                  <div class="flex items-center justify-between gap-3">
                    <dt class="flex items-center gap-2 text-muted-foreground"><Gauge class="h-3.5 w-3.5" aria-hidden="true" />计算后端</dt>
                    <dd class="font-medium">MATLAB 优先</dd>
                  </div>
                  <div class="flex items-center justify-between gap-3">
                    <dt class="flex items-center gap-2 text-muted-foreground"><FileText class="h-3.5 w-3.5" aria-hidden="true" />论文产物</dt>
                    <dd class="font-medium">Markdown / DOCX</dd>
                  </div>
                  <div class="flex items-center justify-between gap-3">
                    <dt class="flex items-center gap-2 text-muted-foreground"><Clock3 class="h-3.5 w-3.5" aria-hidden="true" />检查点</dt>
                    <dd class="font-medium">节点级持久化</dd>
                  </div>
                </dl>
              </section>
            </aside>
          </div>
        </div>
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
        <DialogHeader data-testid="home-delete-task-dialog">
          <DialogTitle>永久删除这个历史项目？</DialogTitle>
          <DialogDescription class="pt-1 leading-6">
            <span class="block font-medium text-foreground">{{ taskPendingDelete?.title }}</span>
            <span class="mt-1 block">消息记录、上传附件和生成文件都会删除，此操作无法撤销。</span>
          </DialogDescription>
        </DialogHeader>
        <DialogFooter class="mt-2 gap-2 sm:gap-0">
          <DialogClose as-child>
            <Button type="button" variant="outline" :disabled="isDeleting">取消</Button>
          </DialogClose>
          <Button type="button" variant="destructive" :disabled="isDeleting" @click="confirmTaskDeletion">
            <LoaderCircle v-if="isDeleting" class="h-4 w-4 animate-spin motion-reduce:animate-none" />
            {{ isDeleting ? '正在删除…' : '永久删除' }}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <Dialog :open="clearHistoryDialogOpen" @update:open="handleClearHistoryDialogOpen">
      <DialogContent class="max-w-md">
        <DialogHeader data-testid="home-clear-history-dialog">
          <DialogTitle>永久清空全部历史项目？</DialogTitle>
          <DialogDescription class="pt-1 leading-6">
            将删除 {{ taskStore.taskHistory.length }} 条项目的消息记录、上传附件和生成文件，此操作无法撤销。
          </DialogDescription>
        </DialogHeader>
        <div
          v-if="activeTaskCount > 0"
          class="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm leading-5 text-amber-800 dark:border-amber-900 dark:bg-amber-950/35 dark:text-amber-200"
          role="alert"
        >
          仍有 {{ activeTaskCount }} 个任务正在运行或等待验收，请先停止或完成审核后再清空。
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
            <LoaderCircle v-if="isClearingHistory" class="h-4 w-4 animate-spin motion-reduce:animate-none" />
            {{ isClearingHistory ? '正在清空…' : '永久清空' }}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  </div>
</template>
