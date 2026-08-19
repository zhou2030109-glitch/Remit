<script setup lang="ts">
import { getApiConfigStatus, saveApiConfig } from "@/apis/apiKeyApi";
import { submitModelingTask } from "@/apis/submitModelingApi";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
	Select,
	SelectContent,
	SelectGroup,
	SelectItem,
	SelectLabel,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/ui/toast";
import { useApiKeyStore } from "@/stores/apiKeys";
import { FileUp, LoaderCircle, Rocket } from "lucide-vue-next";
import { computed, ref } from "vue";
import { useRouter } from "vue-router";
import FileConfirmDialog from "./FileConfirmDialog.vue";
import ProblemPdfDropzone from "./ProblemPdfDropzone.vue";

interface FileConfirmDialogHandle {
	openConfirmDialog: () => Promise<boolean>;
}

const ACCEPTED_DATA_EXTENSIONS = ".txt,.csv,.xlsx,.xls,.blocks,.nets,.pl";

const { toast } = useToast();
const apiKeyStore = useApiKeyStore();
const router = useRouter();

/** 当前步骤（1: 数据文件，2: 赛题与选项） */
const currentStep = ref(1);

const fileConfirmDialog = ref<FileConfirmDialogHandle | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);

/** 已上传的数据文件列表 */
const uploadedFiles = ref<File[]>([]);
const fileUploaded = ref(true);

/** 题目内容 */
const question = ref("");
/** 解析成功的赛题 PDF */
const problemPdf = ref<File | null>(null);
/** 不得被题目拆解阶段丢弃的额外交付要求 */
const userRequirements = ref("");

/** 是否正在提交任务 */
const isSubmitting = ref(false);
/** 任务ID */
const taskId = ref<string | null>(null);

/** 输出选项 */
const selectedOptions = ref({
	template: "国赛",
	language: "中文",
	format: "Markdown",
});

const OPTION_GROUPS: {
	field: keyof typeof selectedOptions.value;
	title: string;
	placeholder: string;
	choices: string[];
}[] = [
	{
		field: "template",
		title: "模板",
		placeholder: "选择模板",
		choices: ["国赛", "美赛"],
	},
	{
		field: "language",
		title: "语言",
		placeholder: "选择语言",
		choices: ["中文", "英文"],
	},
	{
		field: "format",
		title: "格式",
		placeholder: "选择格式",
		choices: ["Markdown", "LaTeX"],
	},
];

/** 浮动成功提示（上传 / 提交共用） */
const flashMessage = ref<string | null>(null);
let flashTimer: ReturnType<typeof setTimeout> | undefined;

function flash(message: string, durationMs = 1500): void {
	flashMessage.value = message;
	if (flashTimer !== undefined) {
		clearTimeout(flashTimer);
	}
	flashTimer = setTimeout(() => {
		flashMessage.value = null;
	}, durationMs);
}

const canSubmit = computed(
	() =>
		Boolean(problemPdf.value && question.value.trim()) && !isSubmitting.value,
);

function goToStep(step: number): void {
	currentStep.value = Math.min(2, Math.max(1, step));
}

/** 更新待提交的数据文件，并显示上传成功提示。 */
function setUploadedFiles(files: FileList | File[]): void {
	const selected = Array.from(files);
	if (selected.length === 0) {
		return;
	}
	uploadedFiles.value = selected;
	fileUploaded.value = true;
	flash(`已成功上传 ${selected.length} 个文件，请继续下一步操作。`, 1000);
}

function handleFileUpload(event: Event): void {
	const input = event.target as HTMLInputElement;
	if (input.files) {
		setUploadedFiles(input.files);
	}
}

function handleFileDrop(event: DragEvent): void {
	if (event.dataTransfer?.files) {
		setUploadedFiles(event.dataTransfer.files);
	}
}

function handleProblemPdfParsed(payload: { file: File; text: string }): void {
	problemPdf.value = payload.file;
	question.value = payload.text;
}

function handleProblemPdfCleared(): void {
	problemPdf.value = null;
	question.value = "";
}

/** 确保后端有可用的模型配置；浏览器侧有配置时覆盖后端环境变量 */
async function ensureModelCredentials(): Promise<boolean> {
	if (apiKeyStore.isEmpty) {
		const configStatus = await getApiConfigStatus();
		if (!configStatus.data.configured) {
			toast({
				title: "请先配置 API Key",
				description: "可在后端 .env.dev 或侧边栏 API Key 中配置",
				variant: "destructive",
			});
			return false;
		}
		return true;
	}
	await saveApiConfig({
		coordinator: apiKeyStore.coordinatorConfig,
		modeler: apiKeyStore.modelerConfig,
		coder: apiKeyStore.coderConfig,
		writer: apiKeyStore.writerConfig,
		openalex_email: apiKeyStore.openalexEmail,
	});
	return true;
}

/** 未上传数据附件时先征得用户确认 */
async function confirmWithoutDataFiles(): Promise<boolean> {
	if (uploadedFiles.value.length > 0) {
		return true;
	}
	if (!fileConfirmDialog.value) {
		return false;
	}
	const proceed = await fileConfirmDialog.value.openConfirmDialog();
	if (!proceed) {
		toast({
			title: "请先上传文件",
			description: "请先上传文件",
			variant: "destructive",
		});
	}
	return proceed;
}

/** 提交建模任务 */
async function handleSubmit(): Promise<void> {
	if (!problemPdf.value || !question.value.trim()) {
		toast({
			title: "请先上传赛题 PDF",
			description: "等待 PDF 解析完成后即可开始分析",
			variant: "destructive",
		});
		return;
	}

	isSubmitting.value = true;
	try {
		if (!(await ensureModelCredentials())) {
			return;
		}
		if (!(await confirmWithoutDataFiles())) {
			return;
		}

		const taskFiles = [...uploadedFiles.value, problemPdf.value];
		const response = await submitModelingTask(
			{
				ques_all: question.value,
				user_requirements: userRequirements.value,
				comp_template: selectedOptions.value.template,
				format_output: selectedOptions.value.format,
			},
			taskFiles,
		);

		taskId.value = response?.data?.task_id ?? null;
		flash(`任务提交成功，编号为：${taskId.value}。`, 3000);
		router.push(`/project/${taskId.value}/overview`);
		toast({
			title: "任务提交成功",
			description: `任务提交成功，编号为：${taskId.value}`,
		});
	} catch (error) {
		console.error("任务提交失败:", error);
		toast({
			title: "任务提交失败",
			description: "请检查 API Key 是否正确",
			variant: "destructive",
		});
	} finally {
		isSubmitting.value = false;
	}
}
</script>

<template>
  <div class="relative mx-auto w-full min-w-0 max-w-xl">
    <Transition name="fade">
      <div v-if="flashMessage" class="fixed top-4 right-4 z-50">
        <Alert>
          <Rocket class="h-4 w-4" />
          <AlertTitle>操作成功</AlertTitle>
          <AlertDescription>{{ flashMessage }}</AlertDescription>
        </Alert>
      </div>
    </Transition>

    <div class="border rounded-lg shadow-sm">
      <!-- 第一步：上传数据文件 -->
      <div v-if="currentStep === 1" class="p-4 sm:p-6">
        <div
          class="border-2 border-dashed rounded-lg p-8 text-center hover:border-primary/50 transition-colors cursor-pointer"
          @click="() => fileInput?.click()"
          @dragover.prevent
          @drop.prevent="handleFileDrop">
          <input type="file" ref="fileInput" class="hidden" multiple
            :accept="ACCEPTED_DATA_EXTENSIONS" @change="handleFileUpload">
          <div class="mx-auto w-12 h-12 rounded-full bg-primary/10 flex items-center justify-center">
            <FileUp class="w-6 h-6 text-primary" />
          </div>
          <div>
            <p class="text-lg font-medium">拖拽数据集到此处或点击上传</p>
            <p class="text-sm text-muted-foreground mt-1">
              支持 .txt, .csv, .xlsx, .xls, .blocks, .nets, .pl 等格式文件（可多选）
            </p>
            <div v-if="uploadedFiles.length > 0" class="text-sm text-green-600 mt-1">
              已上传文件:
              <ul>
                <li v-for="file in uploadedFiles" :key="file.name">{{ file.name }}</li>
              </ul>
            </div>
          </div>
        </div>
        <div class="mt-4 flex justify-end">
          <Button size="sm" :disabled="!fileUploaded" @click="goToStep(2)">
            下一步
          </Button>
        </div>
      </div>

      <!-- 第二步：赛题 PDF 与输出选项 -->
      <div v-if="currentStep === 2" class="p-4 sm:p-6">
        <div class="space-y-4">
          <div class="space-y-2">
            <div>
              <h4 class="text-sm font-medium">上传赛题 PDF</h4>
              <p class="mt-1 text-xs text-muted-foreground">拖入后自动解析完整题目背景、条件和所有小问。</p>
            </div>
            <ProblemPdfDropzone @parsed="handleProblemPdfParsed" @cleared="handleProblemPdfCleared" />
          </div>

          <div class="space-y-1">
            <h4 class="text-sm font-medium mb-2">额外交付要求</h4>
            <Textarea v-model="userRequirements"
              placeholder="例如：问题1必须输出逐样本预测值、预测区间和可下载表格；预测模型必须按主体分组验证并优于基线"
              class="min-h-[88px]" />
            <p class="text-xs text-muted-foreground">
              此处内容会独立贯穿协调、建模、求解和质量门禁，不会被并入题目背景后丢失。
            </p>
          </div>

          <div class="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <div v-for="group in OPTION_GROUPS" :key="group.field" class="min-w-0">
              <Select v-model="selectedOptions[group.field]">
                <SelectTrigger class="h-9">
                  <SelectValue :placeholder="group.placeholder" />
                </SelectTrigger>
                <SelectContent>
                  <SelectGroup>
                    <SelectLabel>{{ group.title }}</SelectLabel>
                    <SelectItem v-for="choice in group.choices" :key="choice" :value="choice">
                      {{ choice }}
                    </SelectItem>
                  </SelectGroup>
                </SelectContent>
              </Select>
            </div>
          </div>
        </div>
        <div class="mt-4 flex justify-between">
          <Button variant="outline" size="sm" @click="goToStep(1)">
            上一步
          </Button>
          <Button size="sm" :disabled="!canSubmit" @click="handleSubmit">
            <LoaderCircle v-if="isSubmitting" class="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" />
            {{ isSubmitting ? "正在提交…" : "开始分析" }}
          </Button>
        </div>
      </div>
    </div>
  </div>
  <FileConfirmDialog ref="fileConfirmDialog" />
</template>
