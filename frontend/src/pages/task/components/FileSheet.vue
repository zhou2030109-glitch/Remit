<script setup lang="ts">
import {
	type WorkspaceFile,
	getAllFilesDownloadUrl,
	getFileDownloadUrl,
	getFiles,
} from "@/apis/filesApi";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
	Sheet,
	SheetContent,
	SheetDescription,
	SheetHeader,
	SheetTitle,
	SheetTrigger,
} from "@/components/ui/sheet";
import { useToast } from "@/components/ui/toast/use-toast";
import {
	Tooltip,
	TooltipContent,
	TooltipProvider,
	TooltipTrigger,
} from "@/components/ui/tooltip";
import { Download, File, FileText, Files, RefreshCw } from "lucide-vue-next";
import { ref } from "vue";
import { useRoute } from "vue-router";

const props = defineProps<{
	taskId?: string;
}>();

const route = useRoute();
const taskId = String(
	props.taskId ?? route.params.task_id ?? route.params.projectId ?? "",
);
const { toast } = useToast();

/** 文件列表弹窗显示状态 */
const fileListVisible = ref(false);
/** 文件列表数据 */
const fileList = ref<WorkspaceFile[]>([]);
/** 加载状态 */
const loadingFiles = ref(false);
/** 当前正在下载的文件名 */
const downloadingFile = ref<string | null>(null);
/** 是否正在下载全部文件 */
const downloadingAll = ref(false);

const TEXT_EXTENSIONS = new Set([
	"txt",
	"md",
	"json",
	"csv",
	"xml",
	"yml",
	"yaml",
]);

function displayName(file: WorkspaceFile): string {
	return file.name ?? file.filename ?? "Unknown";
}

function iconFor(fileName: string) {
	const ext = fileName.split(".").pop()?.toLowerCase() ?? "";
	return TEXT_EXTENSIONS.has(ext) ? FileText : File;
}

/** 格式化文件大小显示 */
function formatFileSize(size: number | undefined): string {
	if (!size) {
		return "";
	}
	const units = ["B", "KB", "MB", "GB"];
	let value = size;
	let unit = 0;
	while (value >= 1024 && unit < units.length - 1) {
		value /= 1024;
		unit += 1;
	}
	return `${value.toFixed(1)} ${units[unit]}`;
}

/** 通过隐藏锚点触发浏览器下载 */
function triggerBrowserDownload(url: string, filename: string): void {
	const anchor = document.createElement("a");
	anchor.href = url;
	anchor.download = filename;
	anchor.target = "_blank";
	document.body.appendChild(anchor);
	anchor.click();
	anchor.remove();
}

function reportError(title: string, error: unknown): void {
	console.error(`${title}:`, error);
	toast({ title, description: "操作失败，请稍后重试", variant: "destructive" });
}

/** 拉取文件列表并展开抽屉 */
async function openFolder(): Promise<void> {
	loadingFiles.value = true;
	try {
		const res = await getFiles(taskId);
		if (!res.data) {
			toast({
				title: "获取文件列表失败",
				description: "无法获取工作区文件列表",
				variant: "destructive",
			});
			return;
		}
		fileList.value = Array.isArray(res.data) ? res.data : [res.data];
		fileListVisible.value = true;
	} catch (error) {
		reportError("获取文件列表失败", error);
	} finally {
		loadingFiles.value = false;
	}
}

/** 下载单个文件 */
async function downloadSingleFile(filename: string): Promise<void> {
	downloadingFile.value = filename;
	try {
		const res = await getFileDownloadUrl(taskId, filename);
		if (!res.data?.download_url) {
			throw new Error("missing download_url");
		}
		triggerBrowserDownload(res.data.download_url, filename);
		toast({ title: "下载成功", description: `文件 ${filename} 开始下载` });
	} catch (error) {
		reportError(`下载文件 ${filename} 失败`, error);
	} finally {
		downloadingFile.value = null;
	}
}

/** 下载所有文件（压缩包） */
async function downloadAll(): Promise<void> {
	downloadingAll.value = true;
	try {
		const res = await getAllFilesDownloadUrl(taskId);
		if (!res.data?.download_url) {
			throw new Error("missing download_url");
		}
		triggerBrowserDownload(res.data.download_url, `task_${taskId}_files.zip`);
		toast({ title: "下载成功", description: "所有文件压缩包开始下载" });
	} catch (error) {
		reportError("下载所有文件失败", error);
	} finally {
		downloadingAll.value = false;
	}
}
</script>

<template>
  <Sheet v-model:open="fileListVisible">
    <SheetTrigger asChild>
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger as-child>
            <Button aria-label="工作区文件" title="工作区文件" size="icon" class="flex gap-2"
              :disabled="loadingFiles" @click="openFolder">
              <RefreshCw v-if="loadingFiles" class="w-4 h-4 animate-spin" />
              <Files v-else class="w-4 h-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>
            <p>工作区文件</p>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    </SheetTrigger>

    <SheetContent side="right" class="w-[400px] sm:w-[540px]">
      <SheetHeader>
        <SheetTitle class="flex items-center justify-between mr-5">
          工作区文件
          <Button size="sm" variant="outline" :disabled="downloadingAll || fileList.length === 0"
            @click="downloadAll">
            <RefreshCw v-if="downloadingAll" class="mr-2 h-4 w-4 animate-spin" />
            <Download v-else class="mr-2 h-4 w-4" />
            下载全部
          </Button>
        </SheetTitle>
        <SheetDescription>
          运行产物保存在<span class="font-mono">backend/project/work_dir/{{ taskId }}/*</span> 目录下
        </SheetDescription>
      </SheetHeader>

      <div class="mt-6">
        <ScrollArea class="h-[calc(100vh-120px)]">
          <div v-if="fileList.length === 0" class="text-center py-8 text-gray-500">
            暂无文件
          </div>
          <div v-else class="space-y-2">
            <div v-for="file in fileList" :key="displayName(file)"
              class="flex items-center gap-3 p-3 rounded-lg border hover:bg-gray-50 transition-colors">
              <component :is="iconFor(displayName(file))" class="w-5 h-5 text-gray-600 flex-shrink-0" />
              <div class="flex-1 min-w-0">
                <div class="font-medium text-sm truncate">{{ displayName(file) }}</div>
                <div class="text-xs text-gray-500 flex gap-2">
                  <span v-if="file.size">{{ formatFileSize(file.size) }}</span>
                  <span v-if="file.modified_time">{{ new Date(file.modified_time).toLocaleDateString() }}</span>
                  <span v-if="file.type">{{ file.type }}</span>
                </div>
              </div>
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger as-child>
                    <Button size="sm" variant="ghost" class="flex-shrink-0"
                      :disabled="downloadingFile === displayName(file)"
                      @click="downloadSingleFile(displayName(file))">
                      <RefreshCw v-if="downloadingFile === displayName(file)" class="w-4 h-4 animate-spin" />
                      <Download v-else class="w-4 h-4" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>
                    <p>下载文件</p>
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
            </div>
          </div>
        </ScrollArea>
      </div>
    </SheetContent>
  </Sheet>
</template>
