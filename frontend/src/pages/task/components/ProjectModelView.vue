<script setup lang="ts">
import { useTaskStore } from "@/stores/task";
import {
	ArrowRight,
	BrainCircuit,
	CheckCircle2,
	GitCompareArrows,
	ShieldAlert,
} from "lucide-vue-next";
import { computed } from "vue";
import {
	agentValueText,
	humanizeAgentKey,
	parseAgentRecord,
} from "../agentContent";

const taskStore = useTaskStore();
const modelerMessage = computed(() => {
	const messages = taskStore.modelerMessages;
	return messages.length ? messages[messages.length - 1] : null;
});
const modelData = computed(() =>
	parseAgentRecord(modelerMessage.value?.content),
);
const latestResult = computed(() => {
	const summaries = taskStore.executionSummaries;
	return summaries.length ? summaries[summaries.length - 1] : null;
});

const sections = computed(() => {
	if (!modelData.value) return [];
	const priority = ["eda", "assumptions", "validation"];
	return Object.entries(modelData.value)
		.filter(
			([key, value]) =>
				!["title", "background", "ques_count"].includes(key) &&
				Boolean(agentValueText(value)),
		)
		.map(([key, value]) => ({
			key,
			label: humanizeAgentKey(key),
			content: agentValueText(value),
			order: priority.includes(key)
				? priority.indexOf(key)
				: /^ques/i.test(key)
					? 10 + Number(key.match(/\d+/)?.[0] ?? 0)
					: 100,
		}))
		.sort((a, b) => a.order - b.order);
});

const rawFallback = computed(() =>
	modelData.value ? "" : (modelerMessage.value?.content?.trim() ?? ""),
);
</script>

<template>
  <section class="flex h-full min-h-0 flex-col bg-background" aria-labelledby="model-view-title">
    <header class="shrink-0 border-b bg-card px-5 py-4 lg:px-6">
      <div class="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p class="text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">Model blueprint</p>
          <h1 id="model-view-title" class="mt-1 text-lg font-semibold tracking-tight">模型设计与决策记录</h1>
          <p class="mt-1 text-xs text-muted-foreground">从建模手方案到代码验证结果，集中记录选择、替换与局限。</p>
        </div>
        <span v-if="latestResult?.selected_model" class="max-w-sm truncate rounded-md border bg-[hsl(var(--surface-subtle))] px-2.5 py-1.5 text-[11px] font-medium" :title="latestResult.selected_model">
          当前入选 · {{ latestResult.selected_model }}
        </span>
      </div>
    </header>

    <div class="min-h-0 flex-1 overflow-y-auto">
      <div class="mx-auto grid w-full max-w-6xl gap-5 p-5 xl:grid-cols-[minmax(0,1fr)_320px] lg:p-6">
        <main class="min-w-0">
          <div class="mb-2.5 flex items-center justify-between">
            <h2 class="text-sm font-semibold">建模方案</h2>
            <span class="text-[10px] text-muted-foreground">{{ sections.length }} 个结构化章节</span>
          </div>
          <div v-if="sections.length" class="app-panel divide-y overflow-hidden">
            <article v-for="(section, index) in sections" :key="section.key" class="grid gap-3 px-4 py-4 sm:grid-cols-[34px_minmax(0,1fr)]">
              <span class="flex h-7 w-7 items-center justify-center rounded-md border bg-[hsl(var(--surface-subtle))] font-mono text-[10px] text-muted-foreground">{{ String(index + 1).padStart(2, '0') }}</span>
              <div class="min-w-0">
                <h3 class="text-xs font-semibold">{{ section.label }}</h3>
                <p class="mt-1.5 whitespace-pre-wrap text-sm leading-7 text-secondary">{{ section.content }}</p>
              </div>
            </article>
          </div>
          <div v-else-if="rawFallback" class="app-panel p-5">
            <p class="whitespace-pre-wrap text-sm leading-7 text-secondary">{{ rawFallback }}</p>
          </div>
          <div v-else class="app-panel flex min-h-48 items-center justify-center border-dashed p-6 text-center">
            <div>
              <BrainCircuit class="mx-auto h-5 w-5 text-muted-foreground" aria-hidden="true" />
              <p class="mt-2 text-xs font-medium">尚未形成模型方案</p>
              <p class="mt-1 text-[10px] text-muted-foreground">建模手输出后会在此按章节整理。</p>
            </div>
          </div>
        </main>

        <aside class="space-y-4" aria-label="模型决策侧栏">
          <section class="app-panel p-4">
            <div class="flex items-center gap-2">
              <GitCompareArrows class="h-4 w-4 text-primary" aria-hidden="true" />
              <h2 class="text-xs font-semibold">模型选择链</h2>
            </div>
            <div v-if="latestResult" class="mt-3 space-y-2">
              <div v-for="candidate in latestResult.candidate_models" :key="candidate" class="flex items-center gap-2 rounded-md border px-2.5 py-2 text-[11px]">
                <CheckCircle2 v-if="candidate === latestResult.selected_model" class="h-3.5 w-3.5 shrink-0 text-[hsl(var(--success))]" aria-hidden="true" />
                <span v-else class="h-1.5 w-1.5 shrink-0 rounded-full bg-muted-foreground/50" />
                <span class="min-w-0 flex-1 truncate" :title="candidate">{{ candidate }}</span>
                <span v-if="candidate === latestResult.selected_model" class="text-[9px] font-medium text-[hsl(var(--success))]">入选</span>
              </div>
              <p v-if="!latestResult.candidate_models.length" class="text-[11px] text-muted-foreground">本次记录未提供候选模型清单。</p>
              <div v-if="latestResult.revision_count" class="flex items-center gap-2 rounded-md bg-[hsl(var(--warning-subtle))] px-2.5 py-2 text-[10px] text-[hsl(var(--warning))]">
                已根据结果自动换模/返修 {{ latestResult.revision_count }} 次
                <ArrowRight class="ml-auto h-3 w-3" aria-hidden="true" />
              </div>
            </div>
            <p v-else class="mt-3 text-[11px] leading-5 text-muted-foreground">代码手完成首轮验证后，这里会显示候选模型、入选模型和改模次数。</p>
          </section>

          <section class="app-panel p-4">
            <div class="flex items-center gap-2">
              <ShieldAlert class="h-4 w-4 text-[hsl(var(--warning))]" aria-hidden="true" />
              <h2 class="text-xs font-semibold">建模手复核</h2>
            </div>
            <p class="mt-2 text-[11px] leading-5 text-secondary">{{ latestResult?.modeler_summary || '尚无运行结果可供建模手复核。' }}</p>
            <ul v-if="latestResult?.modeler_weaknesses.length" class="mt-3 space-y-1.5 border-t pt-3 text-[10px] leading-4 text-muted-foreground">
              <li v-for="weakness in latestResult.modeler_weaknesses" :key="weakness">· {{ weakness }}</li>
            </ul>
          </section>
        </aside>
      </div>
    </div>
  </section>
</template>
