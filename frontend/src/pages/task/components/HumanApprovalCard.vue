<script setup lang="ts">
import { Button } from "@/components/ui/button";
import {
	Collapsible,
	CollapsibleContent,
	CollapsibleTrigger,
} from "@/components/ui/collapsible";
import CsvPreviewTable from "@/pages/task/components/CsvPreviewTable.vue";
import type {
	ApprovalMessage,
	MetricExplanation,
	StructuredQuestionAnalysis,
} from "@/utils/response";
import {
	Ban,
	Check,
	ChevronDown,
	ClipboardCheck,
	FileText,
	LoaderCircle,
	LockKeyhole,
	MessageCircleQuestion,
	RotateCcw,
} from "lucide-vue-next";
import { computed } from "vue";

const props = defineProps<{
	approval: ApprovalMessage;
	deciding: boolean;
}>();

defineEmits<{
	approve: [];
	revise: [];
	explain: [];
	veto: [feedback: string];
}>();

const explain = computed(() => props.approval.explain ?? {});
type AnalysisListKey = Exclude<keyof StructuredQuestionAnalysis, "objective">;
const analysisFields: Array<{ key: AnalysisListKey; label: string }> = [
	{ key: "input_data", label: "输入数据" },
	{ key: "decision_variables", label: "决策变量" },
	{ key: "constraints", label: "约束" },
	{ key: "expected_outputs", label: "输出" },
	{ key: "dependencies", label: "依赖关系" },
	{ key: "risks", label: "风险" },
	{ key: "validation_requirements", label: "验证要求" },
	{ key: "data_evidence", label: "附件 / 文献证据" },
];
const structuredAnalyses = computed(
	() =>
		Object.entries(explain.value.question_analyses ?? {}).sort(
			([left], [right]) =>
				left.localeCompare(right, undefined, { numeric: true }),
		) as Array<[string, StructuredQuestionAnalysis]>,
);
const hasExplain = computed(() =>
	Boolean(
		explain.value.what_happened ||
			explain.value.evidence_issues?.length ||
			structuredAnalyses.value.length ||
			explain.value.key_numbers?.length ||
			explain.value.next_step ||
			explain.value.candidates?.length ||
			explain.value.pilot_table?.rows?.length ||
			explain.value.citation_table?.rows?.length,
	),
);

function questionLabel(key: string): string {
	const number = key.match(/\d+/)?.[0];
	return number ? `问题 ${number}` : key;
}

function verdictDot(verdict: MetricExplanation["verdict"]) {
	if (verdict === "good") return "bg-[hsl(var(--success))]";
	if (verdict === "ok") return "bg-[hsl(var(--warning))]";
	if (verdict === "poor") return "bg-[hsl(var(--danger))]";
	return "bg-muted-foreground/50";
}

function vetoFeedback(candidate: {
	question: string;
	name: string;
}): string {
	return `关于 ${candidate.question}：否决候选方案「${candidate.name}」，不要使用它；请给出替代方案并重新提交总体建模方案。`;
}
</script>

<template>
  <aside
    class="w-full"
    data-testid="human-approval-gate"
    aria-label="人工审核"
    aria-live="assertive"
  >
    <Collapsible v-slot="{ open }" :default-open="structuredAnalyses.length > 0">
      <div class="relative overflow-hidden rounded-xl border border-amber-200 bg-white shadow-[0_8px_24px_-20px_rgba(15,23,42,0.55)]">
        <div class="absolute inset-y-0 left-0 w-1 bg-amber-400" aria-hidden="true" />

        <div class="px-3 py-2.5 pl-4">
          <div class="flex min-w-0 items-start gap-2.5">
            <span class="mt-0.5 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-amber-100 text-amber-700">
              <ClipboardCheck class="h-4 w-4" aria-hidden="true" />
            </span>

            <div class="min-w-0 flex-1">
              <div class="flex min-w-0 items-center gap-2">
                <span class="shrink-0 text-[11px] font-semibold uppercase tracking-wide text-amber-700">
                  等待你确认
                </span>
                <span class="inline-flex min-w-0 items-center gap-1 text-[11px] text-slate-400">
                  <LockKeyhole class="h-3 w-3 shrink-0" aria-hidden="true" />
                  后续已暂停
                </span>
                <span v-if="approval.revision_count > 0" class="shrink-0 text-[11px] text-primary">
                  第 {{ approval.revision_count }} 次返修
                </span>
              </div>
              <h2 class="mt-0.5 truncate text-sm font-semibold text-slate-900">
                {{ approval.node_label }}
              </h2>
              <p class="mt-0.5 truncate text-xs text-slate-500" :title="approval.summary">
                {{ approval.summary }}
              </p>
            </div>

            <CollapsibleTrigger as-child>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                class="h-8 shrink-0 px-2 text-xs text-slate-500 hover:text-slate-900"
                data-testid="toggle-approval-details"
              >
                {{ open ? "收起" : "详情" }}
                <ChevronDown
                  class="ml-1 h-3.5 w-3.5 transition-transform duration-200 motion-reduce:transition-none"
                  :class="open ? 'rotate-180' : ''"
                  aria-hidden="true"
                />
              </Button>
            </CollapsibleTrigger>
          </div>

          <div class="mt-2.5 flex items-center justify-end gap-2">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              class="h-8 px-2 text-xs text-slate-500 hover:text-slate-900"
              :disabled="deciding"
              data-testid="ask-ai-explain-button"
              @click="$emit('explain')"
            >
              <MessageCircleQuestion class="mr-1 h-3.5 w-3.5" aria-hidden="true" />
              让 AI 解释
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              class="h-8 border-slate-200 bg-white px-3 text-xs text-slate-700"
              :disabled="deciding"
              data-testid="request-revision-button"
              @click="$emit('revise')"
            >
              <RotateCcw class="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
              提修改意见
            </Button>
            <Button
              type="button"
              size="sm"
              class="h-8 bg-primary px-3 text-xs text-primary-foreground hover:bg-primary/90"
              :disabled="deciding"
              data-testid="approve-and-continue-button"
              @click="$emit('approve')"
            >
              <LoaderCircle v-if="deciding" class="mr-1.5 h-3.5 w-3.5 animate-spin motion-reduce:animate-none" aria-hidden="true" />
              <Check v-else class="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
              {{ deciding ? "提交中…" : "批准并继续" }}
            </Button>
          </div>
        </div>

        <CollapsibleContent>
          <div class="border-t border-slate-100 bg-slate-50/70 px-4 py-3">
            <template v-if="hasExplain">
              <div v-if="explain.what_happened" data-testid="approval-what-happened">
                <p class="text-[10px] font-semibold uppercase tracking-wide text-slate-400">这一步做了什么</p>
                <p class="mt-1 text-xs leading-5 text-slate-600">{{ explain.what_happened }}</p>
              </div>
              <div v-if="explain.evidence_issues?.length" class="mt-2.5 rounded-md border border-[hsl(var(--warning)/0.35)] bg-[hsl(var(--warning-subtle))] px-3 py-2" data-testid="approval-evidence-issues">
                <p class="text-[10px] font-semibold text-[hsl(var(--warning))]">证据核验尚未完整</p>
                <ul class="mt-1 space-y-1 text-[11px] leading-4 text-[hsl(var(--warning))]">
                  <li v-for="issue in explain.evidence_issues" :key="issue">• {{ issue }}</li>
                </ul>
              </div>
              <div v-if="explain.key_numbers?.length" class="mt-2.5" data-testid="approval-key-numbers">
                <p class="text-[10px] font-semibold uppercase tracking-wide text-slate-400">关键数字什么意思</p>
                <ul class="mt-1 space-y-1.5">
                  <li v-for="item in explain.key_numbers" :key="item.name" class="flex gap-2 text-xs leading-5 text-slate-600">
                    <span class="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full" :class="verdictDot(item.verdict)" />
                    <span><strong class="font-medium text-slate-800">{{ item.friendly_name }} = {{ item.value_text }}</strong>：{{ item.meaning }}</span>
                  </li>
                </ul>
              </div>
              <section v-if="structuredAnalyses.length" class="mt-3" data-testid="approval-question-analyses">
                <div class="flex items-center justify-between gap-3">
                  <p class="text-[10px] font-semibold uppercase tracking-wide text-slate-400">逐题结构化理解</p>
                  <span class="rounded-full border border-slate-200 bg-white px-2 py-0.5 text-[9px] font-medium text-slate-500">AI 生成 · 人工确认</span>
                </div>
                <div class="mt-1.5 space-y-2.5">
                  <article
                    v-for="([questionKey, analysis], index) in structuredAnalyses"
                    :key="questionKey"
                    class="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm"
                  >
                    <header class="flex items-start gap-2 border-b border-slate-100 bg-slate-50/70 px-3 py-2">
                      <span class="inline-flex h-5 min-w-5 shrink-0 items-center justify-center rounded bg-slate-900 px-1 text-[9px] font-semibold text-white">
                        {{ index + 1 }}
                      </span>
                      <div class="min-w-0">
                        <p class="text-[10px] font-semibold text-slate-500">{{ questionLabel(questionKey) }} · 核心目标</p>
                        <h3 class="mt-0.5 text-xs font-semibold leading-5 text-slate-900">{{ analysis.objective }}</h3>
                      </div>
                    </header>
                    <dl class="grid gap-px bg-slate-100 sm:grid-cols-2">
                      <div
                        v-for="field in analysisFields"
                        :key="field.key"
                        class="bg-white px-3 py-2"
                        :class="field.key === 'data_evidence' ? 'border-l-2 border-l-primary/50 sm:col-span-2' : ''"
                      >
                        <dt class="text-[9px] font-semibold uppercase tracking-wide text-slate-400">{{ field.label }}</dt>
                        <dd class="mt-1">
                          <ul v-if="analysis[field.key]?.length" class="space-y-0.5 text-[11px] leading-4 text-slate-600">
                            <li v-for="item in analysis[field.key]" :key="item" class="flex gap-1.5">
                              <span class="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-slate-300" aria-hidden="true" />
                              <span>{{ item }}</span>
                            </li>
                          </ul>
                          <span v-else class="text-[10px] text-amber-600">待核验</span>
                        </dd>
                      </div>
                    </dl>
                  </article>
                </div>
              </section>
              <div v-if="explain.pilot_table?.rows?.length" class="mt-2.5" data-testid="approval-pilot-table">
                <p class="text-[10px] font-semibold uppercase tracking-wide text-slate-400">小实验真实对比（相同数据划分）</p>
                <CsvPreviewTable
                  class="mt-1"
                  :columns="explain.pilot_table.columns"
                  :rows="explain.pilot_table.rows"
                />
              </div>
              <div v-if="explain.citation_table?.rows?.length" class="mt-2.5" data-testid="approval-citation-table">
                <p class="text-[10px] font-semibold uppercase tracking-wide text-slate-400">参考文献裁决（只有采用/修改后采用会进论文）</p>
                <CsvPreviewTable
                  class="mt-1"
                  :columns="explain.citation_table.columns"
                  :rows="explain.citation_table.rows"
                />
              </div>
              <div v-if="explain.candidates?.length" class="mt-2.5" data-testid="approval-candidates">
                <p class="text-[10px] font-semibold uppercase tracking-wide text-slate-400">方法库 Top-K 与评审候选（可直接否决）</p>
                <ul class="mt-1 space-y-1">
                  <li v-for="candidate in explain.candidates" :key="`${candidate.question}-${candidate.name}`" class="flex items-start gap-2 rounded-md border border-slate-200 bg-white px-2 py-1.5">
                    <div class="min-w-0 flex-1 text-[11px] leading-4">
                      <span class="font-medium text-slate-800">{{ candidate.question }} · {{ candidate.name }}</span>
                      <span v-if="candidate.role" class="ml-1 rounded bg-slate-100 px-1 text-[9px] text-slate-500">{{ candidate.role }}</span>
                      <p v-if="candidate.reason" class="mt-0.5 text-[10px] text-slate-500">{{ candidate.reason }}</p>
                    </div>
                    <button
                      type="button"
                      class="inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-[10px] text-red-600 hover:bg-red-50"
                      :disabled="deciding"
                      @click="$emit('veto', vetoFeedback(candidate))"
                    >
                      <Ban class="h-3 w-3" aria-hidden="true" />
                      否决
                    </button>
                  </li>
                </ul>
              </div>
              <div v-if="explain.next_step" class="mt-2.5" data-testid="approval-next-step">
                <p class="text-[10px] font-semibold uppercase tracking-wide text-slate-400">批准后会发生什么</p>
                <p class="mt-1 text-xs leading-5 text-slate-600">{{ explain.next_step }}</p>
              </div>
              <div v-if="explain.revise_hint" class="mt-2.5 rounded-md bg-amber-50 px-2.5 py-2">
                <p class="text-[10px] font-semibold text-amber-700">不满意怎么办</p>
                <p class="mt-0.5 text-[11px] leading-4 text-amber-800">{{ explain.revise_hint }}</p>
              </div>
            </template>
            <p v-else class="max-h-28 overflow-y-auto pr-2 text-xs leading-5 text-slate-600">
              {{ approval.summary }}
            </p>
            <div v-if="approval.artifacts.length" class="mt-2.5 flex flex-wrap items-center gap-1.5">
              <span class="mr-1 inline-flex items-center gap-1 text-[11px] font-medium text-slate-500">
                <FileText class="h-3.5 w-3.5" aria-hidden="true" />
                本步产物
              </span>
              <code
                v-for="artifact in approval.artifacts.slice(0, 5)"
                :key="artifact"
                class="max-w-48 truncate rounded-md border border-slate-200 bg-white px-1.5 py-0.5 text-[11px] text-slate-600"
                :title="artifact"
              >{{ artifact }}</code>
              <span v-if="approval.artifacts.length > 5" class="text-[11px] text-slate-400">
                +{{ approval.artifacts.length - 5 }} 项
              </span>
            </div>
          </div>
        </CollapsibleContent>
      </div>
    </Collapsible>
  </aside>
</template>
