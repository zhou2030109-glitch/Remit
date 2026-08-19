<script setup lang="ts">
import { Button } from "@/components/ui/button";
import type {
	ProjectAssetCount,
	ProjectStage,
	StageKey,
	StageStatus,
} from "@/pages/task/projectWorkspace";
import {
	BarChart3,
	BookOpenText,
	Braces,
	Check,
	ChevronRight,
	Circle,
	CircleAlert,
	Database,
	FileChartColumn,
	FileCode2,
	FileText,
	FlaskConical,
	FolderKanban,
	Gauge,
	Library,
	LoaderCircle,
	PanelLeftClose,
	PanelLeftOpen,
	Settings2,
	Sigma,
	TriangleAlert,
} from "lucide-vue-next";
import type { Component } from "vue";

const props = defineProps<{
	stages: ProjectStage[];
	activeStage: StageKey;
	assets: ProjectAssetCount[];
	collapsed: boolean;
	environmentLabel: string;
}>();

defineEmits<{
	select: [stage: StageKey];
	selectAsset: [asset: ProjectAssetCount["key"]];
	toggle: [];
	settings: [];
}>();

const stageIcons: Record<StageKey, Component> = {
	overview: Gauge,
	problem: BookOpenText,
	data: Database,
	literature: Library,
	model: Sigma,
	solve: Braces,
	results: BarChart3,
	paper: FileText,
};

const assetIcons: Record<ProjectAssetCount["key"], Component> = {
	datasets: Database,
	code: FileCode2,
	charts: FileChartColumn,
	experiments: FlaskConical,
	paper: FileText,
	references: FolderKanban,
};

const statusMeta: Record<
	StageStatus,
	{ label: string; icon: Component; className: string }
> = {
	not_started: {
		label: "未开始",
		icon: Circle,
		className: "text-muted-foreground",
	},
	running: {
		label: "运行中",
		icon: LoaderCircle,
		className: "text-[hsl(var(--info))]",
	},
	awaiting_approval: {
		label: "待确认",
		icon: CircleAlert,
		className: "text-[hsl(var(--warning))]",
	},
	completed: {
		label: "已完成",
		icon: Check,
		className: "text-[hsl(var(--success))]",
	},
	warning: {
		label: "有缺口",
		icon: TriangleAlert,
		className: "text-[hsl(var(--warning))]",
	},
	failed: {
		label: "失败",
		icon: TriangleAlert,
		className: "text-[hsl(var(--danger))]",
	},
};
</script>

<template>
  <aside
    class="flex h-full shrink-0 flex-col border-r bg-[hsl(var(--sidebar-background))] transition-[width] duration-150 motion-reduce:transition-none"
    :class="props.collapsed ? 'w-14' : 'w-56'"
    aria-label="项目导航"
  >
    <div class="flex h-11 items-center border-b px-2" :class="props.collapsed ? 'justify-center' : 'justify-between'">
      <span v-if="!props.collapsed" class="px-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">建模流程</span>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        class="h-8 w-8 text-muted-foreground"
        :aria-label="props.collapsed ? '展开项目导航' : '折叠项目导航'"
        :title="props.collapsed ? '展开项目导航' : '折叠项目导航'"
        @click="$emit('toggle')"
      >
        <PanelLeftOpen v-if="props.collapsed" class="h-4 w-4" aria-hidden="true" />
        <PanelLeftClose v-else class="h-4 w-4" aria-hidden="true" />
      </Button>
    </div>

    <div class="min-h-0 flex-1 overflow-y-auto px-2 py-3">
      <nav class="space-y-0.5" aria-label="建模阶段">
        <button
          v-for="stage in props.stages"
          :key="stage.key"
          type="button"
          class="group flex h-9 w-full items-center rounded-md text-left text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          :class="[
            props.collapsed ? 'justify-center px-0' : 'gap-2.5 px-2.5',
            props.activeStage === stage.key
              ? 'bg-sidebar-accent font-medium text-sidebar-accent-foreground'
              : 'text-sidebar-foreground hover:bg-sidebar-accent/70',
          ]"
          :aria-current="props.activeStage === stage.key ? 'page' : undefined"
          :title="props.collapsed ? `${stage.label} · ${statusMeta[stage.status].label}` : undefined"
          @click="$emit('select', stage.key)"
        >
          <component :is="stageIcons[stage.key]" class="h-4 w-4 shrink-0" aria-hidden="true" />
          <span v-if="!props.collapsed" class="min-w-0 flex-1 truncate">{{ stage.label }}</span>
          <span v-if="!props.collapsed" class="inline-flex items-center gap-1 text-[10px]" :class="statusMeta[stage.status].className">
            <component
              :is="statusMeta[stage.status].icon"
              class="h-3 w-3"
              :class="stage.status === 'running' ? 'animate-spin motion-reduce:animate-none' : ''"
              aria-hidden="true"
            />
            <span class="sr-only xl:not-sr-only">{{ statusMeta[stage.status].label }}</span>
          </span>
        </button>
      </nav>

      <template v-if="!props.collapsed">
        <p class="px-2.5 pb-1.5 pt-6 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">项目资产</p>
        <nav class="space-y-0.5" aria-label="项目资产">
          <button
            v-for="asset in props.assets"
            :key="asset.key"
            type="button"
            class="flex h-8 w-full items-center gap-2.5 rounded-md px-2.5 text-left text-xs text-sidebar-foreground transition-colors hover:bg-sidebar-accent/70"
            @click="$emit('selectAsset', asset.key)"
          >
            <component :is="assetIcons[asset.key]" class="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
            <span class="min-w-0 flex-1 truncate">{{ asset.label }}</span>
            <span class="mono-data text-[10px] text-muted-foreground">{{ asset.count }}</span>
          </button>
        </nav>
      </template>
    </div>

    <div class="border-t p-2">
      <div v-if="!props.collapsed" class="mb-1 flex items-center gap-2 rounded-md px-2 py-2 text-[10px] text-muted-foreground">
        <span class="h-1.5 w-1.5 rounded-full bg-[hsl(var(--success))]" aria-hidden="true" />
        <span class="truncate">{{ props.environmentLabel }}</span>
      </div>
      <button
        type="button"
        class="flex h-8 w-full items-center rounded-md text-xs text-sidebar-foreground transition-colors hover:bg-sidebar-accent"
        :class="props.collapsed ? 'justify-center' : 'gap-2.5 px-2'"
        title="项目设置"
        @click="$emit('settings')"
      >
        <Settings2 class="h-3.5 w-3.5" aria-hidden="true" />
        <span v-if="!props.collapsed">项目设置</span>
        <ChevronRight v-if="!props.collapsed" class="ml-auto h-3 w-3 text-muted-foreground" aria-hidden="true" />
      </button>
    </div>
  </aside>
</template>
