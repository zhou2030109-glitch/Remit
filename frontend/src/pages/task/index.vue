<script setup lang="ts">
import {
	type ResumeOptions,
	getResumeOptions,
	getWriterSeque,
} from "@/apis/commonApi";
import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogClose,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/ui/toast";
import ProjectWorkspaceShell from "@/pages/task/components/ProjectWorkspaceShell.vue";
import { useTaskStore } from "@/stores/task";
import { LoaderCircle } from "lucide-vue-next";
import { onBeforeUnmount, onMounted, ref } from "vue";

const props = defineProps<{ task_id: string }>();

const taskStore = useTaskStore();
const { toast } = useToast();
const writerSequence = ref<string[]>([]);
const startTime = ref(Date.now());
const runningDuration = ref("0s");
let timer: ReturnType<typeof setInterval> | null = null;

const isStopping = ref(false);
const resumeOptions = ref<ResumeOptions | null>(null);
const resumeDialogOpen = ref(false);
const selectedResumeNode = ref("");
const isResuming = ref(false);

const revisionDialogOpen = ref(false);
const revisionFeedback = ref("");
const selectedRevisionNode = ref("");
const isDecidingApproval = ref(false);

function formatDuration(ms: number) {
	const seconds = Math.floor(ms / 1000);
	const hours = Math.floor(seconds / 3600);
	const minutes = Math.floor((seconds % 3600) / 60);
	const remainingSeconds = seconds % 60;
	if (hours > 0) return `${hours}h ${minutes}m ${remainingSeconds}s`;
	if (minutes > 0) return `${minutes}m ${remainingSeconds}s`;
	return `${remainingSeconds}s`;
}

function updateDuration() {
	runningDuration.value = formatDuration(Date.now() - startTime.value);
}

function openRevisionDialog() {
	selectedRevisionNode.value = taskStore.pendingApproval?.node_id ?? "";
	// 候选否决等操作会预填草稿；仅在输入框为空时带入，避免覆盖用户已写内容
	if (taskStore.reviseDraft && !revisionFeedback.value.trim()) {
		revisionFeedback.value = taskStore.reviseDraft;
	}
	taskStore.reviseDraft = "";
	revisionDialogOpen.value = true;
}

async function handleApprove() {
	if (!taskStore.pendingApproval || isDecidingApproval.value) return;
	isDecidingApproval.value = true;
	try {
		const result = await taskStore.decideApproval(props.task_id, "approve");
		startTime.value = Date.now();
		toast({ title: "本步已批准", description: result.message });
	} catch (error) {
		console.error("提交人工批准失败:", error);
		toast({
			variant: "destructive",
			title: "批准失败",
			description: "审核状态可能已变化，已为你重新加载。",
		});
		await taskStore.loadPendingApproval(props.task_id);
	} finally {
		isDecidingApproval.value = false;
	}
}

async function handleRevision() {
	const feedback = revisionFeedback.value.trim();
	if (!feedback || isDecidingApproval.value) return;
	isDecidingApproval.value = true;
	try {
		const result = await taskStore.decideApproval(
			props.task_id,
			"revise",
			feedback,
			selectedRevisionNode.value,
		);
		revisionDialogOpen.value = false;
		revisionFeedback.value = "";
		selectedRevisionNode.value = "";
		startTime.value = Date.now();
		toast({ title: "已退回重做", description: result.message });
	} catch (error) {
		console.error("提交返修意见失败:", error);
		toast({
			variant: "destructive",
			title: "退回失败",
			description: "审核状态可能已变化，修改意见仍保留，请刷新后重试。",
		});
		await taskStore.loadPendingApproval(props.task_id);
	} finally {
		isDecidingApproval.value = false;
	}
}

async function handleStop() {
	isStopping.value = true;
	try {
		const result = await taskStore.stopTask(props.task_id);
		if (result.success) await loadResumeOptions(true);
	} finally {
		isStopping.value = false;
	}
}

async function loadResumeOptions(waitForCleanup = false) {
	const attempts = waitForCleanup ? 12 : 1;
	for (let attempt = 0; attempt < attempts; attempt++) {
		try {
			const response = await getResumeOptions(props.task_id);
			resumeOptions.value = response.data;
			const interrupted = response.data.nodes.find(
				(node) => node.status === "interrupted",
			);
			const latestAvailable =
				response.data.nodes[response.data.nodes.length - 1];
			selectedResumeNode.value =
				interrupted?.node_id ?? latestAvailable?.node_id ?? "";
			if (response.data.resumable || !waitForCleanup) return;
		} catch (error) {
			console.error("读取续跑节点失败:", error);
			resumeOptions.value = null;
			if (!waitForCleanup) return;
		}
		await new Promise((resolve) => window.setTimeout(resolve, 250));
	}
}

async function handleResume() {
	if (!selectedResumeNode.value || isResuming.value) return;
	isResuming.value = true;
	try {
		const result = await taskStore.resumeTask(
			props.task_id,
			selectedResumeNode.value,
		);
		resumeDialogOpen.value = false;
		resumeOptions.value = null;
		startTime.value = Date.now();
		toast({ title: "任务已续跑", description: result.message });
	} catch (error) {
		console.error("从节点续跑失败:", error);
		toast({
			variant: "destructive",
			title: "无法续跑任务",
			description: "节点状态可能已变化，请刷新后重试。",
		});
		await loadResumeOptions();
	} finally {
		isResuming.value = false;
	}
}

onMounted(async () => {
	// 提前申请桌面通知权限：审批等待/失败挂起时主动提醒用户
	try {
		if (
			typeof Notification !== "undefined" &&
			Notification.permission === "default"
		) {
			void Notification.requestPermission();
		}
	} catch {
		// 通知权限不可用不影响功能
	}
	await Promise.all([
		taskStore.loadTaskMessages(props.task_id),
		taskStore.loadTaskHistory(),
	]);
	taskStore.connectWebSocket(props.task_id);
	if (!taskStore.isRunning) await loadResumeOptions();

	try {
		const response = await getWriterSeque();
		const payload = response.data as unknown;
		writerSequence.value = Array.isArray(payload)
			? payload.filter((item): item is string => typeof item === "string")
			: payload && typeof payload === "object" && "writer_seque" in payload
				? (Reflect.get(payload, "writer_seque") as string[])
				: [];
	} catch (error) {
		console.error("加载论文目录失败:", error);
		writerSequence.value = [];
	}

	timer = setInterval(updateDuration, 1000);
	updateDuration();
});

onBeforeUnmount(() => {
	taskStore.closeWebSocket();
	if (timer) clearInterval(timer);
});
</script>

<template>
  <ProjectWorkspaceShell
    :task-id="props.task_id"
    :writer-sequence="writerSequence"
    :running-duration="runningDuration"
    :resume-available="Boolean(resumeOptions?.resumable)"
    :is-stopping="isStopping"
    :is-deciding-approval="isDecidingApproval"
    @stop="handleStop"
    @resume="resumeDialogOpen = true"
    @approve="handleApprove"
    @revise="openRevisionDialog"
  />

  <Dialog v-model:open="resumeDialogOpen">
    <DialogContent class="w-[calc(100vw-2rem)] max-w-lg overflow-hidden">
      <DialogHeader>
        <DialogTitle>选择续跑节点</DialogTitle>
        <DialogDescription class="leading-6">
          系统会保留该节点之前已完成的成果，从所选节点重新执行，并覆盖它之后的旧结果。
        </DialogDescription>
      </DialogHeader>
      <div class="min-w-0 space-y-2 py-2" data-testid="resume-task-dialog">
        <label for="resume-node" class="text-sm font-medium">恢复位置</label>
        <Select v-model="selectedResumeNode">
          <SelectTrigger id="resume-node" class="min-w-0 max-w-full [&>span]:min-w-0 [&>span]:flex-1">
            <SelectValue placeholder="选择一个可恢复节点" />
          </SelectTrigger>
          <SelectContent class="max-w-[calc(100vw-2rem)]">
            <SelectItem
              v-for="node in resumeOptions?.nodes ?? []"
              :key="node.node_id"
              :value="node.node_id"
              class="max-w-[calc(100vw-3rem)] [&>span:last-child]:block [&>span:last-child]:truncate"
            >
              {{ node.label }}{{ node.status === 'interrupted' ? '（上次中断处）' : node.status === 'completed' ? '（重新执行）' : '' }}
            </SelectItem>
          </SelectContent>
        </Select>
        <p class="text-xs leading-5 text-muted-foreground">
          为避免旧文件让质量门禁误判，所选求解节点及其下游产物会先失效，再重新生成。
        </p>
      </div>
      <DialogFooter class="gap-2 sm:gap-0">
        <DialogClose as-child>
          <Button type="button" variant="outline" :disabled="isResuming">取消</Button>
        </DialogClose>
        <Button type="button" :disabled="!selectedResumeNode || isResuming" data-testid="confirm-resume-task-button" @click="handleResume">
          {{ isResuming ? '正在启动…' : '从此节点继续' }}
        </Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>

  <Dialog v-model:open="revisionDialogOpen">
    <DialogContent class="w-[calc(100vw-2rem)] max-w-xl overflow-hidden">
      <DialogHeader>
        <DialogTitle>退回本步并要求修改</DialogTitle>
        <DialogDescription class="leading-6">
          意见会写入工作流检查点，Agent 必须重做“{{ taskStore.pendingApproval?.node_label }}”。该节点及其下游旧成果会失效。
        </DialogDescription>
      </DialogHeader>
      <div class="min-w-0 space-y-2 py-2" data-testid="revision-feedback-dialog">
        <label for="revision-target" class="text-sm font-medium">从哪个节点开始返修</label>
        <Select v-model="selectedRevisionNode">
          <SelectTrigger id="revision-target" class="min-w-0 max-w-full [&>span]:min-w-0 [&>span]:flex-1">
            <SelectValue placeholder="选择需要重做的节点" />
          </SelectTrigger>
          <SelectContent class="max-w-[calc(100vw-2rem)]">
            <SelectItem
              v-for="target in taskStore.pendingApproval?.revision_targets ?? []"
              :key="target.node_id"
              :value="target.node_id"
              class="max-w-[calc(100vw-3rem)] [&>span:last-child]:block [&>span:last-child]:truncate"
            >
              {{ target.label }}{{ target.node_id === taskStore.pendingApproval?.node_id ? '（当前步骤）' : '（回退）' }}
            </SelectItem>
          </SelectContent>
        </Select>
        <label for="revision-feedback" class="text-sm font-medium">具体修改意见</label>
        <Textarea
          id="revision-feedback"
          v-model="revisionFeedback"
          rows="6"
          class="resize-none"
          placeholder="例如：问题一的数据划分存在泄漏，请按主体分组交叉验证；若验证集 R² 仍低于 0.6，必须更换模型并说明理由。"
          :disabled="isDecidingApproval"
        />
        <p class="text-xs leading-5 text-muted-foreground">
          请写清“哪里不合格、希望怎么改、用什么指标验收”，返修效果会更稳定。
        </p>
      </div>
      <DialogFooter class="gap-2 sm:gap-0">
        <DialogClose as-child>
          <Button type="button" variant="outline" :disabled="isDecidingApproval">取消</Button>
        </DialogClose>
        <Button
          type="button"
          variant="destructive"
          :disabled="!selectedRevisionNode || !revisionFeedback.trim() || isDecidingApproval"
          data-testid="confirm-revision-button"
          @click="handleRevision"
        >
          <LoaderCircle v-if="isDecidingApproval" class="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" />
          {{ isDecidingApproval ? '正在退回…' : '确认退回并重做' }}
        </Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
</template>
