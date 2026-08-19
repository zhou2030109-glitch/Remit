<script setup lang="ts">
import { useTaskStore } from "@/stores/task";
import type { CitationEntry, MethodCard } from "@/utils/response";
import {
	BookMarked,
	BookOpenCheck,
	ExternalLink,
	FileSearch,
	FlaskConical,
	Quote,
	ScrollText,
} from "lucide-vue-next";
import { computed, ref } from "vue";

// ---- State ----

const taskStore = useTaskStore();
const activeCardId = ref("");

// ---- Computed ----

const evidence = computed(() => taskStore.workspaceSnapshot?.method_evidence);
const cards = computed<MethodCard[]>(() => evidence.value?.method_cards ?? []);
const candidates = computed(() => evidence.value?.candidates ?? []);
const citations = computed<CitationEntry[]>(
	() => evidence.value?.citation_entries ?? [],
);
const finalCitations = computed<CitationEntry[]>(
	() => evidence.value?.final_citations ?? [],
);

const fullTextCount = computed(
	() =>
		cards.value.filter((card) => card.evidence_level === "full_text").length,
);

/** 方法卡按小问分组，保持小问顺序稳定 */
const cardsByQuestion = computed(() => {
	const groups = new Map<string, MethodCard[]>();
	for (const card of cards.value) {
		const list = groups.get(card.question_key) ?? [];
		list.push(card);
		groups.set(card.question_key, list);
	}
	return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));
});

const activeCard = computed(
	() =>
		cards.value.find((card) => card.card_id === activeCardId.value) ??
		cards.value[0] ??
		null,
);

/** 当前方法卡衍生出的候选方案 */
const activeCandidates = computed(() =>
	candidates.value.filter(
		(item) => item.source_card_id === activeCard.value?.card_id,
	),
);

/** 当前方法卡的代码验证裁决 */
const activeDecisions = computed(() =>
	citations.value.filter((item) => item.card_id === activeCard.value?.card_id),
);

const stageSteps = computed(() => [
	{ label: "入选文献", value: cards.value.length },
	{ label: "读到全文", value: fullTextCount.value },
	{
		label: "衍生候选",
		value: candidates.value.filter((i) => i.source_card_id).length,
	},
	{ label: "最终引用", value: finalCitations.value.length },
]);

// ---- Methods ----

const decisionClass = (decision: string) => {
	if (decision === "adopted")
		return "bg-[hsl(var(--success)/0.12)] text-[hsl(var(--success))]";
	if (decision === "modified")
		return "bg-[hsl(var(--info)/0.12)] text-[hsl(var(--info))]";
	return "bg-muted text-muted-foreground";
};

const selectCard = (cardId: string) => {
	activeCardId.value = cardId;
};
</script>

<template>
  <div class="flex h-full min-h-0 flex-col">
    <header class="shrink-0 border-b bg-card px-5 py-4">
      <p class="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">文献与方法</p>
      <h2 class="mt-1 text-lg font-semibold tracking-tight">方法证据链</h2>
      <p class="mt-1 text-xs text-muted-foreground">
        检索 → 逐题筛选 → 读全文提方法卡 → 生成候选方案 → 小样本代码验证 → 只保留真正影响建模的引用。
      </p>
      <div v-if="cards.length" class="mt-3 flex flex-wrap gap-2">
        <span
          v-for="step in stageSteps"
          :key="step.label"
          class="inline-flex items-center gap-1.5 rounded-md border bg-[hsl(var(--surface-subtle))] px-2.5 py-1 text-[10px]"
        >
          <span class="text-muted-foreground">{{ step.label }}</span>
          <span class="mono-data font-semibold">{{ step.value }}</span>
        </span>
      </div>
    </header>

    <div v-if="!cards.length" class="flex min-h-0 flex-1 items-center justify-center p-6 text-center">
      <div class="max-w-sm">
        <FileSearch class="mx-auto h-6 w-6 text-muted-foreground" aria-hidden="true" />
        <h3 class="mt-3 text-xs font-semibold">尚未产出方法卡</h3>
        <p class="mt-1 text-[11px] leading-5 text-muted-foreground">
          文献调研节点完成后，这里会显示每个小问精读的 2～3 篇论文、提取的方法卡，
          以及每篇文献经代码验证后是被采用、修改还是放弃。
        </p>
      </div>
    </div>

    <div v-else class="grid min-h-0 flex-1 lg:grid-cols-[260px_minmax(0,1fr)]">
      <aside class="min-h-0 overflow-y-auto border-r bg-[hsl(var(--surface-subtle))] p-3" aria-label="方法卡列表">
        <div v-for="[questionKey, group] in cardsByQuestion" :key="questionKey" class="mb-3">
          <p class="px-2 pb-1.5 text-[10px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
            {{ questionKey }}
          </p>
          <div class="space-y-1">
            <button
              v-for="card in group"
              :key="card.card_id"
              type="button"
              class="flex w-full flex-col gap-1 rounded-md px-2 py-2 text-left text-xs transition-colors"
              :class="card.card_id === activeCard?.card_id
                ? 'bg-card font-medium shadow-sm ring-1 ring-border'
                : 'text-secondary hover:bg-card/70'"
              @click="selectCard(card.card_id)"
            >
              <span class="flex items-start gap-1.5">
                <BookMarked class="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
                <span class="min-w-0 flex-1 line-clamp-2" :title="card.title">{{ card.title }}</span>
              </span>
              <span class="flex items-center gap-1.5 pl-5 text-[9px] text-muted-foreground">
                <span
                  class="rounded px-1 py-0.5"
                  :class="card.evidence_level === 'full_text'
                    ? 'bg-[hsl(var(--success)/0.12)] text-[hsl(var(--success))]'
                    : 'bg-muted'"
                >
                  {{ card.evidence_level === 'full_text' ? '全文' : '仅摘要' }}
                </span>
                <span v-if="card.publication_year" class="mono-data">{{ card.publication_year }}</span>
              </span>
            </button>
          </div>
        </div>
      </aside>

      <section v-if="activeCard" class="min-h-0 overflow-y-auto p-5" aria-label="方法卡详情">
        <article class="app-panel p-4">
          <h3 class="text-sm font-semibold leading-6">{{ activeCard.title }}</h3>
          <p v-if="activeCard.citation" class="mt-1 text-[11px] leading-5 text-muted-foreground">{{ activeCard.citation }}</p>
          <div class="mt-2 flex flex-wrap items-center gap-2 text-[10px]">
            <span class="rounded-md bg-muted px-2 py-0.5 mono-data">{{ activeCard.card_id }}</span>
            <a
              v-if="activeCard.url"
              :href="activeCard.url"
              target="_blank"
              rel="noopener noreferrer"
              class="inline-flex items-center gap-1 text-[hsl(var(--info))] underline"
            >
              原文链接
              <ExternalLink class="h-3 w-3" aria-hidden="true" />
            </a>
            <a
              v-if="activeCard.fulltext_source"
              :href="activeCard.fulltext_source"
              target="_blank"
              rel="noopener noreferrer"
              class="inline-flex items-center gap-1 text-muted-foreground underline"
            >
              全文 PDF
              <ExternalLink class="h-3 w-3" aria-hidden="true" />
            </a>
          </div>
          <p v-if="activeCard.relevance_reason" class="mt-3 rounded-md bg-[hsl(var(--surface-subtle))] px-3 py-2 text-[11px] leading-5">
            <span class="font-medium">入选理由：</span>{{ activeCard.relevance_reason }}
          </p>

          <dl class="mt-4 space-y-3 text-[11px] leading-5">
            <div v-if="activeCard.problem_solved">
              <dt class="font-semibold">解决的问题</dt>
              <dd class="mt-0.5 text-muted-foreground">{{ activeCard.problem_solved }}</dd>
            </div>
            <div v-if="activeCard.method">
              <dt class="font-semibold">采用的模型 / 算法</dt>
              <dd class="mt-0.5 text-muted-foreground">{{ activeCard.method }}</dd>
            </div>
            <div v-if="activeCard.key_steps.length">
              <dt class="font-semibold">关键步骤</dt>
              <dd class="mt-0.5">
                <ol class="list-decimal space-y-0.5 pl-4 text-muted-foreground">
                  <li v-for="(step, index) in activeCard.key_steps" :key="index">{{ step }}</li>
                </ol>
              </dd>
            </div>
            <div v-if="activeCard.applicable_conditions.length">
              <dt class="font-semibold">适用条件</dt>
              <dd class="mt-0.5">
                <ul class="list-disc space-y-0.5 pl-4 text-muted-foreground">
                  <li v-for="(item, index) in activeCard.applicable_conditions" :key="index">{{ item }}</li>
                </ul>
              </dd>
            </div>
            <div class="grid gap-3 sm:grid-cols-2">
              <div v-if="activeCard.strengths.length">
                <dt class="font-semibold text-[hsl(var(--success))]">优点</dt>
                <dd class="mt-0.5">
                  <ul class="list-disc space-y-0.5 pl-4 text-muted-foreground">
                    <li v-for="(item, index) in activeCard.strengths" :key="index">{{ item }}</li>
                  </ul>
                </dd>
              </div>
              <div v-if="activeCard.limitations.length">
                <dt class="font-semibold text-[hsl(var(--warning))]">缺点与局限</dt>
                <dd class="mt-0.5">
                  <ul class="list-disc space-y-0.5 pl-4 text-muted-foreground">
                    <li v-for="(item, index) in activeCard.limitations" :key="index">{{ item }}</li>
                  </ul>
                </dd>
              </div>
            </div>
            <div v-if="activeCard.key_parameters.length">
              <dt class="font-semibold">关键参数</dt>
              <dd class="mt-0.5 text-muted-foreground">{{ activeCard.key_parameters.join("；") }}</dd>
            </div>
            <div v-if="activeCard.competition_adaptation">
              <dt class="font-semibold">竞赛适配</dt>
              <dd class="mt-0.5 text-muted-foreground">{{ activeCard.competition_adaptation }}</dd>
            </div>
          </dl>
        </article>

        <section v-if="activeCard.source_locations.length" class="mt-4 app-panel p-4" aria-labelledby="source-heading">
          <h4 id="source-heading" class="flex items-center gap-1.5 text-xs font-semibold">
            <ScrollText class="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
            原文位置
          </h4>
          <ul class="mt-2 space-y-2">
            <li
              v-for="(location, index) in activeCard.source_locations"
              :key="index"
              class="rounded-md border-l-2 border-[hsl(var(--info))] bg-[hsl(var(--surface-subtle))] px-3 py-2"
            >
              <p class="text-[10px] font-medium">
                {{ location.section }}<span v-if="location.page" class="mono-data"> · 第 {{ location.page }} 页</span>
              </p>
              <p v-if="location.quote" class="mt-1 text-[10px] italic leading-4 text-muted-foreground">“{{ location.quote }}”</p>
            </li>
          </ul>
        </section>

        <section v-if="activeCandidates.length" class="mt-4 app-panel p-4" aria-labelledby="candidate-heading">
          <h4 id="candidate-heading" class="flex items-center gap-1.5 text-xs font-semibold">
            <FlaskConical class="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
            由这篇文献衍生的候选方案
          </h4>
          <ul class="mt-2 space-y-2">
            <li v-for="candidate in activeCandidates" :key="candidate.name" class="rounded-md border px-3 py-2">
              <p class="text-[11px] font-medium">
                {{ candidate.name }}
                <span class="ml-1 rounded bg-muted px-1 py-0.5 text-[9px] text-muted-foreground">{{ candidate.role }}</span>
              </p>
              <p class="mt-1 text-[10px] leading-4 text-muted-foreground">{{ candidate.approach }}</p>
              <p v-if="candidate.adaptation" class="mt-1 text-[10px] leading-4">
                <span class="font-medium">相对原文的修改：</span>
                <span class="text-muted-foreground">{{ candidate.adaptation }}</span>
              </p>
            </li>
          </ul>
        </section>

        <section v-if="activeDecisions.length" class="mt-4 app-panel p-4" aria-labelledby="decision-heading">
          <h4 id="decision-heading" class="flex items-center gap-1.5 text-xs font-semibold">
            <BookOpenCheck class="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
            代码验证后的裁决
          </h4>
          <ul class="mt-2 space-y-2">
            <li v-for="entry in activeDecisions" :key="`${entry.question_key}-${entry.card_id}`" class="rounded-md border px-3 py-2">
              <p class="flex items-center gap-2 text-[11px]">
                <span class="mono-data text-muted-foreground">{{ entry.question_key }}</span>
                <span class="rounded px-1.5 py-0.5 text-[10px] font-medium" :class="decisionClass(entry.decision)">
                  {{ entry.decision_label }}
                </span>
                <span v-if="entry.is_selected_model" class="text-[10px] text-[hsl(var(--success))]">最终入选模型</span>
              </p>
              <p v-if="entry.evidence" class="mt-1 text-[10px] leading-4 text-muted-foreground">
                <span class="font-medium">实验依据：</span>{{ entry.evidence }}
              </p>
              <p v-if="entry.influence" class="mt-1 text-[10px] leading-4 text-muted-foreground">
                <span class="font-medium">对建模的影响：</span>{{ entry.influence }}
              </p>
            </li>
          </ul>
        </section>

        <section class="mt-4 app-panel p-4" aria-labelledby="final-heading">
          <h4 id="final-heading" class="flex items-center gap-1.5 text-xs font-semibold">
            <Quote class="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
            论文最终参考文献
          </h4>
          <p class="mt-1 text-[10px] text-muted-foreground">只有经代码验证后被采用或修改采用的文献才会写进论文。</p>
          <ol v-if="finalCitations.length" class="mt-2 list-decimal space-y-1.5 pl-4">
            <li v-for="entry in finalCitations" :key="entry.card_id" class="text-[10px] leading-4">
              <span>{{ entry.citation || entry.title }}</span>
              <span class="ml-1 rounded px-1 py-0.5 text-[9px]" :class="decisionClass(entry.decision)">{{ entry.decision_label }}</span>
            </li>
          </ol>
          <p v-else class="mt-2 text-[10px] text-muted-foreground">
            探索实验尚未完成裁决；完成后这里会给出可直接写进论文的参考文献清单。
          </p>
        </section>
      </section>
    </div>
  </div>
</template>
