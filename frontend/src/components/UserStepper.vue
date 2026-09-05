<script setup lang="ts">
import { getApiConfigStatus, saveApiConfig } from "@/apis/apiKeyApi";
import {
	type ExecutionBackend,
	submitModelingTask,
} from "@/apis/submitModelingApi";
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
});

/** 当前项目使用的代码计算环境；保持原有 MATLAB 默认行为 */
const executionBackend = ref<ExecutionBackend>("matlab");

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
	const selected = [...files].filter((file) => file.size > 0);
	if (!selected.length) return;
	uploadedFiles.value = selected;
	fileUploaded.value = true;
	flash(`已暂存 ${selected.length} 个数据附件。`, 1000);
}

function handleFileUpload(event: Event): void {
	const files = (event.currentTarget as HTMLInputElement).files;
	if (files) setUploadedFiles(files);
}

function handleFileDrop(event: DragEvent): void {
	const files = event.dataTransfer?.files;
	if (files) setUploadedFiles(files);
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
		const { data } = await getApiConfigStatus();
		if (data.configured) return true;
		toast({
			title: "模型尚未配置",
			description: "请打开右上角设置，至少配置四个工作流角色。",
			variant: "destructive",
		});
		return false;
	}
	const configs = apiKeyStore.getAllAgentConfigs();
	await saveApiConfig({
		coordinator: configs.CoordinatorAgent,
		modeler: configs.ModelerAgent,
		coder: configs.CoderAgent,
		writer: configs.WriterAgent,
		model_scout: configs.ModelScoutAgent,
		model_critic: configs.ModelCriticAgent,
		model_council_enabled: apiKeyStore.modelCouncilEnabled,
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
	const accepted = await fileConfirmDialog.value.openConfirmDialog();
	if (!accepted) {
		toast({
			title: "已取消提交",
			description: "你可以补充数据附件后再开始分析。",
			variant: "destructive",
		});
	}
	return accepted;
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

		const request = {
			ques_all: question.value,
			user_requirements: userRequirements.value,
			comp_template: selectedOptions.value.template,
			format_output: "LaTeX",
			execution_backend: executionBackend.value,
		};
		const attachments = uploadedFiles.value.concat(problemPdf.value);
		const { data } = await submitModelingTask(request, attachments);
		taskId.value = data?.task_id || null;
		if (!taskId.value) throw new Error("后端没有返回任务编号");
		flash(`任务 ${taskId.value} 已进入分析队列。`, 3000);
		await router.push(`/project/${taskId.value}/overview`);
		toast({
			title: "分析已启动",
			description: `任务编号：${taskId.value}`,
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

          <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
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
          <div class="rounded-lg border px-3 py-3">
            <div class="mb-2 flex items-start justify-between gap-3">
              <div>
                <h4 class="text-sm font-medium">计算环境</h4>
                <p class="mt-0.5 text-xs text-muted-foreground">
                  MATLAB 保持默认；选择 Python 后，本项目会直接使用 Python 并在续跑时保持不变。
                </p>
              </div>
              <span class="shrink-0 rounded-full bg-primary/10 px-2 py-0.5 text-xs text-primary">
                项目级
              </span>
            </div>
            <Select v-model="executionBackend">
              <SelectTrigger class="h-9">
                <SelectValue placeholder="选择计算环境" />
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  <SelectLabel>计算环境</SelectLabel>
                  <SelectItem value="matlab">MATLAB（默认）</SelectItem>
                  <SelectItem value="python">Python</SelectItem>
                </SelectGroup>
              </SelectContent>
            </Select>
          </div>
          <div class="rounded-lg border bg-muted/40 px-3 py-2">
            <p class="text-sm font-medium">固定交付：PDF + 可编译 LaTeX</p>
            <p class="mt-0.5 text-xs text-muted-foreground">
              终稿必须通过两次 XeLaTeX 编译和 PDF 渲染检查，不再生成 Markdown 或 DOCX 成稿。
            </p>
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
