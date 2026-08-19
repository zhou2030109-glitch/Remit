<script setup lang="ts">
import CsvPreviewTable from "@/pages/task/components/CsvPreviewTable.vue";
import { useTaskStore } from "@/stores/task";
import type {
	ExecutionMetric,
	ExecutionSummaryMessage,
	MetricExplanation,
} from "@/utils/response";
import {
	BarChart3,
	CheckCircle2,
	CircleAlert,
	FileCode2,
	FlaskConical,
	GitCompareArrows,
	Image,
	RefreshCw,
	ShieldCheck,
} from "lucide-vue-next";
import { computed } from "vue";

const props = defineProps<{ view: "solve" | "results" }>();
const taskStore = useTaskStore();

const summaries = computed(() => [...taskStore.executionSummaries].reverse());
const latest = computed(() => summaries.value[0] ?? null);
const statusMeta = {
	passed: {
		label: "质量门禁通过",
		className: "bg-[hsl(var(--success-subtle))] text-[hsl(var(--success))]",
		icon: CheckCircle2,
	},
	refined: {
		label: "改模后通过",
		className: "bg-[hsl(var(--accent))] text-primary",
		icon: RefreshCw,
	},
	needs_review: {
		label: "需要重点审核",
		className: "bg-[hsl(var(--warning-subtle))] text-[hsl(var(--warning))]",
		icon: CircleAlert,
	},
} as const;

function metricLabel(name: string) {
	const labels: Record<string, string> = {
		r2: "R²",
		rmse: "RMSE",
		mae: "MAE",
		accuracy: "准确率",
		balanced_accuracy: "平衡准确率",
		f1_macro: "Macro-F1",
	};
	return labels[name.trim().toLowerCase()] ?? name.toUpperCase();
}

function formatMetric(value: number | null | undefined) {
	if (value == null || !Number.isFinite(value)) return "—";
	const magnitude = Math.abs(value);
	if (magnitude !== 0 && (magnitude < 0.001 || magnitude >= 10000))
		return value.toExponential(2);
	return value.toLocaleString("zh-CN", { maximumFractionDigits: 4 });
}

function comparison(metric: ExecutionMetric) {
	if (metric.relative_improvement == null) return "";
	const value = metric.relative_improvement;
	return `${value >= 0 ? "+" : "−"}${Math.abs(value).toLocaleString("zh-CN", {
		style: "percent",
		maximumFractionDigits: 1,
	})}`;
}

function totalArtifacts(summary: ExecutionSummaryMessage) {
	return summary.artifacts.length + summary.paper_ready_images.length;
}

function statusIconTone(status: ExecutionSummaryMessage["status"]) {
	if (status === "passed") return "text-[hsl(var(--success))]";
	if (status === "needs_review") return "text-[hsl(var(--warning))]";
	return "text-primary";
}

function verdictDot(verdict: MetricExplanation["verdict"]) {
	if (verdict === "good") return "bg-[hsl(var(--success))]";
	if (verdict === "ok") return "bg-[hsl(var(--warning))]";
	if (verdict === "poor") return "bg-[hsl(var(--danger))]";
	return "bg-muted-foreground/50";
}
</script>

<template>
  <section class="flex h-full min-h-0 flex-col bg-background" :aria-labelledby="`${props.view}-view-title`">
    <header class="shrink-0 border-b bg-card px-5 py-4 lg:px-6">
      <div class="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p class="text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">{{ props.view === 'solve' ? 'Experiment ledger' : 'Evidence review' }}</p>
          <h1 :id="`${props.view}-view-title`" class="mt-1 text-lg font-semibold tracking-tight">{{ props.view === 'solve' ? '模型求解与实验记录' : '结果分析与质量复核' }}</h1>
          <p class="mt-1 text-xs text-muted-foreground">{{ props.view === 'solve' ? '查看每轮运行、候选模型与产物位置，不在页面铺开代码。' : '集中核对核心指标、基线改善、模型局限和论文可用证据。' }}</p>
        </div>
        <div class="flex items-center gap-2 text-[10px] text-muted-foreground">
          <span class="rounded-md border bg-[hsl(var(--surface-subtle))] px-2 py-1">{{ summaries.length }} 个实验节点</span>
          <span v-if="latest" class="rounded-md px-2 py-1 font-medium" :class="statusMeta[latest.status].className">{{ statusMeta[latest.status].label }}</span>
        </div>
      </div>
    </header>

    <div class="min-h-0 flex-1 overflow-y-auto">
      <div v-if="latest" class="mx-auto w-full max-w-6xl space-y-5 p-5 lg:p-6">
        <section class="app-panel overflow-hidden" aria-labelledby="latest-result-heading">
          <div class="grid gap-0 xl:grid-cols-[minmax(0,1fr)_300px]">
            <div class="p-5">
              <div class="flex flex-wrap items-start justify-between gap-3">
                <div class="min-w-0">
                  <div class="flex items-center gap-2">
                    <component :is="statusMeta[latest.status].icon" class="h-4 w-4" :class="statusIconTone(latest.status)" aria-hidden="true" />
                    <p class="text-[10px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">最新复核节点</p>
                  </div>
                  <h2 id="latest-result-heading" class="mt-2 text-base font-semibold">{{ latest.node_label }}</h2>
                  <p class="mt-2 max-w-3xl text-sm leading-6 text-secondary">{{ latest.run_summary }}</p>
                </div>
                <span v-if="latest.revision_count" class="rounded-md bg-[hsl(var(--warning-subtle))] px-2 py-1 text-[10px] font-medium text-[hsl(var(--warning))]">自动换模 {{ latest.revision_count }} 次</span>
              </div>

              <div v-if="latest.metrics.length" class="mt-4 grid gap-px overflow-hidden rounded-lg border bg-border sm:grid-cols-2 xl:grid-cols-4">
                <div v-for="metric in latest.metrics" :key="metric.name" class="bg-card px-3 py-3">
                  <div class="flex items-center justify-between gap-2 text-[10px] font-medium text-muted-foreground">
                    <span>{{ metricLabel(metric.name) }}</span>
                    <BarChart3 class="h-3 w-3" aria-hidden="true" />
                  </div>
                  <div class="mt-1 flex items-baseline justify-between gap-2">
                    <strong class="mono-data text-lg font-semibold">{{ formatMetric(metric.model_value) }}</strong>
                    <span v-if="metric.relative_improvement != null" class="text-[10px] font-medium" :class="metric.relative_improvement >= 0 ? 'text-[hsl(var(--success))]' : 'text-[hsl(var(--danger))]'">{{ comparison(metric) }}</span>
                  </div>
                  <p v-if="metric.baseline_value != null" class="mt-0.5 text-[9px] text-muted-foreground">基线 {{ formatMetric(metric.baseline_value) }}</p>
                </div>
              </div>

              <div v-if="latest.metric_explanations?.length" class="mt-3 rounded-lg border bg-[hsl(var(--surface-subtle))] px-4 py-3" data-testid="metric-explanations">
                <p class="text-[10px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">这些数字是什么意思？</p>
                <ul class="mt-2 space-y-2">
                  <li v-for="explanation in latest.metric_explanations" :key="explanation.name" class="flex gap-2 text-[11px] leading-5">
                    <span class="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full" :class="verdictDot(explanation.verdict)" />
                    <span class="text-secondary"><strong class="font-medium text-foreground">{{ explanation.friendly_name }} = {{ explanation.value_text }}</strong>：{{ explanation.meaning }}</span>
                  </li>
                </ul>
              </div>
            </div>

            <aside class="border-t bg-[hsl(var(--surface-subtle))] p-5 xl:border-l xl:border-t-0" aria-label="入选模型">
              <p class="text-[10px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">Selected model</p>
              <h3 class="mt-2 break-words text-sm font-semibold leading-6">{{ latest.selected_model || '尚未记录入选模型' }}</h3>
              <div class="mt-4 space-y-2 text-[11px]">
                <div class="flex items-center justify-between gap-3"><span class="text-muted-foreground">候选模型</span><strong>{{ latest.candidate_models.length }}</strong></div>
                <div class="flex items-center justify-between gap-3"><span class="text-muted-foreground">代码位置</span><strong>{{ latest.code_locations.length }}</strong></div>
                <div class="flex items-center justify-between gap-3"><span class="text-muted-foreground">交付产物</span><strong>{{ totalArtifacts(latest) }}</strong></div>
              </div>
              <div v-if="latest.candidate_models.length" class="mt-4 border-t pt-3">
                <p class="text-[10px] font-medium text-muted-foreground">比较过的方案</p>
                <div class="mt-2 flex flex-wrap gap-1">
                  <span v-for="model in latest.candidate_models" :key="model" class="max-w-full truncate rounded border bg-card px-1.5 py-0.5 text-[9px]" :title="model">{{ model }}</span>
                </div>
              </div>
            </aside>
          </div>
        </section>

        <section v-if="latest.table_previews?.length" aria-labelledby="table-preview-heading">
          <div class="mb-2.5 flex items-center justify-between">
            <h2 id="table-preview-heading" class="text-sm font-semibold">结果数据预览</h2>
            <span class="text-[10px] text-muted-foreground">来自本轮真实产物 CSV</span>
          </div>
          <div class="grid gap-4 xl:grid-cols-2">
            <CsvPreviewTable
              v-for="table in latest.table_previews"
              :key="table.filename"
              :title="table.filename"
              :columns="table.columns"
              :rows="table.rows"
              :truncated="table.rows.length >= table.preview_limited_to_rows"
            />
          </div>
        </section>

        <div class="grid gap-5 xl:grid-cols-[minmax(0,1.15fr)_minmax(280px,0.85fr)]">
          <section aria-labelledby="modeler-review-heading">
            <div class="mb-2.5 flex items-center justify-between">
              <h2 id="modeler-review-heading" class="text-sm font-semibold">建模手回读结论</h2>
              <span class="inline-flex items-center gap-1 text-[10px] text-muted-foreground"><ShieldCheck class="h-3 w-3" />结果驱动改模</span>
            </div>
            <div class="app-panel p-4">
              <p class="text-sm leading-6 text-secondary">{{ latest.modeler_summary }}</p>
              <ul v-if="latest.modeler_evidence.length" class="mt-3 space-y-2 border-t pt-3 text-[11px] leading-5 text-secondary">
                <li v-for="evidence in latest.modeler_evidence" :key="evidence" class="flex gap-2">
                  <CheckCircle2 class="mt-0.5 h-3.5 w-3.5 shrink-0 text-[hsl(var(--success))]" aria-hidden="true" />
                  <span>{{ evidence }}</span>
                </li>
              </ul>
              <div v-if="latest.modeler_weaknesses.length" class="mt-3 rounded-md border border-[hsl(var(--warning)/0.25)] bg-[hsl(var(--warning-subtle))] px-3 py-2.5">
                <p class="text-[10px] font-semibold text-[hsl(var(--warning))]">仍需人工检查</p>
                <ul class="mt-1.5 space-y-1 text-[10px] leading-4 text-secondary">
                  <li v-for="weakness in latest.modeler_weaknesses" :key="weakness">· {{ weakness }}</li>
                </ul>
              </div>
            </div>
          </section>

          <section aria-labelledby="deliverables-heading">
            <div class="mb-2.5 flex items-center justify-between">
              <h2 id="deliverables-heading" class="text-sm font-semibold">本轮产物</h2>
              <span class="text-[10px] text-muted-foreground">真实文件索引</span>
            </div>
            <div class="app-panel divide-y overflow-hidden">
              <div v-for="location in latest.code_locations.slice(0, 5)" :key="`${location.path}-${location.section}`" class="flex items-start gap-2.5 px-3 py-2.5">
                <FileCode2 class="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
                <div class="min-w-0 flex-1"><code class="block truncate text-[10px]" :title="location.path">{{ location.path }}</code><span class="mt-0.5 block truncate text-[9px] text-muted-foreground">{{ location.section || location.language }}</span></div>
              </div>
              <div v-for="imagePath in latest.paper_ready_images.slice(0, 3)" :key="imagePath" class="flex items-center gap-2.5 px-3 py-2.5">
                <Image class="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
                <code class="min-w-0 flex-1 truncate text-[10px]" :title="imagePath">{{ imagePath }}</code>
              </div>
              <div v-if="!latest.code_locations.length && !latest.paper_ready_images.length" class="px-4 py-8 text-center text-[11px] text-muted-foreground">本轮尚无可索引文件。</div>
            </div>
          </section>
        </div>

        <section v-if="summaries.length > 1" aria-labelledby="history-heading">
          <div class="mb-2.5 flex items-center gap-2">
            <GitCompareArrows class="h-4 w-4 text-muted-foreground" aria-hidden="true" />
            <h2 id="history-heading" class="text-sm font-semibold">历史实验与改模轨迹</h2>
          </div>
          <div class="app-panel divide-y overflow-hidden">
            <article v-for="summary in summaries.slice(1)" :key="summary.id" class="grid gap-3 px-4 py-3 sm:grid-cols-[minmax(0,1fr)_180px_90px] sm:items-center">
              <div class="min-w-0"><h3 class="truncate text-xs font-medium" :title="summary.node_label">{{ summary.node_label }}</h3><p class="mt-0.5 line-clamp-1 text-[10px] text-muted-foreground">{{ summary.run_summary }}</p></div>
              <span class="truncate text-[10px] text-secondary" :title="summary.selected_model">{{ summary.selected_model || '未记录模型' }}</span>
              <span class="justify-self-start rounded-md px-2 py-1 text-[9px] font-medium sm:justify-self-end" :class="statusMeta[summary.status].className">{{ statusMeta[summary.status].label }}</span>
            </article>
          </div>
        </section>
      </div>

      <div v-else class="flex h-full min-h-72 items-center justify-center p-6 text-center">
        <div class="max-w-sm">
          <FlaskConical class="mx-auto h-6 w-6 text-muted-foreground" aria-hidden="true" />
          <h2 class="mt-3 text-sm font-semibold">{{ taskStore.isRunning ? '正在生成第一份实验记录' : '暂无结构化运行结果' }}</h2>
          <p class="mt-1 text-[11px] leading-5 text-muted-foreground">代码完成执行、质量门禁和建模手回读后，指标、模型选择和文件索引会出现在这里。</p>
        </div>
      </div>
    </div>
  </section>
</template>
