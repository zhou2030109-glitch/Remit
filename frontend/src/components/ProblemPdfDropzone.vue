<script setup lang="ts">
import { parseProblemPdf } from "@/apis/submitModelingApi";
import { Button } from "@/components/ui/button";
import { isAxiosError } from "axios";
import {
	CircleAlert,
	CircleCheck,
	FileText,
	FileUp,
	LoaderCircle,
	X,
} from "lucide-vue-next";
import { computed, ref } from "vue";

interface ParsedProblemPdf {
	file: File;
	text: string;
	pageCount: number;
	charCount: number;
	figureCount: number;
}

const emit = defineEmits<{
	parsed: [payload: ParsedProblemPdf];
	cleared: [];
}>();

const MAX_PDF_BYTES = 25 * 1024 * 1024;

const fileInput = ref<HTMLInputElement | null>(null);
const selectedFile = ref<File | null>(null);
const parseStatus = ref<"idle" | "parsing" | "success" | "error">("idle");
const errorMessage = ref("");
const pageCount = ref(0);
const charCount = ref(0);
const figureCount = ref(0);
const visionFailed = ref(false);
const textPreview = ref("");
const dragDepth = ref(0);
let requestId = 0;

const isDragging = computed(() => dragDepth.value > 0);
const isParsing = computed(() => parseStatus.value === "parsing");
const liveMessage = computed(() => {
	if (parseStatus.value === "parsing") return "正在解析 PDF 并识别插图";
	if (parseStatus.value === "success") {
		const figures = figureCount.value
			? `，识别 ${figureCount.value} 张插图`
			: "";
		return `PDF 解析完成，共 ${pageCount.value} 页，${charCount.value} 个字符${figures}`;
	}
	if (parseStatus.value === "error") return errorMessage.value;
	return "等待上传赛题 PDF";
});

const formatFileSize = (bytes: number) => {
	if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
	return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
};

const openFilePicker = () => {
	fileInput.value?.click();
};

const resetResult = () => {
	pageCount.value = 0;
	charCount.value = 0;
	figureCount.value = 0;
	visionFailed.value = false;
	textPreview.value = "";
	errorMessage.value = "";
};

const setError = (message: string, file: File | null = null) => {
	selectedFile.value = file;
	parseStatus.value = "error";
	errorMessage.value = message;
	emit("cleared");
};

const parseFile = async (file: File) => {
	requestId += 1;
	const currentRequest = requestId;
	resetResult();
	emit("cleared");

	if (!file.name.toLowerCase().endsWith(".pdf")) {
		setError("这里只接受 PDF 格式的赛题文件");
		return;
	}
	if (file.size > MAX_PDF_BYTES) {
		setError("PDF 不能超过 25MB", file);
		return;
	}

	selectedFile.value = file;
	parseStatus.value = "parsing";

	try {
		const response = await parseProblemPdf(file);
		if (currentRequest !== requestId) return;

		pageCount.value = response.data.page_count;
		charCount.value = response.data.char_count;
		figureCount.value = response.data.figure_count;
		visionFailed.value = response.data.vision_status === "failed";
		textPreview.value = response.data.text.replace(/\s+/g, " ").slice(0, 180);
		parseStatus.value = "success";
		emit("parsed", {
			file,
			text: response.data.text,
			pageCount: response.data.page_count,
			charCount: response.data.char_count,
			figureCount: response.data.figure_count,
		});
	} catch (error) {
		if (currentRequest !== requestId) return;
		const responseData = isAxiosError(error)
			? (error.response?.data as { detail?: string } | undefined)
			: undefined;
		setError(responseData?.detail || "PDF 解析失败，请检查文件后重试", file);
	}
};

const handleFileInput = (event: Event) => {
	const input = event.target as HTMLInputElement;
	const file = input.files?.[0];
	if (file) void parseFile(file);
	input.value = "";
};

const handleDragEnter = () => {
	dragDepth.value += 1;
};

const handleDragLeave = () => {
	dragDepth.value = Math.max(0, dragDepth.value - 1);
};

const handleDrop = (event: DragEvent) => {
	dragDepth.value = 0;
	const file = event.dataTransfer?.files?.[0];
	if (file) void parseFile(file);
};

const clearFile = () => {
	requestId += 1;
	selectedFile.value = null;
	parseStatus.value = "idle";
	dragDepth.value = 0;
	resetResult();
	emit("cleared");
};
</script>

<template>
  <div
    class="rounded-lg border-2 border-dashed transition-[border-color,background-color,box-shadow] duration-200"
    :class="[
      isDragging
        ? 'border-primary bg-primary/5 shadow-[0_0_0_4px_hsl(var(--primary)/0.08)]'
        : parseStatus === 'error'
          ? 'border-destructive/60 bg-destructive/[0.03]'
          : parseStatus === 'success'
            ? 'border-emerald-500/55 bg-emerald-500/[0.03]'
            : 'border-border hover:border-primary/50 hover:bg-muted/30',
    ]"
    @dragenter.prevent="handleDragEnter"
    @dragover.prevent
    @dragleave.prevent="handleDragLeave"
    @drop.prevent="handleDrop"
  >
    <input
      ref="fileInput"
      type="file"
      class="sr-only"
      accept="application/pdf,.pdf"
      @change="handleFileInput"
    >

    <button
      type="button"
      class="flex min-h-[176px] w-full min-w-0 flex-col items-center justify-center rounded-lg px-4 py-7 text-center outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 sm:px-6"
      :aria-busy="isParsing"
      aria-label="选择或拖入赛题 PDF"
      @click="openFilePicker"
    >
      <span
        class="mb-3 flex h-12 w-12 items-center justify-center rounded-full"
        :class="[
          parseStatus === 'error'
            ? 'bg-destructive/10 text-destructive'
            : parseStatus === 'success'
              ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
              : 'bg-primary/10 text-primary',
        ]"
      >
        <LoaderCircle v-if="isParsing" class="h-6 w-6 animate-spin motion-reduce:animate-none" />
        <CircleAlert v-else-if="parseStatus === 'error'" class="h-6 w-6" />
        <CircleCheck v-else-if="parseStatus === 'success'" class="h-6 w-6" />
        <FileUp v-else class="h-6 w-6" />
      </span>

      <template v-if="isParsing">
        <span class="text-base font-medium">正在读取题目内容…</span>
        <span class="mt-1 text-sm text-muted-foreground">按页提取背景、条件与小问，并识别图中的信息</span>
      </template>
      <template v-else-if="parseStatus === 'success'">
        <span class="text-base font-medium">题目解析完成</span>
        <span class="mt-1 text-sm text-muted-foreground">
          <template v-if="figureCount">已把 {{ figureCount }} 张插图的内容转成文字并并入题面</template>
          <template v-else-if="visionFailed">插图识别未成功，已按纯文本导入</template>
          <template v-else>点击可替换，或直接开始分析</template>
        </span>
      </template>
      <template v-else-if="parseStatus === 'error'">
        <span class="text-base font-medium text-destructive">无法解析这个 PDF</span>
        <span class="mt-1 max-w-md text-sm text-muted-foreground">{{ errorMessage }}</span>
      </template>
      <template v-else>
        <span class="text-base font-medium">将赛题 PDF 拖到这里</span>
        <span class="mt-1 max-w-full text-sm text-muted-foreground">或点击选择文件 · 单个 PDF，最大 25MB</span>
      </template>
    </button>

    <div v-if="selectedFile" class="mx-4 mb-4 rounded-md border bg-background/90 px-3 py-3">
      <div class="flex items-center gap-3">
        <span class="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
          <FileText class="h-5 w-5" />
        </span>
        <div class="min-w-0 flex-1 text-left">
          <p class="truncate text-sm font-medium">{{ selectedFile.name }}</p>
          <p class="mt-0.5 text-xs text-muted-foreground">
            {{ formatFileSize(selectedFile.size) }}
            <template v-if="parseStatus === 'success'"> · {{ pageCount }} 页 · {{ charCount }} 字符</template>
            <template v-if="parseStatus === 'success' && figureCount"> · {{ figureCount }} 张插图已识别</template>
          </p>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          class="h-9 w-9 shrink-0"
          aria-label="移除赛题 PDF"
          @click.stop="clearFile"
        >
          <X class="h-4 w-4" />
        </Button>
      </div>
      <p
        v-if="parseStatus === 'success' && textPreview"
        class="mt-3 max-h-10 overflow-hidden border-t pt-2 text-left text-xs leading-5 text-muted-foreground"
      >
        {{ textPreview }}{{ charCount > 180 ? '…' : '' }}
      </p>
    </div>

    <p class="sr-only" aria-live="polite">{{ liveMessage }}</p>
  </div>
</template>
