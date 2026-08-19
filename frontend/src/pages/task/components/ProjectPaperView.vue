<script setup lang="ts">
import { renderMarkdown } from "@/utils/markdown";
import type { WriterMessage } from "@/utils/response";
import { BookOpen, CheckCircle2, FileText, ListTree } from "lucide-vue-next";
import { computed } from "vue";

const props = defineProps<{
	messages: WriterMessage[];
	writerSequence: string[];
}>();

const sections = computed(() => {
	const mapped = props.messages
		.filter((message) => Boolean(message.content?.trim()))
		.map((message, index) => ({
			id: `paper-section-${index}`,
			title: message.sub_title || `论文片段 ${index + 1}`,
			content: message.content ?? "",
			rendered: renderMarkdown(message.content ?? ""),
			originalIndex: index,
		}));
	if (!props.writerSequence.length) return mapped;
	return mapped.sort((a, b) => {
		const aIndex = props.writerSequence.indexOf(a.title);
		const bIndex = props.writerSequence.indexOf(b.title);
		if (aIndex === -1 && bIndex === -1)
			return a.originalIndex - b.originalIndex;
		if (aIndex === -1) return 1;
		if (bIndex === -1) return -1;
		return aIndex - bIndex;
	});
});

const totalCharacters = computed(() =>
	sections.value.reduce((total, section) => total + section.content.length, 0),
);
</script>

<template>
  <section class="flex h-full min-h-0 flex-col bg-background" aria-labelledby="paper-view-title">
    <header class="shrink-0 border-b bg-card px-5 py-4 lg:px-6">
      <div class="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p class="text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">Paper workspace</p>
          <h1 id="paper-view-title" class="mt-1 text-lg font-semibold tracking-tight">论文写作与版本预览</h1>
          <p class="mt-1 text-xs text-muted-foreground">按工作流目录组织真实论文片段，公式、表格和图像引用保持 Markdown 渲染。</p>
        </div>
        <div class="flex items-center gap-2 text-[10px] text-muted-foreground">
          <span class="rounded-md border bg-[hsl(var(--surface-subtle))] px-2 py-1">{{ sections.length }} 个章节</span>
          <span class="rounded-md border bg-[hsl(var(--surface-subtle))] px-2 py-1">{{ totalCharacters.toLocaleString('zh-CN') }} 字符</span>
        </div>
      </div>
    </header>

    <div class="grid min-h-0 flex-1 lg:grid-cols-[210px_minmax(0,1fr)]">
      <aside class="hidden min-h-0 border-r bg-[hsl(var(--surface-subtle))] p-3 lg:flex lg:flex-col" aria-label="论文目录">
        <div class="flex items-center gap-2 px-2 py-2">
          <ListTree class="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
          <h2 class="text-xs font-semibold">论文目录</h2>
        </div>
        <nav class="mt-1 min-h-0 flex-1 overflow-y-auto">
          <a v-for="(section, index) in sections" :key="section.id" :href="`#${section.id}`" class="flex items-start gap-2 rounded-md px-2 py-2 text-[11px] leading-4 text-secondary transition-colors hover:bg-card hover:text-foreground">
            <span class="mt-0.5 font-mono text-[9px] text-muted-foreground">{{ String(index + 1).padStart(2, '0') }}</span>
            <span class="line-clamp-2">{{ section.title }}</span>
          </a>
          <p v-if="!sections.length" class="px-2 py-4 text-[10px] leading-4 text-muted-foreground">论文手开始写作后，章节会按既定顺序出现在这里。</p>
        </nav>
        <div class="mt-3 border-t px-2 pt-3 text-[10px] leading-4 text-muted-foreground">
          <span class="inline-flex items-center gap-1 text-[hsl(var(--success))]"><CheckCircle2 class="h-3 w-3" />自动保存</span>
          <p class="mt-1">内容来自任务消息，不生成本地虚假版本。</p>
        </div>
      </aside>

      <main class="min-h-0 overflow-y-auto bg-[hsl(var(--surface-subtle))] p-4 sm:p-6 lg:p-8">
        <div v-if="sections.length" class="mx-auto max-w-[860px] space-y-4">
          <article v-for="section in sections" :id="section.id" :key="section.id" class="scroll-mt-4 overflow-hidden rounded-lg border bg-card shadow-[var(--shadow-panel)]">
            <header class="flex items-center justify-between gap-3 border-b px-5 py-3">
              <div class="flex min-w-0 items-center gap-2">
                <FileText class="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
                <h2 class="truncate text-xs font-semibold" :title="section.title">{{ section.title }}</h2>
              </div>
              <span class="shrink-0 text-[9px] text-muted-foreground">AI 生成 · 待人工终审</span>
            </header>
            <div class="artifact-document px-6 py-7 sm:px-9 sm:py-9" v-html="section.rendered" />
          </article>
        </div>

        <div v-else class="mx-auto flex min-h-[420px] max-w-[860px] items-center justify-center rounded-lg border border-dashed bg-card p-8 text-center">
          <div class="max-w-sm">
            <BookOpen class="mx-auto h-6 w-6 text-muted-foreground" aria-hidden="true" />
            <h2 class="mt-3 text-sm font-semibold">尚未生成论文内容</h2>
            <p class="mt-1 text-[11px] leading-5 text-muted-foreground">通过前置节点的人工验收后，论文手会按目录逐节生成可编辑的 Markdown 内容。</p>
          </div>
        </div>
      </main>
    </div>
  </section>
</template>

<style scoped>
.artifact-document {
	color: hsl(var(--foreground));
	font-size: 0.875rem;
	line-height: 1.85;
}

.artifact-document :deep(h1) {
	margin: 0 0 1.5rem;
	font-size: 1.55rem;
	line-height: 1.3;
}

.artifact-document :deep(h2) {
	margin: 1.8rem 0 0.75rem;
	font-size: 1.2rem;
	line-height: 1.4;
}

.artifact-document :deep(h3) {
	margin: 1.4rem 0 0.55rem;
	font-size: 1rem;
}

.artifact-document :deep(p) {
	margin: 0.7rem 0;
}

.artifact-document :deep(ul),
.artifact-document :deep(ol) {
	margin: 0.75rem 0;
	padding-left: 1.4rem;
}

.artifact-document :deep(table) {
	margin: 1rem 0;
	width: 100%;
	border-collapse: collapse;
	font-size: 0.78rem;
}

.artifact-document :deep(th),
.artifact-document :deep(td) {
	border: 1px solid hsl(var(--border));
	padding: 0.55rem 0.65rem;
	text-align: left;
}

.artifact-document :deep(th) {
	background: hsl(var(--surface-subtle));
	font-weight: 600;
}

.artifact-document :deep(pre) {
	overflow-x: auto;
	border: 1px solid hsl(var(--border));
	border-radius: 0.4rem;
	background: hsl(var(--muted));
	padding: 0.8rem;
}

.artifact-document :deep(img) {
	max-width: 100%;
	height: auto;
}
</style>
