<script setup lang="ts">
import { Button } from "@/components/ui/button";
import { useTaskStore } from "@/stores/task";
import {
	Check,
	ClipboardCopy,
	FileCode2,
	FileText,
	FolderOpen,
} from "lucide-vue-next";
import { computed, ref } from "vue";

const taskStore = useTaskStore();
const copiedPath = ref("");

const codeLocations = computed(() => {
	const seen = new Set<string>();
	return taskStore.executionSummaries
		.flatMap((summary) =>
			summary.code_locations.map((location) => ({
				...location,
				nodeLabel: summary.node_label,
			})),
		)
		.filter((location) => {
			const key = `${location.path}:${location.section}`;
			if (seen.has(key)) return false;
			seen.add(key);
			return true;
		});
});

const generatedArtifacts = computed(() => {
	const seen = new Set<string>();
	return taskStore.executionSummaries
		.flatMap((summary) => summary.artifacts)
		.filter((artifact) => {
			if (seen.has(artifact)) return false;
			seen.add(artifact);
			return true;
		});
});

async function copyPath(path: string) {
	if (!navigator.clipboard) return;
	await navigator.clipboard.writeText(path);
	copiedPath.value = path;
	window.setTimeout(() => {
		if (copiedPath.value === path) copiedPath.value = "";
	}, 1500);
}
</script>

<template>
  <div class="mx-auto w-full max-w-6xl p-5 lg:p-6">
    <div class="flex flex-wrap items-end justify-between gap-3 border-b pb-4">
      <div>
        <p class="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">代码与生成文件</p>
        <h2 class="mt-1 text-lg font-semibold tracking-tight">运行产物索引</h2>
        <p class="mt-1 text-xs text-muted-foreground">不在页面铺开整段代码；这里提供真实文件位置、Notebook 章节和对应求解节点。</p>
      </div>
      <span class="rounded-md border bg-card px-2.5 py-1.5 text-[11px] text-muted-foreground">
        {{ codeLocations.length }} 个代码位置 · {{ generatedArtifacts.length }} 项产物
      </span>
    </div>

    <div class="mt-5 grid gap-5 xl:grid-cols-[minmax(0,1.25fr)_minmax(280px,0.75fr)]">
      <section aria-labelledby="code-location-heading">
        <h3 id="code-location-heading" class="mb-2.5 text-sm font-semibold">代码位置</h3>
        <div v-if="codeLocations.length" class="app-panel overflow-hidden">
          <div class="grid grid-cols-[minmax(0,1fr)_minmax(120px,0.4fr)_80px] border-b bg-[hsl(var(--surface-subtle))] px-4 py-2 text-[10px] font-medium text-muted-foreground">
            <span>文件路径</span>
            <span>Notebook 章节</span>
            <span class="text-right">操作</span>
          </div>
          <div
            v-for="location in codeLocations"
            :key="`${location.path}-${location.section}`"
            class="grid grid-cols-[minmax(0,1fr)_minmax(120px,0.4fr)_80px] items-center gap-3 border-b px-4 py-3 last:border-0"
          >
            <div class="min-w-0">
              <code class="block truncate text-[11px] text-foreground" :title="location.path">{{ location.path }}</code>
              <span class="mt-0.5 block truncate text-[10px] text-muted-foreground">{{ location.nodeLabel }} · {{ location.language }}</span>
            </div>
            <span class="truncate text-[11px] text-secondary" :title="location.section">{{ location.section || '—' }}</span>
            <div class="text-right">
              <Button type="button" variant="ghost" size="icon" class="h-7 w-7" :title="copiedPath === location.path ? '已复制' : '复制路径'" @click="copyPath(location.path)">
                <Check v-if="copiedPath === location.path" class="h-3.5 w-3.5 text-[hsl(var(--success))]" aria-hidden="true" />
                <ClipboardCopy v-else class="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
              </Button>
            </div>
          </div>
        </div>
        <div v-else class="app-panel flex min-h-48 items-center justify-center border-dashed p-6 text-center">
          <div>
            <FileCode2 class="mx-auto h-5 w-5 text-muted-foreground" aria-hidden="true" />
            <p class="mt-2 text-xs font-medium">尚未生成代码索引</p>
            <p class="mt-1 text-[10px] text-muted-foreground">求解节点完成后，代码文件和章节位置会自动归档。</p>
          </div>
        </div>
      </section>

      <section aria-labelledby="generated-heading">
        <h3 id="generated-heading" class="mb-2.5 text-sm font-semibold">生成文件</h3>
        <div v-if="generatedArtifacts.length" class="app-panel divide-y overflow-hidden">
          <div v-for="artifact in generatedArtifacts" :key="artifact" class="flex items-center gap-2.5 px-3 py-2.5">
            <FileText class="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
            <code class="min-w-0 flex-1 truncate text-[10px]" :title="artifact">{{ artifact }}</code>
          </div>
        </div>
        <div v-else class="app-panel flex min-h-48 items-center justify-center border-dashed p-6 text-center">
          <div>
            <FolderOpen class="mx-auto h-5 w-5 text-muted-foreground" aria-hidden="true" />
            <p class="mt-2 text-xs font-medium">暂无生成文件</p>
            <p class="mt-1 text-[10px] text-muted-foreground">可在顶部工作区文件按钮查看后端目录的实时内容。</p>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>
