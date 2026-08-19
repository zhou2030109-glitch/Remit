<script setup lang="ts">
import type { CopilotAction } from "@/apis/commonApi";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import HumanApprovalCard from "@/pages/task/components/HumanApprovalCard.vue";
import { useTaskStore } from "@/stores/task";
import { AgentType } from "@/utils/enum";
import type { Message } from "@/utils/response";
import {
	Bot,
	CheckCircle2,
	ChevronRight,
	CircleAlert,
	Info,
	LoaderCircle,
	MessageSquareText,
	Send,
	Sparkles,
	UserRound,
	WandSparkles,
	XCircle,
} from "lucide-vue-next";
import { computed, nextTick, ref, watch } from "vue";
import { agentValueText, parseAgentRecord } from "../agentContent";

const props = defineProps<{
	taskId: string;
	selectedModel: string;
	decidingApproval: boolean;
}>();

const emit = defineEmits<{
	close: [];
	approve: [];
	revise: [];
}>();

const taskStore = useTaskStore();
const inputValue = ref("");
const isSending = ref(false);
const sendError = ref("");
const sendingQuickAction = ref("");
const activityRef = ref<HTMLDivElement | null>(null);

const agentLabels: Record<AgentType, string> = {
	[AgentType.COORDINATOR]: "协调者",
	[AgentType.MODELER]: "建模手",
	[AgentType.CODER]: "代码手",
	[AgentType.WRITER]: "论文手",
	[AgentType.MODEL_SCOUT]: "候选探索",
	[AgentType.MODEL_CRITIC]: "匿名盲审",
};

const currentAgent = computed(() => {
	for (let index = taskStore.messages.length - 1; index >= 0; index--) {
		const message = taskStore.messages[index];
		if (message.msg_type === "agent") return agentLabels[message.agent_type];
	}
	return "协调者";
});

const currentActivity = computed(() => {
	if (taskStore.pendingApproval)
		return `等待你验收：${taskStore.pendingApproval.node_label}`;
	if (!taskStore.isRunning) return "当前没有正在执行的步骤";
	for (let index = taskStore.messages.length - 1; index >= 0; index--) {
		const message = taskStore.messages[index];
		if (message.msg_type === "system" && message.content)
			return message.content;
	}
	return "正在处理当前建模步骤";
});

const visibleMessages = computed(() => taskStore.chatMessages.slice(-30));
const quickActions: CopilotAction[] = [
	"解释当前模型",
	"分析当前结果",
	"检查模型局限",
];

function messageLabel(message: Message) {
	if (message.msg_type === "user") return "你的补充";
	if (message.msg_type === "agent") return agentLabels[message.agent_type];
	return "工作流事件";
}

function messageIcon(message: Message) {
	if (message.msg_type === "user") return UserRound;
	if (message.msg_type === "agent") return Bot;
	if (message.msg_type === "system") {
		if (message.type === "success") return CheckCircle2;
		if (message.type === "warning") return CircleAlert;
		if (message.type === "error") return XCircle;
	}
	return Info;
}

function messageTone(message: Message) {
	if (message.msg_type === "system") {
		if (message.type === "success") return "text-[hsl(var(--success))]";
		if (message.type === "warning") return "text-[hsl(var(--warning))]";
		if (message.type === "error") return "text-[hsl(var(--danger))]";
	}
	return message.msg_type === "agent"
		? "text-primary"
		: "text-muted-foreground";
}

function normalizedContent(message: Message) {
	const content = message.content?.trim() ?? "";
	const parsed = parseAgentRecord(content);
	if (!parsed) return content;
	for (const key of ["run_summary", "summary", "message", "result", "title"]) {
		const value = agentValueText(parsed[key]);
		if (value) return value;
	}
	return content;
}

function previewContent(message: Message) {
	const content = normalizedContent(message).replace(/\n{3,}/g, "\n\n");
	return content.length > 280 ? `${content.slice(0, 280).trimEnd()}…` : content;
}

function isLongMessage(message: Message) {
	return normalizedContent(message).length > 280;
}

function formatTime(value?: string) {
	if (!value) return "";
	const date = new Date(value);
	if (Number.isNaN(date.getTime())) return "";
	return new Intl.DateTimeFormat("zh-CN", {
		hour: "2-digit",
		minute: "2-digit",
		hour12: false,
	}).format(date);
}

async function sendMessage(content = inputValue.value) {
	const normalized = content.trim();
	if (!normalized || isSending.value) return;
	isSending.value = true;
	sendError.value = "";
	try {
		await taskStore.sendUserMessage(props.taskId, normalized);
		if (content === inputValue.value) inputValue.value = "";
	} catch (error) {
		console.error("发送任务消息失败:", error);
		sendError.value = "发送失败，内容已保留，请重试。";
	} finally {
		isSending.value = false;
	}
}

function handleVeto(feedback: string) {
	// 预填退回意见草稿，交给任务页的返修对话框消费
	taskStore.reviseDraft = feedback;
	emit("revise");
}

async function sendQuickAction(action: CopilotAction) {
	if (sendingQuickAction.value) return;
	sendingQuickAction.value = action;
	sendError.value = "";
	try {
		await taskStore.requestCopilot(props.taskId, action);
	} catch (error) {
		console.error("Project Copilot 分析失败:", error);
		sendError.value = "分析失败，请检查模型配置或稍后重试。";
	} finally {
		sendingQuickAction.value = "";
	}
}

watch(
	() => visibleMessages.value.map((message) => message.id).join("|"),
	async () => {
		await nextTick();
		if (activityRef.value)
			activityRef.value.scrollTop = activityRef.value.scrollHeight;
	},
	{ immediate: true },
);
</script>

<template>
  <aside class="flex h-full min-w-0 flex-col bg-card" aria-label="项目 Copilot">
    <header class="shrink-0 border-b px-3.5 py-3">
      <div class="flex items-center gap-2.5">
        <span class="flex h-8 w-8 items-center justify-center rounded-md border bg-[hsl(var(--accent))] text-primary">
          <Bot class="h-4 w-4" aria-hidden="true" />
        </span>
        <div class="min-w-0 flex-1">
          <div class="flex items-center gap-1.5">
            <h2 class="text-xs font-semibold">Project Copilot</h2>
            <span class="rounded bg-muted px-1 py-0.5 text-[8px] font-medium text-muted-foreground">AI</span>
          </div>
          <p class="mt-0.5 truncate text-[10px] text-muted-foreground">{{ currentAgent }} · {{ props.selectedModel || '当前配置模型' }}</p>
        </div>
        <Button type="button" variant="ghost" size="icon" class="h-7 w-7 text-muted-foreground" title="收起 Copilot" aria-label="收起 Copilot" @click="$emit('close')">
          <ChevronRight class="h-4 w-4" aria-hidden="true" />
        </Button>
      </div>

      <div class="mt-3 flex items-start gap-2 rounded-md border bg-[hsl(var(--surface-subtle))] px-2.5 py-2.5">
        <LoaderCircle v-if="taskStore.isRunning" class="mt-0.5 h-3.5 w-3.5 shrink-0 animate-spin text-primary motion-reduce:animate-none" aria-hidden="true" />
        <Sparkles v-else class="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
        <div class="min-w-0">
          <p class="text-[10px] font-semibold">当前执行</p>
          <p class="mt-0.5 line-clamp-2 text-[10px] leading-4 text-muted-foreground">{{ currentActivity }}</p>
        </div>
      </div>
    </header>

    <div class="shrink-0 border-b px-3 py-2">
      <div class="flex flex-wrap gap-1.5">
        <button
          v-for="action in quickActions"
          :key="action"
          type="button"
          class="inline-flex h-7 items-center gap-1 rounded-md border bg-card px-2 text-[10px] text-secondary transition-colors hover:bg-muted/60 disabled:opacity-50"
          :disabled="Boolean(sendingQuickAction) || isSending"
          @click="sendQuickAction(action)"
        >
          <LoaderCircle v-if="sendingQuickAction === action" class="h-3 w-3 animate-spin motion-reduce:animate-none" aria-hidden="true" />
          <WandSparkles v-else class="h-3 w-3" aria-hidden="true" />
          {{ action }}
        </button>
      </div>
    </div>

    <div ref="activityRef" class="min-h-0 flex-1 overflow-y-auto px-3 py-3">
      <HumanApprovalCard
        v-if="taskStore.pendingApproval"
        class="mb-3"
        :approval="taskStore.pendingApproval"
        :deciding="props.decidingApproval"
        @revise="$emit('revise')"
        @approve="$emit('approve')"
        @explain="sendQuickAction('分析当前结果')"
        @veto="handleVeto"
      />

      <div v-if="visibleMessages.length" class="relative space-y-0 before:absolute before:bottom-3 before:left-[13px] before:top-3 before:w-px before:bg-border">
        <article v-for="message in visibleMessages" :key="message.id" class="relative grid grid-cols-[28px_minmax(0,1fr)] gap-2.5 pb-4 last:pb-1">
          <span class="relative z-10 flex h-7 w-7 items-center justify-center rounded-full border bg-card" :class="messageTone(message)">
            <component :is="messageIcon(message)" class="h-3.5 w-3.5" aria-hidden="true" />
          </span>
          <div class="min-w-0 pt-0.5">
            <div class="flex items-center gap-1.5">
              <h3 class="text-[10px] font-semibold">{{ messageLabel(message) }}</h3>
              <span v-if="message.msg_type === 'agent'" class="rounded bg-[hsl(var(--accent))] px-1 py-0.5 text-[8px] font-medium text-primary">AI 生成</span>
              <time v-if="formatTime(message.created_at)" class="ml-auto text-[8px] text-muted-foreground">{{ formatTime(message.created_at) }}</time>
            </div>

            <div class="mt-1 rounded-md border bg-[hsl(var(--surface-subtle))] px-2.5 py-2">
              <p class="whitespace-pre-wrap break-words text-[10px] leading-4 text-secondary">{{ previewContent(message) || '无文本内容' }}</p>
              <details v-if="isLongMessage(message)" class="mt-1.5 border-t pt-1.5">
                <summary class="cursor-pointer text-[9px] font-medium text-primary">展开完整记录</summary>
                <pre class="mt-2 max-h-56 overflow-auto whitespace-pre-wrap break-words font-sans text-[10px] leading-4 text-secondary">{{ normalizedContent(message) }}</pre>
              </details>
            </div>
          </div>
        </article>
      </div>

      <div v-else class="flex h-full min-h-40 items-center justify-center text-center">
        <div class="max-w-52">
          <MessageSquareText class="mx-auto h-5 w-5 text-muted-foreground" aria-hidden="true" />
          <p class="mt-2 text-[11px] font-medium">暂无协作记录</p>
          <p class="mt-1 text-[9px] leading-4 text-muted-foreground">Agent 输出和你的补充会以时间线形式出现。</p>
        </div>
      </div>
    </div>

    <form class="shrink-0 border-t bg-card p-3" data-testid="task-message-form" @submit.prevent="sendMessage()">
      <div class="rounded-lg border bg-background p-1.5 focus-within:ring-1 focus-within:ring-ring">
        <Textarea
          v-model="inputValue"
          rows="2"
          class="min-h-14 resize-none border-0 bg-transparent px-2 py-1.5 text-[11px] shadow-none focus-visible:ring-0"
          placeholder="补充约束、质疑结果或要求解释…"
          :disabled="isSending"
          @keydown.ctrl.enter.prevent="sendMessage()"
          @keydown.meta.enter.prevent="sendMessage()"
        />
        <div class="flex items-center justify-between gap-2 px-1 pb-0.5">
          <span class="text-[8px] text-muted-foreground">Ctrl / ⌘ + Enter 发送</span>
          <Button type="submit" size="icon" class="h-7 w-7" aria-label="发送补充消息" :disabled="!inputValue.trim() || isSending">
            <LoaderCircle v-if="isSending" class="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" aria-hidden="true" />
            <Send v-else class="h-3.5 w-3.5" aria-hidden="true" />
          </Button>
        </div>
      </div>
      <p v-if="sendError" role="alert" class="mt-1.5 text-[9px] text-[hsl(var(--danger))]">{{ sendError }}</p>
      <p v-else class="mt-1.5 text-[9px] leading-4 text-muted-foreground">补充内容会实时注入正在执行的步骤（Agent 下一轮对话即生效），并保存到任务历史。</p>
    </form>
  </aside>
</template>
