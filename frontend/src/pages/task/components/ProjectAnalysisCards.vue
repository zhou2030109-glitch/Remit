<script setup lang="ts">
import type { StructuredQuestionAnalysis } from "@/utils/response";
import { computed } from "vue";

const props = defineProps<{
	analyses: Record<string, StructuredQuestionAnalysis>;
}>();

type ListField = Exclude<keyof StructuredQuestionAnalysis, "objective">;
const fields: Array<{ key: ListField; label: string }> = [
	{ key: "input_data", label: "输入数据" },
	{ key: "decision_variables", label: "决策变量" },
	{ key: "constraints", label: "约束" },
	{ key: "expected_outputs", label: "输出" },
	{ key: "dependencies", label: "依赖关系" },
	{ key: "risks", label: "风险" },
	{ key: "validation_requirements", label: "验证要求" },
	{ key: "data_evidence", label: "附件与文献证据" },
];

const entries = computed(() =>
	Object.entries(props.analyses).sort(([left], [right]) =>
		left.localeCompare(right, undefined, { numeric: true }),
	),
);

function questionNumber(key: string): string {
	return key.match(/\d+/)?.[0] ?? key;
}
</script>

<template>
  <div v-if="entries.length" class="space-y-3">
    <article
      v-for="[key, analysis] in entries"
      :key="key"
      class="overflow-hidden rounded-lg border bg-card"
    >
      <header class="grid gap-2 border-b bg-[hsl(var(--surface-subtle))] px-4 py-3 sm:grid-cols-[52px_minmax(0,1fr)]">
        <span class="flex h-8 w-10 items-center justify-center rounded-md bg-foreground font-mono text-[10px] font-semibold text-background">
          Q{{ questionNumber(key) }}
        </span>
        <div>
          <p class="text-[10px] font-semibold text-muted-foreground">目标</p>
          <h4 class="mt-0.5 whitespace-pre-wrap text-sm font-semibold leading-6">
            {{ analysis.objective || "未生成目标" }}
          </h4>
        </div>
      </header>

      <dl class="grid gap-px bg-border sm:grid-cols-2">
        <div
          v-for="field in fields"
          :key="field.key"
          class="bg-card px-4 py-3"
          :class="field.key === 'data_evidence' ? 'border-l-2 border-l-primary sm:col-span-2' : ''"
        >
          <dt class="text-[10px] font-semibold text-muted-foreground">{{ field.label }}</dt>
          <dd class="mt-1.5">
            <ul v-if="analysis[field.key]?.length" class="space-y-1.5 text-xs leading-5 text-secondary">
              <li v-for="(item, index) in analysis[field.key]" :key="`${field.key}-${index}`" class="flex gap-2">
                <span class="mt-2 h-1 w-1 shrink-0 rounded-full bg-muted-foreground/55" aria-hidden="true" />
                <span class="whitespace-pre-wrap break-words">{{ item }}</span>
              </li>
            </ul>
            <span v-else class="text-[11px] text-[hsl(var(--warning))]">未生成或待核验</span>
          </dd>
        </div>
      </dl>
    </article>
  </div>
  <div v-else class="rounded-lg border border-dashed px-4 py-8 text-center text-xs text-muted-foreground">
    本阶段尚未生成逐题结构化分析。
  </div>
</template>
