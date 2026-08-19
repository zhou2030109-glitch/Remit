<script setup lang="ts">
import { Button } from "@/components/ui/button";
import ProjectAnalysisCards from "@/pages/task/components/ProjectAnalysisCards.vue";
import { useTaskStore } from "@/stores/task";
import type { AuditStatus } from "@/utils/response";
import {
	AlertTriangle,
	BookOpenText,
	Database,
	FileCheck2,
	FileText,
	LoaderCircle,
	RefreshCw,
	SearchX,
} from "lucide-vue-next";
import { computed, onMounted, ref } from "vue";

const props = defineProps<{ taskId: string }>();
const taskStore = useTaskStore();
const loading = ref(false);
const loadError = ref("");

const snapshot = computed(() => taskStore.workspaceSnapshot);
const questions = computed(() =>
	Object.entries(snapshot.value?.source.questions ?? {})
		.map(([key, content]) => ({
			key,
			number: Number(key.match(/\d+/)?.[0] ?? 0),
			content,
		}))
		.sort((left, right) => left.number - right.number),
);

function asRecord(value: unknown): Record<string, unknown> {
	return value && typeof value === "object" && !Array.isArray(value)
		? (value as Record<string, unknown>)
		: {};
}

function asRecords(value: unknown): Array<Record<string, unknown>> {
	return Array.isArray(value) ? value.map(asRecord) : [];
}

function asStrings(value: unknown): string[] {
	return Array.isArray(value) ? value.map(String).filter(Boolean) : [];
}

const dataProfile = computed(() =>
	asRecord(snapshot.value?.research.data_profile),
);
const dataFiles = computed(() => asRecords(dataProfile.value.files));
const literature = computed(() =>
	asRecord(snapshot.value?.research.literature_review),
);
const literaturePapers = computed(() => asRecords(literature.value.papers));
const literatureQueries = computed(() =>
	asStrings(literature.value.searched_queries),
);
const literatureErrors = computed(() => asStrings(literature.value.errors));
const literatureFilteredCount = computed(() => {
	const filtered = literature.value.filtered_out;
	if (!filtered || typeof filtered !== "object") return 0;
	const count = Reflect.get(filtered, "count");
	return typeof count === "number" ? count : 0;
});

const sourceStatus = computed<AuditStatus>(() =>
	questions.value.length ? "completed" : "pending",
);
const preliminaryStatus = computed<AuditStatus>(() =>
	Object.keys(snapshot.value?.preliminary_analysis.question_analyses ?? {})
		.length
		? "completed"
		: "pending",
);

function statusLabel(status: string): string {
	if (status === "completed") return "已完成，有真实产物";
	if (status === "warning" || status === "partial")
		return "部分完成，有证据缺口";
	if (status === "failed") return "未完成";
	return "尚未开始";
}

function statusClass(status: string): string {
	if (status === "completed") {
		return "border-[hsl(var(--success)/0.3)] bg-[hsl(var(--success-subtle))] text-[hsl(var(--success))]";
	}
	if (status === "warning" || status === "partial") {
		return "border-[hsl(var(--warning)/0.35)] bg-[hsl(var(--warning-subtle))] text-[hsl(var(--warning))]";
	}
	if (status === "failed") {
		return "border-destructive/30 bg-destructive/5 text-destructive";
	}
	return "border-border bg-muted text-muted-foreground";
}

async function refresh() {
	loading.value = true;
	loadError.value = "";
	try {
		const result = await taskStore.loadTaskWorkspace(props.taskId);
		if (!result) loadError.value = "无法读取阶段产物，请检查后端后重试。";
	} finally {
		loading.value = false;
	}
}

onMounted(() => {
	if (!snapshot.value || snapshot.value.task_id !== props.taskId)
		void refresh();
});
</script>

<template>
  <section class="flex h-full min-h-0 flex-col bg-background" aria-labelledby="problem-audit-title">
    <header class="shrink-0 border-b bg-card px-5 py-4 lg:px-6">
      <div class="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p class="text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">Problem evidence ledger</p>
          <h1 id="problem-audit-title" class="mt-1 text-lg font-semibold tracking-tight">题目理解 · 全阶段审计</h1>
          <p class="mt-1 max-w-3xl text-xs leading-5 text-muted-foreground">
            主页面直接读取检查点和落盘产物。每一阶段分别展示真实结果、证据缺口和失败原因，不再从 Copilot 消息猜测。
          </p>
        </div>
        <div class="flex items-center gap-2">
          <span class="rounded-md border bg-[hsl(var(--surface-subtle))] px-2 py-1 text-[10px] text-muted-foreground">
            {{ questions.length }} 个子问题
          </span>
          <Button type="button" variant="outline" size="sm" class="h-8 gap-1.5 text-xs" :disabled="loading" @click="refresh">
            <RefreshCw class="h-3.5 w-3.5" :class="loading ? 'animate-spin motion-reduce:animate-none' : ''" aria-hidden="true" />
            刷新产物
          </Button>
        </div>
      </div>
    </header>

    <div class="min-h-0 flex-1 overflow-y-auto">
      <div class="mx-auto w-full max-w-7xl space-y-4 p-5 lg:p-6">
        <div v-if="loadError" class="flex items-start gap-2 rounded-lg border border-destructive/25 bg-destructive/5 px-4 py-3 text-xs text-destructive">
          <AlertTriangle class="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          {{ loadError }}
        </div>

        <div v-if="loading && !snapshot" class="app-panel flex min-h-56 items-center justify-center gap-2 text-sm text-muted-foreground">
          <LoaderCircle class="h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
          正在读取工作流产物
        </div>

        <template v-else-if="snapshot">
          <section class="app-panel overflow-hidden" aria-labelledby="source-stage-title">
            <header class="flex flex-wrap items-start justify-between gap-3 border-b bg-[hsl(var(--surface-subtle))] px-4 py-3">
              <div class="flex items-start gap-3">
                <span class="flex h-8 w-8 items-center justify-center rounded-md border bg-card font-mono text-[10px] font-semibold">01</span>
                <div>
                  <h2 id="source-stage-title" class="text-sm font-semibold">原题忠实提取</h2>
                  <p class="mt-0.5 text-[11px] text-muted-foreground">只读源字段；后续返修不能覆盖原题和小问。</p>
                </div>
              </div>
              <span class="rounded-full border px-2.5 py-1 text-[10px] font-medium" :class="statusClass(sourceStatus)">{{ statusLabel(sourceStatus) }}</span>
            </header>
            <div class="space-y-5 p-4 lg:p-5">
              <div>
                <h3 class="text-xl font-semibold leading-8 tracking-tight">{{ snapshot.source.title || "题名尚未提取" }}</h3>
                <p v-if="snapshot.source.background" class="mt-3 whitespace-pre-wrap border-l-2 border-primary/45 pl-4 text-sm leading-7 text-secondary">{{ snapshot.source.background }}</p>
              </div>

              <div>
                <div class="mb-2 flex items-center gap-2">
                  <FileCheck2 class="h-4 w-4 text-primary" aria-hidden="true" />
                  <h3 class="text-xs font-semibold">原题小问</h3>
                </div>
                <div v-if="questions.length" class="divide-y rounded-lg border">
                  <article v-for="question in questions" :key="question.key" class="grid gap-3 px-4 py-4 sm:grid-cols-[44px_minmax(0,1fr)]">
                    <span class="flex h-8 w-8 items-center justify-center rounded-md bg-foreground font-mono text-[10px] font-semibold text-background">Q{{ question.number }}</span>
                    <p class="whitespace-pre-wrap text-sm leading-6 text-secondary">{{ question.content }}</p>
                  </article>
                </div>
                <p v-else class="rounded-lg border border-dashed px-4 py-6 text-center text-xs text-[hsl(var(--warning))]">尚未提取正式小问。</p>
              </div>

              <details class="rounded-lg border bg-[hsl(var(--surface-subtle))]">
                <summary class="cursor-pointer px-4 py-3 text-xs font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary">查看完整原题正文</summary>
                <div class="border-t bg-card px-4 py-4">
                  <p class="whitespace-pre-wrap break-words text-xs leading-6 text-secondary">{{ snapshot.source.original_problem || "原题正文尚未保存" }}</p>
                </div>
              </details>
            </div>
          </section>

          <section class="app-panel overflow-hidden" aria-labelledby="preliminary-stage-title">
            <header class="flex flex-wrap items-start justify-between gap-3 border-b bg-[hsl(var(--surface-subtle))] px-4 py-3">
              <div class="flex items-start gap-3">
                <span class="flex h-8 w-8 items-center justify-center rounded-md border bg-card font-mono text-[10px] font-semibold">02</span>
                <div>
                  <h2 id="preliminary-stage-title" class="text-sm font-semibold">初步结构化题意分析</h2>
                  <p class="mt-0.5 text-[11px] text-muted-foreground">附件扫描前的第一版理解，保留用于与数据校正版逐项对照。</p>
                </div>
              </div>
              <span class="rounded-full border px-2.5 py-1 text-[10px] font-medium" :class="statusClass(preliminaryStatus)">{{ statusLabel(preliminaryStatus) }}</span>
            </header>
            <div class="space-y-4 p-4 lg:p-5">
              <div v-if="snapshot.preliminary_analysis.analysis_summary" class="rounded-lg border-l-2 border-l-primary bg-[hsl(var(--surface-subtle))] px-4 py-3">
                <p class="text-[10px] font-semibold text-muted-foreground">总体理解</p>
                <p class="mt-1 whitespace-pre-wrap text-sm leading-6 text-secondary">{{ snapshot.preliminary_analysis.analysis_summary }}</p>
              </div>
              <ProjectAnalysisCards :analyses="snapshot.preliminary_analysis.question_analyses" />
            </div>
          </section>

          <section class="app-panel overflow-hidden" aria-labelledby="research-stage-title">
            <header class="flex flex-wrap items-start justify-between gap-3 border-b bg-[hsl(var(--surface-subtle))] px-4 py-3">
              <div class="flex items-start gap-3">
                <span class="flex h-8 w-8 items-center justify-center rounded-md border bg-card font-mono text-[10px] font-semibold">03</span>
                <div>
                  <h2 id="research-stage-title" class="text-sm font-semibold">附件数据侦察与文献调研</h2>
                  <p class="mt-0.5 text-[11px] text-muted-foreground">附件画像和文献检索分别验收；任何一项失败都会明确标黄或标红。</p>
                </div>
              </div>
              <span class="rounded-full border px-2.5 py-1 text-[10px] font-medium" :class="statusClass(snapshot.research.outcome.status)">{{ statusLabel(snapshot.research.outcome.status) }}</span>
            </header>

            <div class="grid gap-4 p-4 lg:grid-cols-2 lg:p-5">
              <article class="rounded-lg border p-4">
                <div class="flex items-center justify-between gap-2">
                  <div class="flex items-center gap-2">
                    <Database class="h-4 w-4 text-primary" aria-hidden="true" />
                    <h3 class="text-xs font-semibold">附件画像</h3>
                  </div>
                  <span class="rounded-full border px-2 py-0.5 text-[9px] font-medium" :class="statusClass(String(snapshot.research.outcome.data_status))">{{ statusLabel(String(snapshot.research.outcome.data_status)) }}</span>
                </div>
                <div v-if="dataFiles.length" class="mt-3 space-y-3">
                  <section v-for="file in dataFiles" :key="String(file.file)" class="rounded-md bg-[hsl(var(--surface-subtle))] px-3 py-3">
                    <div class="flex flex-wrap items-baseline justify-between gap-2">
                      <h4 class="break-all text-xs font-semibold">{{ file.file }}</h4>
                      <span class="font-mono text-[10px] text-muted-foreground">{{ file.rows }} 行 · {{ file.columns_count }} 列</span>
                    </div>
                    <ul v-if="asRecords(file.sections).length" class="mt-2 space-y-1 text-[10px] leading-4 text-muted-foreground">
                      <li v-for="section in asRecords(file.sections)" :key="String(section.section)">
                        表段 {{ section.section }}：{{ section.rows }} 行，{{ section.columns_count }} 列
                      </li>
                    </ul>
                  </section>
                </div>
                <div v-else class="mt-3 rounded-md border border-dashed px-3 py-6 text-center">
                  <SearchX class="mx-auto h-5 w-5 text-[hsl(var(--warning))]" aria-hidden="true" />
                  <p class="mt-2 text-[11px] text-[hsl(var(--warning))]">没有生成附件画像</p>
                </div>
              </article>

              <article class="rounded-lg border p-4">
                <div class="flex items-center justify-between gap-2">
                  <div class="flex items-center gap-2">
                    <BookOpenText class="h-4 w-4 text-primary" aria-hidden="true" />
                    <h3 class="text-xs font-semibold">文献调研</h3>
                  </div>
                  <span class="rounded-full border px-2 py-0.5 text-[9px] font-medium" :class="statusClass(String(literature.status || snapshot.research.outcome.literature_status))">{{ statusLabel(String(literature.status || snapshot.research.outcome.literature_status)) }}</span>
                </div>
                <div class="mt-3 grid grid-cols-2 gap-2 text-center">
                  <div class="rounded-md bg-[hsl(var(--surface-subtle))] px-2 py-2">
                    <div class="font-mono text-sm font-semibold">{{ literatureQueries.length }}</div>
                    <div class="text-[9px] text-muted-foreground">检索式</div>
                  </div>
                  <div class="rounded-md bg-[hsl(var(--surface-subtle))] px-2 py-2">
                    <div class="font-mono text-sm font-semibold">{{ (literature.kept_paper_count ?? literature.paper_count) || 0 }}</div>
                    <div class="text-[9px] text-muted-foreground">精选文献</div>
                  </div>
                </div>
                <div
                  v-if="literatureFilteredCount > 0"
                  class="mt-3 rounded-md border border-[hsl(var(--warning)/0.25)] bg-[hsl(var(--warning-subtle))] px-3 py-2 text-[10px] leading-5 text-muted-foreground"
                >
                  另有 {{ literatureFilteredCount }} 篇检索命中因与赛题相关性不足被过滤，不计入上方列表。
                </div>
                <div v-if="literatureQueries.length" class="mt-3">
                  <h4 class="text-[10px] font-semibold text-muted-foreground">实际执行的检索式</h4>
                  <ul class="mt-1.5 space-y-1 text-[11px] leading-5 text-secondary">
                    <li v-for="query in literatureQueries" :key="query" class="break-words font-mono">{{ query }}</li>
                  </ul>
                </div>
                <div v-if="literatureErrors.length" class="mt-3 rounded-md border border-[hsl(var(--warning)/0.35)] bg-[hsl(var(--warning-subtle))] px-3 py-2.5">
                  <h4 class="flex items-center gap-1.5 text-[10px] font-semibold text-[hsl(var(--warning))]">
                    <AlertTriangle class="h-3.5 w-3.5" aria-hidden="true" />失败原因
                  </h4>
                  <ul class="mt-1.5 space-y-1 text-[11px] leading-5 text-[hsl(var(--warning))]">
                    <li v-for="error in literatureErrors" :key="error" class="break-words">{{ error }}</li>
                  </ul>
                </div>
                <ul v-if="literaturePapers.length" class="mt-3 divide-y rounded-md border">
                  <li v-for="paper in literaturePapers" :key="String(paper.title)" class="px-3 py-2.5">
                    <a v-if="paper.url" :href="String(paper.url)" target="_blank" rel="noopener noreferrer" class="text-[11px] font-medium leading-5 text-primary hover:underline">{{ paper.title }}</a>
                    <p v-else class="text-[11px] font-medium leading-5">{{ paper.title }}</p>
                    <p class="mt-0.5 text-[9px] text-muted-foreground">
                      {{ paper.publication_year || '年份未知' }} · 被引 {{ paper.citations_count || 0 }}
                      <span v-if="paper.source"> · {{ paper.source }}</span>
                      <span v-if="paper.doi"> · DOI {{ paper.doi }}</span>
                    </p>
                  </li>
                </ul>
              </article>

              <div v-if="snapshot.research.outcome.issues.length" class="rounded-lg border border-[hsl(var(--warning)/0.35)] bg-[hsl(var(--warning-subtle))] px-4 py-3 lg:col-span-2">
                <h3 class="text-[10px] font-semibold text-[hsl(var(--warning))]">本阶段尚未解决的证据缺口</h3>
                <ul class="mt-1.5 space-y-1 text-xs leading-5 text-[hsl(var(--warning))]">
                  <li v-for="issue in snapshot.research.outcome.issues" :key="issue">• {{ issue }}</li>
                </ul>
              </div>
            </div>
          </section>

          <section class="app-panel overflow-hidden" aria-labelledby="refined-stage-title">
            <header class="flex flex-wrap items-start justify-between gap-3 border-b bg-[hsl(var(--surface-subtle))] px-4 py-3">
              <div class="flex items-start gap-3">
                <span class="flex h-8 w-8 items-center justify-center rounded-md border bg-card font-mono text-[10px] font-semibold">04</span>
                <div>
                  <h2 id="refined-stage-title" class="text-sm font-semibold">基于真实证据修正后的题目理解</h2>
                  <p class="mt-0.5 text-[11px] text-muted-foreground">这是当前审批对象；下方完整展示每问全部字段，不截取原题代替分析。</p>
                </div>
              </div>
              <span class="rounded-full border px-2.5 py-1 text-[10px] font-medium" :class="statusClass(snapshot.refined_analysis.outcome.status)">{{ statusLabel(snapshot.refined_analysis.outcome.status) }}</span>
            </header>
            <div class="space-y-4 p-4 lg:p-5">
              <div v-if="snapshot.refined_analysis.outcome.issues.length" class="rounded-lg border border-[hsl(var(--warning)/0.35)] bg-[hsl(var(--warning-subtle))] px-4 py-3">
                <div class="flex items-start gap-2">
                  <AlertTriangle class="mt-0.5 h-4 w-4 shrink-0 text-[hsl(var(--warning))]" aria-hidden="true" />
                  <div>
                    <h3 class="text-xs font-semibold text-[hsl(var(--warning))]">分析已生成，但不能宣称证据核验完成</h3>
                    <ul class="mt-1.5 space-y-1 text-[11px] leading-5 text-[hsl(var(--warning))]">
                      <li v-for="issue in snapshot.refined_analysis.outcome.issues" :key="issue">• {{ issue }}</li>
                    </ul>
                  </div>
                </div>
              </div>
              <div v-if="snapshot.refined_analysis.analysis_summary" class="rounded-lg border-l-2 border-l-primary bg-[hsl(var(--surface-subtle))] px-4 py-3">
                <p class="text-[10px] font-semibold text-muted-foreground">数据校正版总体理解</p>
                <p class="mt-1 whitespace-pre-wrap text-sm leading-6 text-secondary">{{ snapshot.refined_analysis.analysis_summary }}</p>
              </div>
              <ProjectAnalysisCards :analyses="snapshot.refined_analysis.question_analyses" />
            </div>
          </section>

          <footer class="flex items-start gap-2 rounded-lg border bg-card px-4 py-3 text-[11px] leading-5 text-muted-foreground">
            <FileText class="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
            本页数据源：workflow_state.json、problem_analysis.json、literature_review.json。Copilot 只负责解释，不再作为唯一结果展示位置。
          </footer>
        </template>

        <div v-else class="app-panel flex min-h-56 items-center justify-center p-6 text-center">
          <div>
            <AlertTriangle class="mx-auto h-5 w-5 text-[hsl(var(--warning))]" aria-hidden="true" />
            <p class="mt-2 text-xs font-medium">尚未读取到阶段产物</p>
            <p class="mt-1 text-[10px] text-muted-foreground">点击“刷新产物”重新读取当前任务检查点。</p>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>
