<script setup lang="ts">
import { type CsvPreview, getFiles, previewCsv } from "@/apis/filesApi";
import { Button } from "@/components/ui/button";
import CsvPreviewTable from "@/pages/task/components/CsvPreviewTable.vue";
import {
	AlertCircle,
	Database,
	FileSpreadsheet,
	LoaderCircle,
	RefreshCw,
	TableProperties,
} from "lucide-vue-next";
import { computed, onMounted, ref } from "vue";

const props = defineProps<{ taskId: string }>();

interface ProjectFile {
	filename: string;
	file_type: string;
}

const files = ref<ProjectFile[]>([]);
const loading = ref(true);
const loadError = ref("");
const selectedFilename = ref("");
const preview = ref<CsvPreview | null>(null);
const previewLoading = ref(false);
const previewError = ref("");

const datasetExtensions = new Set([
	"blocks",
	"csv",
	"nets",
	"pl",
	"xlsx",
	"xls",
	"json",
	"txt",
	"tsv",
	"mat",
]);
const datasets = computed(() =>
	files.value.filter((file) =>
		datasetExtensions.has(file.file_type.toLowerCase()),
	),
);

const selectedIsCsv = computed(() =>
	selectedFilename.value.toLowerCase().endsWith(".csv"),
);

async function selectFile(file: ProjectFile) {
	selectedFilename.value = file.filename;
	preview.value = null;
	previewError.value = "";
	if (file.file_type.toLowerCase() !== "csv") {
		return;
	}
	previewLoading.value = true;
	try {
		const response = await previewCsv(props.taskId, file.filename);
		preview.value = response.data;
	} catch (error) {
		console.error("加载 CSV 预览失败:", error);
		previewError.value = "预览失败，文件可能过大或格式异常，可下载后查看。";
	} finally {
		previewLoading.value = false;
	}
}

async function loadFiles() {
	loading.value = true;
	loadError.value = "";
	try {
		const response = await getFiles(props.taskId);
		files.value = Array.isArray(response.data) ? response.data : [];
		const firstCsv = datasets.value.find(
			(file) => file.file_type.toLowerCase() === "csv",
		);
		if (firstCsv && !selectedFilename.value) {
			void selectFile(firstCsv);
		}
	} catch (error) {
		console.error("加载数据资产失败:", error);
		loadError.value = "无法读取项目工作区，请检查后端连接后重试。";
	} finally {
		loading.value = false;
	}
}

onMounted(loadFiles);
</script>

<template>
  <div class="flex h-full min-h-0 flex-col">
    <header class="flex shrink-0 flex-wrap items-end justify-between gap-3 border-b bg-card px-5 py-4">
      <div>
        <p class="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">数据处理</p>
        <h2 class="mt-1 text-lg font-semibold tracking-tight">项目数据集</h2>
        <p class="mt-1 text-xs text-muted-foreground">真实文件来自当前任务工作区；未生成字段画像时保持空状态，不伪造统计量。</p>
      </div>
      <Button type="button" variant="outline" size="sm" class="h-8 gap-1.5 text-xs" :disabled="loading" @click="loadFiles">
        <RefreshCw class="h-3.5 w-3.5" :class="loading ? 'animate-spin motion-reduce:animate-none' : ''" aria-hidden="true" />
        刷新
      </Button>
    </header>

    <div class="grid min-h-0 flex-1 lg:grid-cols-[240px_minmax(0,1fr)]">
      <aside class="min-h-0 border-r bg-[hsl(var(--surface-subtle))] p-3" aria-label="数据集列表">
        <div class="flex items-center justify-between px-2 pb-2">
          <h3 class="text-xs font-semibold">数据集</h3>
          <span class="mono-data text-[10px] text-muted-foreground">{{ datasets.length }}</span>
        </div>
        <div v-if="loading" class="flex items-center gap-2 rounded-md px-2 py-3 text-xs text-muted-foreground">
          <LoaderCircle class="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" aria-hidden="true" />
          正在读取工作区
        </div>
        <div v-else-if="datasets.length" class="space-y-1">
          <button
            v-for="file in datasets"
            :key="file.filename"
            type="button"
            class="flex w-full items-center gap-2 rounded-md px-2 py-2 text-left text-xs transition-colors"
            :class="file.filename === selectedFilename ? 'bg-card font-medium shadow-sm ring-1 ring-border' : 'text-secondary hover:bg-card/70'"
            @click="selectFile(file)"
          >
            <FileSpreadsheet class="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
            <span class="min-w-0 flex-1 truncate" :title="file.filename">{{ file.filename }}</span>
            <span class="text-[9px] uppercase text-muted-foreground">{{ file.file_type }}</span>
          </button>
        </div>
        <div v-else class="rounded-md border border-dashed bg-card px-3 py-5 text-center">
          <Database class="mx-auto h-4 w-4 text-muted-foreground" aria-hidden="true" />
          <p class="mt-2 text-[11px] font-medium">未发现数据文件</p>
          <p class="mt-1 text-[10px] leading-4 text-muted-foreground">CSV、XLSX、JSON 等文件出现后会自动列出。</p>
        </div>
      </aside>

      <section class="min-h-0 overflow-y-auto p-5" aria-label="数据检查工作区">
        <div v-if="loadError" class="mb-4 flex items-start gap-2 rounded-md border border-red-200 bg-red-50 px-3 py-2.5 text-xs text-red-700 dark:border-red-900 dark:bg-red-950/30">
          <AlertCircle class="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <span class="flex-1">{{ loadError }}</span>
          <button type="button" class="font-medium underline" @click="loadFiles">重试</button>
        </div>

        <section class="app-panel min-h-72 overflow-hidden" aria-labelledby="preview-heading">
          <div class="flex items-center justify-between border-b px-4 py-3">
            <div>
              <h3 id="preview-heading" class="text-xs font-semibold">数据预览</h3>
              <p class="mt-0.5 text-[10px] text-muted-foreground">
                {{ selectedFilename ? `正在查看 ${selectedFilename}` : "从左侧选择一个数据文件" }}
              </p>
            </div>
            <span class="rounded-md bg-muted px-2 py-1 text-[10px] text-muted-foreground">只读</span>
          </div>

          <div v-if="previewLoading" class="flex min-h-64 items-center justify-center p-6 text-center">
            <div class="flex items-center gap-2 text-xs text-muted-foreground">
              <LoaderCircle class="h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
              正在读取表格内容
            </div>
          </div>
          <div v-else-if="previewError" class="flex min-h-64 items-center justify-center p-6 text-center">
            <div class="max-w-sm">
              <AlertCircle class="mx-auto h-5 w-5 text-[hsl(var(--warning))]" aria-hidden="true" />
              <p class="mt-2 text-[11px] leading-5 text-muted-foreground">{{ previewError }}</p>
            </div>
          </div>
          <div v-else-if="preview" class="p-4">
            <CsvPreviewTable
              :columns="preview.columns"
              :rows="preview.rows"
              :truncated="preview.truncated"
            />
            <p v-if="preview.truncated" class="mt-2 text-[10px] text-muted-foreground">只展示了前 {{ preview.rows.length }} 行，完整数据请在文件面板下载查看。</p>
          </div>
          <div v-else-if="selectedFilename && !selectedIsCsv" class="flex min-h-64 items-center justify-center p-6 text-center">
            <div class="max-w-sm">
              <TableProperties class="mx-auto h-6 w-6 text-muted-foreground" aria-hidden="true" />
              <h4 class="mt-3 text-xs font-semibold">该格式暂不支持在线预览</h4>
              <p class="mt-1 text-[11px] leading-5 text-muted-foreground">目前支持 CSV 表格预览；其他格式请在文件面板下载后查看。</p>
            </div>
          </div>
          <div v-else class="flex min-h-64 items-center justify-center p-6 text-center">
            <div class="max-w-sm">
              <TableProperties class="mx-auto h-6 w-6 text-muted-foreground" aria-hidden="true" />
              <h4 class="mt-3 text-xs font-semibold">暂无可预览的数据</h4>
              <p class="mt-1 text-[11px] leading-5 text-muted-foreground">上传或生成 CSV 文件后，这里会直接显示表格内容。</p>
            </div>
          </div>
        </section>

        <section class="mt-5" aria-labelledby="cleaning-heading">
          <div class="mb-2.5 flex items-center justify-between">
            <h3 id="cleaning-heading" class="text-sm font-semibold">清洗与质量检查</h3>
            <span class="text-[10px] text-muted-foreground">可追踪步骤</span>
          </div>
          <div class="app-panel flex min-h-28 items-center justify-center border-dashed p-5 text-center">
            <p class="text-[11px] text-muted-foreground">尚无结构化清洗步骤。Agent 生成数据处理记录后，将在此显示参数、前后对比和重新运行入口。</p>
          </div>
        </section>
      </section>
    </div>
  </div>
</template>
