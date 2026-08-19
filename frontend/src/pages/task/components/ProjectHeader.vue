<script setup lang="ts">
import { Button } from "@/components/ui/button";
import FilesSheet from "@/pages/task/components/FileSheet.vue";
import type { ProjectStage, StageKey } from "@/pages/task/projectWorkspace";
import {
	ArrowLeft,
	Bot,
	Check,
	CircleAlert,
	Clock3,
	Download,
	LoaderCircle,
	PanelRightClose,
	PanelRightOpen,
	Pause,
	Play,
	Wifi,
	WifiOff,
} from "lucide-vue-next";
import { RouterLink } from "vue-router";

const props = defineProps<{
	taskId: string;
	title: string;
	stages: ProjectStage[];
	activeStage: StageKey;
	runningDuration: string;
	wsStatus: "connecting" | "connected" | "disconnected" | "reconnecting";
	isRunning: boolean;
	isStopping: boolean;
	canResume: boolean;
	selectedModel: string;
	copilotOpen: boolean;
}>();

defineEmits<{
	selectStage: [stage: StageKey];
	stop: [];
	resume: [];
	download: [];
	toggleCopilot: [];
}>();

const statusLabel = {
	connecting: "连接中",
	connected: "已连接",
	disconnected: "未连接",
	reconnecting: "重连中",
} as const;
</script>

<template>
  <header class="shrink-0 border-b bg-card" aria-label="项目工具栏">
    <div class="flex h-14 min-w-0 items-center gap-2 px-3">
      <Button as-child variant="ghost" size="icon" class="h-8 w-8 shrink-0 text-muted-foreground" title="返回项目主页">
        <RouterLink to="/home" aria-label="返回项目主页">
          <ArrowLeft class="h-4 w-4" aria-hidden="true" />
        </RouterLink>
      </Button>

      <span class="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-slate-950 text-xs font-semibold text-white dark:bg-white dark:text-slate-950">M</span>
      <div class="min-w-0 max-w-72">
        <h1 class="truncate text-sm font-semibold" :title="props.title">{{ props.title }}</h1>
        <div class="mt-0.5 flex items-center gap-2 text-[10px] text-muted-foreground">
          <span class="truncate font-mono">{{ props.taskId }}</span>
          <span class="inline-flex items-center gap-1">
            <Check class="h-3 w-3 text-[hsl(var(--success))]" aria-hidden="true" />
            已自动保存
          </span>
        </div>
      </div>

      <span class="mx-1 hidden h-5 w-px bg-border lg:block" aria-hidden="true" />
      <div class="hidden items-center gap-3 text-[11px] text-muted-foreground lg:flex">
        <span class="inline-flex items-center gap-1.5" :title="statusLabel[props.wsStatus]">
          <Wifi v-if="props.wsStatus === 'connected'" class="h-3.5 w-3.5 text-[hsl(var(--success))]" aria-hidden="true" />
          <LoaderCircle v-else-if="props.wsStatus === 'connecting' || props.wsStatus === 'reconnecting'" class="h-3.5 w-3.5 animate-spin text-[hsl(var(--warning))] motion-reduce:animate-none" aria-hidden="true" />
          <WifiOff v-else class="h-3.5 w-3.5 text-[hsl(var(--danger))]" aria-hidden="true" />
          {{ statusLabel[props.wsStatus] }}
        </span>
        <span class="inline-flex items-center gap-1.5">
          <Clock3 class="h-3.5 w-3.5" aria-hidden="true" />
          <span class="mono-data">{{ props.runningDuration }}</span>
        </span>
        <span v-if="props.selectedModel" class="hidden max-w-52 items-center gap-1.5 xl:inline-flex" :title="props.selectedModel">
          <Bot class="h-3.5 w-3.5" aria-hidden="true" />
          <span class="truncate">{{ props.selectedModel }}</span>
        </span>
      </div>

      <div class="ml-auto flex shrink-0 items-center gap-1">
        <Button
          v-if="props.isRunning"
          type="button"
          variant="destructive"
          size="sm"
          class="h-8 gap-1.5 px-2.5 text-xs"
          :disabled="props.isStopping"
          @click="$emit('stop')"
        >
          <LoaderCircle v-if="props.isStopping" class="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" aria-hidden="true" />
          <Pause v-else class="h-3.5 w-3.5" aria-hidden="true" />
          {{ props.isStopping ? "停止中" : "停止" }}
        </Button>
        <Button
          v-else-if="props.canResume"
          type="button"
          size="sm"
          class="h-8 gap-1.5 px-2.5 text-xs"
          @click="$emit('resume')"
        >
          <Play class="h-3.5 w-3.5" aria-hidden="true" />
          从节点续跑
        </Button>

        <FilesSheet :task-id="props.taskId" />
        <Button type="button" variant="ghost" size="icon" class="h-8 w-8 text-muted-foreground" title="下载消息" aria-label="下载消息" @click="$emit('download')">
          <Download class="h-4 w-4" aria-hidden="true" />
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          class="h-8 w-8 text-muted-foreground"
          :title="props.copilotOpen ? '收起 AI Copilot' : '打开 AI Copilot'"
          :aria-label="props.copilotOpen ? '收起 AI Copilot' : '打开 AI Copilot'"
          @click="$emit('toggleCopilot')"
        >
          <PanelRightClose v-if="props.copilotOpen" class="h-4 w-4" aria-hidden="true" />
          <PanelRightOpen v-else class="h-4 w-4" aria-hidden="true" />
        </Button>
      </div>
    </div>

    <nav class="flex h-9 min-w-0 items-center gap-0 overflow-x-auto border-t px-3" aria-label="阶段进度">
      <template v-for="(stage, index) in props.stages.filter((item) => item.key !== 'overview')" :key="stage.key">
        <button
          type="button"
          class="group inline-flex h-full shrink-0 items-center gap-1.5 border-b-2 px-2.5 text-[11px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
          :class="props.activeStage === stage.key ? 'border-primary font-medium text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground'"
          @click="$emit('selectStage', stage.key)"
        >
          <span
            class="flex h-4 w-4 items-center justify-center rounded-full border text-[9px]"
            :class="{
              'border-[hsl(var(--success))] bg-[hsl(var(--success-subtle))] text-[hsl(var(--success))]': stage.status === 'completed',
              'border-[hsl(var(--warning))] bg-[hsl(var(--warning-subtle))] text-[hsl(var(--warning))]': stage.status === 'awaiting_approval' || stage.status === 'warning',
              'border-primary bg-primary text-primary-foreground': stage.status === 'running',
              'border-[hsl(var(--danger))] text-[hsl(var(--danger))]': stage.status === 'failed',
            }"
          >
            <Check v-if="stage.status === 'completed'" class="h-2.5 w-2.5" aria-hidden="true" />
            <CircleAlert v-else-if="stage.status === 'awaiting_approval' || stage.status === 'warning' || stage.status === 'failed'" class="h-2.5 w-2.5" aria-hidden="true" />
            <span v-else>{{ index + 1 }}</span>
          </span>
          {{ stage.label }}
        </button>
        <span v-if="index < props.stages.length - 2" class="text-border" aria-hidden="true">/</span>
      </template>
    </nav>
  </header>
</template>
