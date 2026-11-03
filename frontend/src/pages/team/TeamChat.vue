<script setup lang="ts">
import PaperProposalReview from "@/pages/writing/PaperProposalReview.vue";
import {
	type ExecutionBackend,
	explainModelingSubmissionFailure,
} from "@/apis/submitModelingApi";
import {
	type CompetitionProfile,
	type PaperProposal,
	type ProjectEntry,
	type TeamAction,
	type TeamEvent,
	type TeamState,
	decidePaperProposal,
	deleteProject,
	getCompetitions,
	getPaperProposals,
	getProjects,
	getTeamState,
	mergeTeamEvents,
	prepareProject,
	sendTeamMessage,
	updateProject,
	uploadProjectAttachments,
} from "@/apis/teamApi";
import ProblemPdfDropzone from "@/components/ProblemPdfDropzone.vue";
import ApiDialog from "@/pages/chat/components/ApiDialog.vue";
import HumanApprovalCard from "@/pages/team/HumanApprovalCard.vue";
import type PaperEditor from "@/pages/writing/PaperEditor.vue";
import { useMediaQuery } from "@vueuse/core";
import {
	Archive,
	ArrowUp,
	Check,
	Copy,
	FileText,
	FolderOpen,
	ListChecks,
	LoaderCircle,
	MessageSquare,
	MoreHorizontal,
	PanelLeft,
	Paperclip,
	Plus,
	Settings2,
	Search,
	SlidersHorizontal,
	Square,
	X,
} from "lucide-vue-next";
import {
	AlertDialogCancel,
	AlertDialogContent,
	AlertDialogDescription,
	AlertDialogOverlay,
	AlertDialogRoot,
	AlertDialogTitle,
} from "reka-ui";
import {
	computed,
	defineAsyncComponent,
	h,
	nextTick,
	onBeforeUnmount,
	onMounted,
	ref,
	shallowRef,
	watch,
} from "vue";
import { RouterLink, useRoute, useRouter } from "vue-router";
import { readBrowserStorage, writeBrowserStorage } from "@/utils/browserStorage";
import ActivitySummary from "./ActivitySummary.vue";
import { useTeamStream } from "./useTeamStream";
import { explainConversationError } from "./conversationErrors";
import MessageContent from "./MessageContent.vue";
import RoleAvatar from "./RoleAvatar.vue";
import RemitWelcome from "./RemitWelcome.vue";
import { roleLabels as labels } from "./roles";
import ProjectFiles from "./ProjectFiles.vue";
import WorkspaceLibrary from "./WorkspaceLibrary.vue";
import ComposerSelect from "./ComposerSelect.vue";
import FloatingPanel from "./FloatingPanel.vue";
import UserMessage from "./UserMessage.vue";
import RuntimePanel from "./RuntimePanel.vue";
import TaskFailureNotice from "./TaskFailureNotice.vue";

const props = defineProps<{ task_id?: string }>();
const approvalExplanationRequest =
	"请用第一次参加数模比赛的人也能听懂的话解释当前成果。用约200至400字说清：现在做了什么、哪些还没做、最重要的1至2个风险、点批准后会做什么以及你的建议。风险请用这道题的具体例子解释，不要堆算法缩写、内部状态或文件名。如果还没计算，直接说明目前只有方案、效果尚未验证。只解释，不批准、不启动或重做任务。";
const PaperEditorView = defineAsyncComponent({
	loader: () =>
		import("@/pages/writing/PaperEditor.vue").then((module) => module.default),
	errorComponent: {
		render: () =>
			h(
				"p",
				{ role: "alert", style: "padding:24px" },
				"论文界面加载失败。请先保留未发送的消息，再刷新页面重试；已保存的项目仍保留。",
			),
	},
});
const competitions = ref<CompetitionProfile[]>([]);
const competitionId = ref("cumcm");
const competitionYear = ref(2026);
const paperLanguage = ref("");
const competitionRequirements = ref("");
const literatureEnabled = ref(true);
const taskPurpose = ref<"modeling" | "numerical_verification">("modeling");
const contestOptionsOpen = ref(false);
const contestTrigger = ref<HTMLElement | null>(null);
const adjustmentTrigger = ref<HTMLElement | null>(null);
const optionsTrigger = ref<HTMLElement | null>(null);
const boardTrigger = ref<HTMLElement | null>(null);
const attachmentTrigger = ref<HTMLElement | null>(null);
const selectedCompetition = computed(() =>
	competitions.value.find((item) => item.id === competitionId.value),
);
const router = useRouter();
const history = ref<ProjectEntry[]>([]);
const route = useRoute();
const view = computed(() =>
	route.query.view === "paper"
		? "paper"
		: route.query.view === "files"
			? "files"
			: "chat",
);
const workspaceViews = [
 { id: "chat", label: "对话", icon: MessageSquare },
 { id: "files", label: "文件与结果", icon: FolderOpen },
 { id: "paper", label: "论文", icon: FileText },
];
const libraryOpen = computed(() => !props.task_id && (view.value !== "chat" || route.query.browse === "1"));
const viewLabel = computed(() => workspaceViews.find(item => item.id === view.value)?.label || "对话");
function libraryUrl(value: string) {
 return `/home?view=${value}&browse=1`;
}
function projectUrl(id: string) {
 return `/project/${encodeURIComponent(id)}${view.value === "chat" ? "" : `?view=${view.value}`}`;
}
const paperOpened = ref(view.value === "paper");
const paperEditor = ref<InstanceType<typeof PaperEditor> | null>(null);
const chatOpen = ref(false);
const editPaper = ref(true);
const proposals = ref<PaperProposal[]>([]);
const deciding = ref("");
const showArchived = ref(false);
const projectMenu = ref("");
const renameId = ref("");
const renameText = ref("");
const deleteTarget = ref<ProjectEntry | null>(null);
const deleting = ref(false);
const deleteError = ref("");
function requestDelete(project: ProjectEntry) {
	deleteTarget.value = project;
	deleteError.value = "";
	projectMenu.value = "";
}
function closeDelete(open: boolean) {
	if (!open && !deleting.value) deleteTarget.value = null;
}
async function confirmDelete() {
	const project = deleteTarget.value;
	if (!project || deleting.value) return;
	deleting.value = true;
	deleteError.value = "";
	try {
		await deleteProject(project.task_id);
		history.value = history.value.filter(
			(item) => item.task_id !== project.task_id,
		);
		writeBrowserStorage("sessionStorage", `team-draft:${project.task_id}`, null);
		deleteTarget.value = null;
		if (project.task_id === props.task_id) {
			closeStream();
			await router.push("/home");
		}
	} catch (cause) {
		deleteError.value = explainModelingSubmissionFailure(cause);
	} finally {
		deleting.value = false;
	}
}
const attachmentMenu = ref(false);
const optionsOpen = ref(false);
const runtimeOpen = ref(false);
const runtimeAnchor = shallowRef<HTMLElement>();
function toggleRuntime(event: MouseEvent) {
 runtimeAnchor.value = event.currentTarget as HTMLElement;
 runtimeOpen.value = !runtimeOpen.value;
}
const displayOptions = ref<HTMLElement | null>(null);

const copied = ref<number | null>(null);
const composerInput = ref<HTMLTextAreaElement | null>(null);
const adjustmentOpen = ref(false);
type Adjustment = "immediate" | "after_step";
async function copyReply(event: TeamEvent) {
	try {
		await navigator.clipboard.writeText(
			event.kind === "error" ? explainConversationError(event.content) : event.content,
		);
		copied.value = event.seq;
	} catch {
		error.value = "复制失败，请选择文字后复制。";
	}
}
watch(view, (value) => {
	if (value === "paper") {
		paperOpened.value = true;
		boardOpen.value = false;
	}
});
async function changeView(value: string) {
	await router.push({
		path: props.task_id ? `/project/${props.task_id}` : "/home",
		query: value === "chat" ? {} : { view: value },
	});
}
async function manageProject(project: ProjectEntry, archive: boolean) {
	try {
		await updateProject(project.task_id, { archived: archive });
		projectMenu.value = "";
		await loadHistory();
		if (project.task_id === props.task_id) await refreshState();
	} catch (cause) {
		error.value = explainModelingSubmissionFailure(cause);
	}
}
async function renameProject() {
	try {
		await updateProject(renameId.value, { title: renameText.value });
		renameId.value = "";
		await loadHistory();
		if (props.task_id) await refreshState();
	} catch (cause) {
		error.value = explainModelingSubmissionFailure(cause);
	}
}
async function refreshProposals() {
	if (props.task_id)
		proposals.value = (await getPaperProposals(props.task_id)).data;
}
async function decideProposal(proposal: PaperProposal, accept: boolean) {
	if (!props.task_id || deciding.value) return;
	deciding.value = proposal.id;
	try {
		if (accept && paperEditor.value && !(await paperEditor.value.save()))
			throw new Error("源码有未保存修改，请先处理保存冲突。");
		const result = (
			await decidePaperProposal(props.task_id, proposal.id, accept)
		).data;
		if (accept) await paperEditor.value?.reloadAccepted();
		await refreshProposals();
		if (result.compile === "failed")
			error.value = "修改已保存，编译未通过，请查看论文编辑器中的日志。";
	} catch (cause) {
		error.value = explainModelingSubmissionFailure(cause);
	} finally {
		deciding.value = "";
	}
}
const historyError = ref("");
const historyLoading = ref(true);
const search = ref("");
const state = ref<TeamState | null>(null);
// Event records are immutable; replay replaces the array and any corrected record.
const events = shallowRef<TeamEvent[]>([]);
const draft = ref("");
const sending = ref(false);
const error = ref("");
let initialStateError = "";
const { connection, open: openStream, close: closeStream } = useTeamStream(
	(incoming, nextState) => {
		if (initialStateError && error.value === initialStateError) error.value = "";
		initialStateError = "";
		if (incoming.length) events.value = mergeTeamEvents(events.value, incoming);
		state.value = nextState;
		const entry = history.value.find((item) => item.task_id === props.task_id);
		if (entry) {
			entry.title = nextState.title;
			entry.status = nextState.status;
		}
		if (incoming.some((event) => ["proposal", "review"].includes(event.kind)))
			void refreshProposals().catch(() => {});
	},
	() => { void router.push("/home"); },
);
const CONNECTION_HINT =
	"页面与本地服务之间的实时同步状态。恢复后自动补齐消息并读取任务状态；长时间未恢复请检查本地服务是否仍在运行。";
const settingsOpen = ref(false);
const sidebarOpen = ref(false);
const narrowScreen = useMediaQuery("(max-width: 850px)");
const sidebarCollapsed = ref(
	readBrowserStorage("localStorage", "remit-sidebar-collapsed") === "true",
);
const sidebarVisible = computed(() =>
	narrowScreen.value ? sidebarOpen.value : !sidebarCollapsed.value,
);
function toggleSidebar() {
	runtimeOpen.value = false;
	if (narrowScreen.value) sidebarOpen.value = !sidebarOpen.value;
	else {
		sidebarCollapsed.value = !sidebarCollapsed.value;
		writeBrowserStorage(
			"localStorage",
			"remit-sidebar-collapsed",
			String(sidebarCollapsed.value),
		);
	}
}
const boardOpen = ref(false);
const uploadsOpen = ref(false);
const fileInput = ref<HTMLInputElement | null>(null);
const folderInput = ref<HTMLInputElement | null>(null);
const uploadProgress = ref<number | null>(null);
const attachmentNotice = ref("");
const attachments = ref<File[]>([]);
const attachmentGroups = computed(() => {
	const groups = new Map<
		string,
		{ key: string; label: string; count: number; folder: boolean }
	>();
	for (const file of attachments.value) {
		const folder = !!file.webkitRelativePath;
		const label = folder ? file.webkitRelativePath.split("/")[0] : file.name;
		const key = `${folder ? "folder" : "file"}:${label}`;
		const group = groups.get(key) || { key, label, count: 0, folder };
		group.count += 1;
		groups.set(key, group);
	}
	return [...groups.values()];
});
function removeAttachmentGroup(key: string) {
	attachments.value = attachments.value.filter(
		(file) =>
			(file.webkitRelativePath
				? `folder:${file.webkitRelativePath.split("/")[0]}`
				: `file:${file.name}`) !== key,
	);
}
const problemDocument = ref<File | null>(null);
const problemText = ref("");
const executionBackend = ref<ExecutionBackend>("python");
const target = ref("all");
const scrollArea = ref<HTMLElement | null>(null);
const following = ref(true);
const visibleCount = ref(120);
const filter = ref("all");
let disposed = false;
let pendingRequest: Parameters<typeof sendTeamMessage>[1] | null = null;
const statuses: Record<string, string> = {
	chat: "对话中",
	idle: "尚未开始",
	preparing: "准备中",
	pending: "待执行",
	running: "执行中",
	completed: "已完成",
	awaiting_approval: "待验收",
	failed: "失败",
	warning: "部分完成，需核验",
	skipped: "已跳过，未验证",
	stopped: "已停止",
	stopping: "正在停止",
	cancelled: "已取消",
	interrupted: "已中断",
	ready: "可开始",
	needs_info: "待补充",
	blocked: "等待建模成果",
};
const projects = computed(() =>
	history.value.filter(
		(item) =>
			item.archived === showArchived.value &&
			item.title.toLowerCase().includes(search.value.toLowerCase()),
	),
);
const filteredEvents = computed(() =>
	events.value.filter(
		(event) =>
			event.kind !== "internal" &&
			event.event_key !== "archive-imported" &&
			(filter.value === "all" || event.role === filter.value),
	),
);
// Page conversation groups, so heartbeats cannot evict their own milestones.
const visibleEvents = filteredEvents;
const timelinePlanId = computed(() => state.value?.preflight?.id);
const timelineItems = computed(() => {
	const items: {
		id: number | string;
		event?: TeamEvent;
		preflight?: boolean;
		activity: TeamEvent[];
	}[] = [];
	const planId = timelinePlanId.value;
	const anchor = events.value.find(
		(event) => event.kind === "preflight" && event.data.id === planId,
	);
	if (
		planId &&
		(filter.value === "all" || filter.value === "coordinator") &&
		(!anchor || !visibleEvents.value.some((event) => event.seq === anchor.seq))
	) {
		items.push({ id: `preflight:${planId}`, preflight: true, activity: [] });
	}
	for (const event of visibleEvents.value) {
		if (planId && event.seq === anchor?.seq) {
			items.push({ id: event.seq, preflight: true, activity: [] });
			continue;
		}
		if (
			["chat", "reply", "error", "execution_summary", "review"].includes(
				event.kind,
			)
		)
			items.push({ id: event.seq, event, activity: [] });
		else {
			let last = items[items.length - 1];
			if (!last || last.event || last.preflight) {
				last = { id: event.seq, activity: [] };
				items.push(last);
			}
			last.activity.push(event);
		}
	}
	return items;
});
const visibleTimelineItems = computed(() =>
	timelineItems.value.slice(-visibleCount.value),
);
const latestActivityId = computed(
	() =>
		[...timelineItems.value].reverse().find((item) => item.activity.length)?.id,
);
const canSend = computed(
	() =>
		!sending.value &&
		(!!draft.value.trim() ||
			(!props.task_id && (!!problemText.value || !!attachments.value.length))),
);
const conversationPending = computed(() =>
	state.value?.commands?.some((command) => command.status === "running"),
);

function statusLabel(status: string) {
	return statuses[status] || status;
}
function roleStatus(role: string) {
	if (role === "coordinator" && conversationPending.value) return "running";
	const steps = state.value?.steps.filter((step) => step.role === role) ?? [];
	return (
		steps.find((step) =>
			[
				"running",
				"awaiting_approval",
				"failed",
				"warning",
				"interrupted",
				"stopped",
				"cancelled",
				"blocked",
			].includes(step.status),
		)?.status ||
		(steps.length && steps.every((step) => step.status === "completed")
			? "completed"
			: "pending")
	);
}
function formatTime(time: string) {
	return new Date(time).toLocaleTimeString("zh-CN", {
		hour: "2-digit",
		minute: "2-digit",
	});
}
function eventLink(event: TeamEvent) {
	const link = event.data.link;
	return typeof link === "string" &&
		props.task_id &&
		link === `/writing/${props.task_id}`
		? link
		: null;
}
function trackScroll() {
	const el = scrollArea.value;
	if (el)
		following.value = el.scrollHeight - el.scrollTop - el.clientHeight < 100;
}
async function scrollToLatest() {
	await nextTick();
	const el = scrollArea.value;
	if (el) el.scrollTop = el.scrollHeight;
	following.value = true;
}
async function importAttachments(files: File[], documentText = "") {
	if (!files.length) return false;
	if (sending.value) {
		attachmentNotice.value = "正在处理上一项操作，请完成后再导入文件。";
		return false;
	}
	error.value = "";
	if (props.task_id) {
		try {
			sending.value = true;
			uploadProgress.value = 0;
			await uploadProjectAttachments(
				props.task_id,
				files,
				documentText,
				(percent) => {
					uploadProgress.value = percent;
				},
			);
			attachmentNotice.value = `已导入 ${files.length} 个文件，可在“文件与结果”查看。`;
			await refreshState();
		} catch (cause) {
			error.value = explainModelingSubmissionFailure(cause);
			return false;
		} finally {
			sending.value = false;
			uploadProgress.value = null;
		}
	} else {
		const existing = new Set(
			attachments.value.map((file) => file.webkitRelativePath || file.name),
		);
		for (const file of files) {
			const path = file.webkitRelativePath || file.name;
			if (!existing.has(path)) {
				attachments.value.push(file);
				existing.add(path);
			}
		}
	}
	return true;
}
async function selectAttachments(selected: File[]) {
	attachmentNotice.value = "";
	const files = selected.filter(
		(file) =>
			!(file.webkitRelativePath || file.name)
				.split("/")
				.some(
					(part) =>
						part.startsWith(".") || /^(thumbs\.db|desktop\.ini)$/i.test(part),
				),
	);
	const skipped = selected.length - files.length;
	await importAttachments(files);
	if (skipped)
		attachmentNotice.value += ` 已跳过 ${skipped} 个隐藏或系统文件。`;
}
async function addAttachments(event: Event) {
	const input = event.target as HTMLInputElement;
	const selected = Array.from(input.files || []);
	input.value = "";
	await selectAttachments(selected);
}
function acceptFileDrag(event: DragEvent) {
	if (event.dataTransfer?.types.includes("Files")) event.preventDefault();
}
async function dropAttachments(event: DragEvent) {
	const files = Array.from(event.dataTransfer?.files || []);
	if (!files.length) return;
	event.preventDefault();
	await selectAttachments(files);
}
async function acceptProblemDocument(payload: { file: File; text: string }) {
	if (props.task_id) {
		if (await importAttachments([payload.file], payload.text))
			uploadsOpen.value = false;
	} else {
		problemDocument.value = payload.file;
		problemText.value = payload.text;
		uploadsOpen.value = false;
	}
}
async function loadHistory() {
	historyLoading.value = true;
	historyError.value = "";
	try {
		const response = await getProjects();
		if (!disposed) history.value = response.data;
	} catch {
		if (!disposed) historyError.value = "项目列表读取失败，请刷新重试。";
	} finally {
		if (!disposed) historyLoading.value = false;
	}
}
async function refreshState() {
	if (!props.task_id) return;
	const response = await getTeamState(props.task_id);
	if (!disposed) state.value = response.data;
}
async function send(
	content = draft.value,
	action?: TeamAction,
	timing?: Adjustment,
) {
	if (
		sending.value ||
		(!content.trim() && !problemText.value && !attachments.value.length)
	)
		return;
	adjustmentOpen.value = false;
	sending.value = true;
	error.value = "";
	try {
		if (!props.task_id) {
			const response = await prepareProject(
				{
					ques_all:
						problemText.value ||
						content ||
						"请检查我上传的附件，先和我确认任务目标。",
					user_requirements: problemText.value ? content : "",
					execution_backend: executionBackend.value,
					competition_id: competitionId.value,
					competition_year: String(competitionYear.value),
					paper_language: paperLanguage.value,
					competition_requirements: competitionRequirements.value,
					literature_enabled: String(literatureEnabled.value),
					task_purpose: taskPurpose.value,
				},
				[
					...attachments.value,
					...(problemDocument.value ? [problemDocument.value] : []),
				],
			);
			writeBrowserStorage("sessionStorage", "team-draft:home", null);
			await router.push(`/project/${response.data.task_id}`);
			return;
		}
		const message =
			target.value === "all" || action
				? content
				: `@${labels[target.value]} ${content}`;
		if (
			!pendingRequest ||
			pendingRequest.content !== message ||
			pendingRequest.action !== action ||
			pendingRequest.timing !== timing ||
			pendingRequest.role !== target.value ||
			(action === "approve" && pendingRequest.checkpoint_id !==
				state.value?.pending_approval?.checkpoint_id) ||
			(action === "start" && pendingRequest.plan_id !== state.value?.preflight?.id)
		)
			pendingRequest = {
				request_id: crypto.randomUUID(),
				content: message,
				action,
				timing,
				role: target.value,
				conversation_only: !action && !timing && state.value?.status === "running",
				checkpoint_id: state.value?.pending_approval?.checkpoint_id,
				plan_id: state.value?.preflight?.id,
				paper_context:
					action === "edit_paper"
						? await paperEditor.value?.paperContext()
						: undefined,
			};
		await sendTeamMessage(props.task_id, pendingRequest);
		pendingRequest = null;
		if (content === draft.value) draft.value = "";
		await scrollToLatest();
	} catch (cause) {
		error.value = explainModelingSubmissionFailure(cause);
	} finally {
		sending.value = false;
	}
}
function setDraft(value: string) {
	draft.value = value;
	void nextTick(() => composerInput.value?.focus());
}
function handleKey(event: KeyboardEvent) {
	if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
		event.preventDefault();
		if (canSend.value)
			void send(
				draft.value,
				view.value === "paper" && editPaper.value ? "edit_paper" : undefined,
			);
	}
}

watch(draft, (value) =>
	writeBrowserStorage("sessionStorage", `team-draft:${props.task_id || "home"}`, value),
);
watch(draft, async () => {
	await nextTick();
	const input = composerInput.value;
	if (input) {
		input.style.height = "auto";
		input.style.height = `${Math.min(160, input.scrollHeight)}px`;
	}
});
watch(
	() => events.value.length,
	() => {
		if (following.value) void scrollToLatest();
	},
);
onMounted(async () => {
	draft.value =
		readBrowserStorage("sessionStorage", `team-draft:${props.task_id || "home"}`) || "";
	void loadHistory();
	if (!props.task_id) {
		try {
			competitions.value = (await getCompetitions()).data;
		} catch {
			error.value = "赛事配置读取失败，请刷新重试。";
		}
	}
	if (!props.task_id) return;
	try {
		await refreshState();
		void refreshProposals().catch(() => {});
	} catch (cause) {
		if (!disposed) {
			initialStateError = explainModelingSubmissionFailure(cause);
			error.value = initialStateError;
		}
	} finally {
		if (!disposed) openStream(props.task_id);
	}
});
onBeforeUnmount(() => {
	disposed = true;
});
</script>

<template>
<div class="team-app" :class="{ 'sidebar-open': sidebarOpen, 'sidebar-collapsed': sidebarCollapsed }" @keydown.esc="optionsOpen = false; adjustmentOpen = false; attachmentMenu = false; projectMenu = ''; sidebarOpen = false">
 <aside class="project-rail" aria-label="项目导航" :aria-hidden="!sidebarVisible" :inert="!sidebarVisible || undefined">
  <RouterLink to="/home" class="brand"><img src="@/assets/remit-icon.png" alt="Remit Logo" /><strong>Remit</strong></RouterLink>
  <RouterLink class="new-chat" :class="{ active: !task_id && !libraryOpen }" to="/home" @click="sidebarOpen = false"><Plus :size="17" />新建项目</RouterLink>
  <nav class="rail-views" aria-label="工作区导航"><RouterLink v-for="tab in workspaceViews" :key="tab.id" :to="libraryUrl(tab.id)" class="rail-navigation" :class="{ selected: (task_id || libraryOpen) && view === tab.id }" :aria-current="(task_id || libraryOpen) && view === tab.id ? 'page' : undefined" @click="sidebarOpen = false"><component :is="tab.icon" :size="17" /><span>{{ tab.label }}</span></RouterLink></nav>
  <label class="project-search-field"><Search :size="15" aria-hidden="true" /><input v-model="search" aria-label="搜索项目" class="project-search" placeholder="搜索项目" /></label>
  <button class="rail-navigation" aria-label="切换归档项目" :aria-pressed="showArchived" :class="{ selected: showArchived }" @click="showArchived = !showArchived"><Archive :size="16" /><span>{{ showArchived ? '返回全部项目' : '项目归档' }}</span></button>
  <div class="rail-caption"><span>{{ showArchived ? '归档项目' : '项目' }}</span></div>
  <nav class="project-list">
   <div v-for="project in projects" :key="project.task_id" class="project-row" :class="{ selected: project.task_id === task_id }">
    <RouterLink :to="projectUrl(project.task_id)" @click="sidebarOpen = false"><MessageSquare :size="15" /><span>{{ project.title }}</span></RouterLink>
    <button :aria-label="'管理项目 ' + project.title" class="project-more" @click="projectMenu = projectMenu === project.task_id ? '' : project.task_id">···</button>
    <div v-if="projectMenu === project.task_id" class="project-menu"><button @click="renameId = project.task_id; renameText = project.title; projectMenu = ''">重命名</button><button @click="manageProject(project, !project.archived)">{{ project.archived ? '恢复项目' : '归档项目' }}</button><button v-if="project.archived" class="danger-text" @click="requestDelete(project)">删除项目</button></div>
   </div>
   <div v-if="!projects.length" class="rail-empty"><p>{{ historyError || (search ? '没有匹配的项目' : showArchived ? '没有归档项目' : '暂无项目') }}</p></div>
  </nav>
  <footer class="rail-footer"><button class="rail-navigation" data-runtime-trigger aria-haspopup="dialog" :aria-expanded="runtimeOpen" @click="toggleRuntime($event)"><SlidersHorizontal :size="16" />环境与诊断</button><button class="rail-navigation" @click="settingsOpen = true"><Settings2 :size="16" />设置</button></footer>
 </aside>
 <button v-if="sidebarOpen" class="rail-scrim" aria-label="关闭项目导航" @click="sidebarOpen = false" />
 <main class="workspace">
  <header class="workspace-header">
   <button class="icon-button" aria-label="切换项目导航" :aria-expanded="narrowScreen ? sidebarOpen : !sidebarCollapsed" :title="(narrowScreen ? sidebarOpen : !sidebarCollapsed) ? '折叠侧栏' : '展开侧栏'" @click="toggleSidebar"><PanelLeft :size="19" /></button>
   <div class="header-title"><RouterLink v-if="task_id" :to="libraryUrl(view)" class="header-library-link">{{ viewLabel }}</RouterLink><span v-if="task_id" class="header-separator" aria-hidden="true">/</span><span>{{ task_id ? state?.title || '加载项目…' : libraryOpen ? viewLabel : '新建项目' }}</span><small v-if="state && !['chat','idle'].includes(state.status)">{{ statusLabel(state.status) }}</small><small v-if="state?.task_purpose === 'numerical_verification'" title="仅核对指定方法与给定输入的计算，不证明泛化能力">数值核验</small></div>
   <div class="header-actions"><span v-if="task_id && connection !== '实时同步'" class="connection" :title="CONNECTION_HINT">{{ connection }}</span><button v-if="task_id && view !== 'chat'" class="icon-button" aria-label="切换项目对话" :aria-pressed="chatOpen" @click="chatOpen = !chatOpen; boardOpen = false"><MessageSquare :size="18" /></button><button v-if="task_id" ref="boardTrigger" class="icon-button" :aria-expanded="boardOpen" aria-label="切换共享状态表" @click="boardOpen = !boardOpen; chatOpen = false"><ListChecks :size="18" /></button><button v-if="task_id" ref="optionsTrigger" class="icon-button" aria-label="对话显示选项" :aria-expanded="optionsOpen" @click="optionsOpen = !optionsOpen"><MoreHorizontal :size="18" /></button></div>
  </header>
  <RuntimePanel v-model:open="runtimeOpen" :task-id="task_id" :anchor="runtimeAnchor" placement="top" @settings="settingsOpen = true" />

  <FloatingPanel v-model:open="optionsOpen" :anchor="optionsTrigger" title="对话显示选项" side="bottom" :width="280"><div ref="displayOptions"><ComposerSelect v-model="filter" label="筛选角色" title="筛选角色" :options="[{value:'all',label:'全部角色'},...Object.entries(labels).map(([value,label])=>({value,label}))]" /></div></FloatingPanel>
  <div v-if="state?.archived" class="archive-banner">此项目已归档。请在左侧项目菜单中恢复后继续。</div>
  <div class="workspace-content">
   <WorkspaceLibrary v-if="libraryOpen" :view="view" :projects="history" :loading="historyLoading" :error="historyError" @retry="loadHistory" />
   <section v-show="task_id && view === 'paper'" class="paper-host"><PaperEditorView v-if="task_id && paperOpened" ref="paperEditor" :task_id="task_id" embedded /></section>
   <ProjectFiles v-if="task_id && view === 'files'" :task_id="task_id" :sequence="state?.sequence || 0" />
   <section v-show="!libraryOpen && (view === 'chat' || chatOpen)" class="conversation" :class="{copilot:view !== 'chat','new-project':!task_id}">
    <header v-if="view !== 'chat'" class="copilot-heading"><strong>项目对话</strong><button class="icon-button" aria-label="关闭项目对话" @click="chatOpen = false"><X :size="16" /></button></header>
    <RemitWelcome v-if="!task_id" />
    <div v-else ref="scrollArea" class="conversation-scroll" @scroll="trackScroll">
     <section class="timeline" aria-label="团队对话与执行记录">

      <button v-if="timelineItems.length > visibleCount" class="text-link" @click="visibleCount += 150">查看更早的记录</button>
      <p v-if="!events.length" class="muted">正在读取对话…</p>
      <template v-for="item in visibleTimelineItems" :key="item.id">
      <section v-if="item.preflight && state?.preflight" class="review-card" aria-label="赛题预读计划"><h2>赛题与执行计划</h2><p>{{ state.preflight.understanding }}</p><div v-for="file in state.preflight.attachments" :key="file.file" class="attachment-check"><Paperclip :size="13" /><span>{{ file.file }}</span><small>{{ file.status === 'parsed' ? '已解析' : '保留原件 · 执行时读取' }}</small></div><ol><li v-for="step in state.preflight.steps" :key="step">{{ step }}</li></ol><template v-if="state.preflight.questions.length"><h3>开始前需要补充</h3><ul><li v-for="question in state.preflight.questions" :key="question">{{ question }}</li></ul><p class="muted">在下方回复，协调手会更新计划。</p></template><button v-else class="primary-button" :disabled="sending || conversationPending || state.archived" @click="send('确认当前计划，开始建模','start')">确认计划，开始建模</button></section>
       <article v-else-if="item.event" class="event" :class="{ 'user-event':item.event.role === 'user','event-error':item.event.kind === 'error' }">
        <header v-if="item.event.role !== 'user'" class="role-heading"><RoleAvatar :role="item.event.role" /><span>{{ labels[item.event.role] || 'Remit' }}</span></header>
        <UserMessage v-if="item.event.role === 'user'" :content="item.event.content" />
        <MessageContent v-else :content="item.event.kind === 'error' ? explainConversationError(item.event.content) : item.event.content" :task-id="task_id" />
        <button v-if="eventLink(item.event)" class="text-link" @click="changeView('paper')">打开论文 →</button>
        <footer class="message-tools"><span v-if="item.event.role === 'user'">你</span><time>{{ formatTime(item.event.at) }}</time><button v-if="item.event.role !== 'user'" class="icon-button" :aria-label="copied === item.event.seq ? '已复制回复' : '复制回复'" @click="copyReply(item.event)"><Check v-if="copied === item.event.seq" :size="14" /><Copy v-else :size="14" /></button></footer>
       </article>
       <ActivitySummary v-else :events="item.activity"  :state="item.id === latestActivityId ? state : undefined" />
      </template>
      <div v-if="conversationPending" class="thinking"><LoaderCircle :size="15" class="spin" />正在等待回复…</div>
      <HumanApprovalCard v-if="state?.pending_approval" :approval="state.pending_approval" :deciding="sending" @approve="send('批准当前步骤','approve')" @revise="setDraft('请退回当前步骤重做，修改要求：')" @explain="send(approvalExplanationRequest)" @veto="feedback => setDraft('请退回重做：' + feedback)" />
      <PaperProposalReview v-for="proposal in proposals" :key="proposal.id" :proposal="proposal" :disabled="!!deciding" :processing="deciding === proposal.id" @decide="decideProposal(proposal,$event)" />
     </section>
    </div>
    <div class="composer-area">
     <TaskFailureNotice v-if="state && (state.status === 'failed' || (state.status === 'stopped' && state.failure))" :state="state" :sending="sending || conversationPending || !!state.archived" @resume="send('重试当前失败步骤','resume')" @resumed="refreshState" @settings="settingsOpen = true" @view="value => router.replace({ query: { ...route.query, view: value } })" />
     <button v-if="!following" class="latest-button" @click="scrollToLatest">回到最新 ↓</button>
     <FloatingPanel v-model:open="uploadsOpen" :anchor="attachmentTrigger" title="赛题文档" :width="460"><ProblemPdfDropzone @parsed="acceptProblemDocument" @cleared="() => { problemDocument = null; problemText = ''; }" /></FloatingPanel>
     <div v-if="!task_id" class="attachment-list"><span v-if="problemDocument"><FileText :size="13" />{{ problemDocument.name }}<button aria-label="移除赛题文档" @click="problemDocument = null; problemText = ''"><X :size="12" /></button></span><span v-for="group in attachmentGroups" :key="group.key"><FolderOpen v-if="group.folder" :size="13" /><Paperclip v-else :size="13" />{{ group.label }}<small v-if="group.folder">{{ group.count }} 个文件</small><button :aria-label="'移除 ' + group.label" @click="removeAttachmentGroup(group.key)"><X :size="12" /></button></span></div>
     <p v-if="uploadProgress !== null" role="status" class="attachment-notice">{{ uploadProgress < 100 ? `正在上传 ${uploadProgress}%` : '正在保存附件…' }}</p><p v-if="attachmentNotice" role="status" class="attachment-notice">{{ attachmentNotice }}</p>
     <FloatingPanel v-if="!task_id" v-model:open="contestOptionsOpen" :anchor="contestTrigger" title="赛事设置" :width="370">
      <div class="contest-popover-form">
       <p class="contest-name">{{ selectedCompetition?.name }}</p>
       <div class="contest-setting-row"><label for="contest-year">年份</label><input id="contest-year" v-model.number="competitionYear" aria-label="赛事年份" type="number" min="2000" max="2100" /></div>
       <div class="contest-setting-row"><span>论文语言</span><ComposerSelect v-model="paperLanguage" label="论文语言" title="论文语言" :options="[{value:'',label:'赛事默认'},{value:'zh',label:'中文'},{value:'en',label:'英文'}]" /></div>
       <div class="contest-setting-row"><span>任务目标</span><ComposerSelect :model-value="taskPurpose" @update:model-value="taskPurpose = $event as 'modeling' | 'numerical_verification'" label="任务目标" title="任务目标" :options="[{value:'modeling',label:'建模与性能评估'},{value:'numerical_verification',label:'指定计算与数值核验'}]" /></div>
       <small v-if="taskPurpose === 'numerical_verification'">仅核对给定方法和输入的计算结果，不证明泛化能力。</small>
       <label class="contest-setting-row"><span>建模前检索文献</span><input v-model="literatureEnabled" type="checkbox" aria-label="建模前检索文献" /></label>
       <small>只依赖已有方法的小任务可关闭；附件核验与计算检查仍会执行。</small>
       <label class="contest-notes">补充要求<textarea v-model="competitionRequirements" aria-label="补充赛事要求" placeholder="组别、题号、页数或提交要求（可选）" /></label>
       <small>{{ selectedCompetition?.event_note || (selectedCompetition?.rules_status === 'format_verified' && competitionYear === selectedCompetition.year ? '已核对主要版式，赛区与提交要求仍需复核。' : '具体版式请结合当届规则核对。') }}</small>
       <a v-for="source in selectedCompetition?.sources" :key="source.url" :href="source.url" target="_blank" rel="noopener">查看赛事资料 ↗</a>
      </div>
     </FloatingPanel>
     <div v-if="error" role="alert" class="send-error">{{ error }}</div>
     <FloatingPanel v-model:open="adjustmentOpen" :anchor="adjustmentTrigger" title="调整任务" :width="320"><div class="adjustment-choices"><button @click="send(draft, undefined, 'immediate')"><strong>立即调整</strong><small>停止当前步骤，按新要求继续</small></button><button @click="send(draft, undefined, 'after_step')"><strong>当前步骤完成后调整</strong><small>保留当前执行，将要求加入后续步骤</small></button></div></FloatingPanel>
     <div v-if="!task_id" class="composer-context"><span class="context-label"><FolderOpen :size="16" />新建项目</span><div class="context-options"><ComposerSelect v-model="competitionId" label="选择数学建模竞赛" title="数学建模竞赛" :options="competitions.map(contest => ({ value: contest.id, label: contest.name }))" /><ComposerSelect :model-value="executionBackend" @update:model-value="executionBackend = $event as ExecutionBackend" label="计算环境" title="计算环境" :options="[{ value: 'python', label: 'Python', description: '使用 Python 运行计算与绘图' }, { value: 'matlab', label: 'MATLAB', description: '使用本机 MATLAB，需要已安装' }]" /><button ref="contestTrigger" class="icon-button" aria-label="赛事设置" :aria-expanded="contestOptionsOpen" @click="contestOptionsOpen = !contestOptionsOpen"><Settings2 :size="15" /></button></div></div>
     <div class="composer" @dragover="acceptFileDrag" @drop="dropAttachments"><textarea ref="composerInput" v-model="draft" rows="1" :aria-label="task_id ? '给团队发送指令' : '描述建模问题'" :placeholder="view === 'paper' && editPaper ? '描述修改要求，可先在源码中选中一段…' : task_id ? '继续对话，或提出修改…' : problemText ? '补充要求（可选）' : '描述问题，或添加赛题与数据…'" @keydown="handleKey" />
      <div class="composer-toolbar"><div class="composer-options">
       <div class="attachment-control"><button ref="attachmentTrigger" class="icon-button" aria-label="添加赛题或数据" :disabled="sending || state?.archived" :aria-expanded="attachmentMenu" @click="attachmentMenu = !attachmentMenu"><Plus :size="19" /></button><div v-if="attachmentMenu" class="attachment-menu"><button @click="uploadsOpen = true; attachmentMenu = false"><FileText :size="15" />赛题文档 <small>PDF / Word</small></button><button @click="fileInput?.click(); attachmentMenu = false"><Paperclip :size="15" />数据附件 <small>不限格式 · 多选</small></button><button @click="folderInput?.click(); attachmentMenu = false"><FolderOpen :size="15" />数据文件夹 <small>包含子文件夹</small></button></div></div>
       <span v-if="!task_id" class="composer-attachment-hint">添加赛题或数据</span>
       <ComposerSelect v-if="task_id && view !== 'paper'" v-model="target" label="指派角色" title="指派角色" :options="[{value:'all',label:labels.coordinator},...['modeler','coder','writer'].map(value=>({value,label:'@ '+labels[value]}))]" /><label v-if="task_id && view === 'paper'" class="edit-mode"><input v-model="editPaper" type="checkbox" />修改源码</label>
      </div><div class="send-actions"><button v-if="state?.status === 'running' && view !== 'paper'" ref="adjustmentTrigger" class="text-link" :disabled="!draft.trim() || sending || state?.archived" @click="adjustmentOpen = !adjustmentOpen">调整任务</button><button v-if="state?.status === 'running'" class="icon-button" aria-label="停止建模" @click="send('停止建模','stop')"><Square :size="15" /></button><button class="send-button" :disabled="!canSend || state?.archived" :aria-label="'发送'" @click="send(draft, view === 'paper' && editPaper ? 'edit_paper' : undefined)"><LoaderCircle v-if="sending" :size="17" class="spin" /><ArrowUp v-else :size="19" /></button></div></div>
     </div>
     <p class="composer-hint">{{ !task_id ? 'Enter 发送 · Shift + Enter 换行' : view === 'paper' && editPaper ? '修改建议经你接受后才写入源码' : state?.status === 'running' ? 'Enter 对话 · 调整执行请点“调整任务”' : 'Enter 发送 · Shift + Enter 换行' }}</p>
    </div>

   </section>
   <FloatingPanel v-if="task_id" v-model:open="boardOpen" :anchor="boardTrigger" side="bottom" title="项目进度" :width="390"><aside class="shared-board floating-board" aria-label="共享全局状态表"><div class="role-grid"><div v-for="role in ['coordinator','modeler','coder','writer']" :key="role"><span class="role-name"><RoleAvatar :role="role" compact />{{ labels[role] }}</span><small>{{ statusLabel(roleStatus(role)) }}</small></div></div><ol class="step-list"><li v-for="step in state?.steps" :key="step.id"><Check v-if="step.status === 'completed'" :size="14" /><span v-else class="step-dot" /><div>{{ step.label }}<small>{{ statusLabel(step.status) }}</small></div></li></ol><button v-if="state?.status === 'completed'" class="primary-button" @click="send('用已验收成果生成论文初稿','write')">开始论文写作</button><button v-if="['stopped','failed'].includes(state?.status || '') && !['EXECUTION_BUDGET','MODEL_STAGE_BUDGET'].includes(state?.failure?.code || '')" class="primary-button" @click="send('继续建模','resume')">继续建模</button><div v-for="item in state?.directives" :key="item.id" class="directive"><p>{{ item.content }}</p><small>{{ item.seen.map(role => labels[role]).join('、') || '等待角色读取' }}</small></div></aside></FloatingPanel>
  </div>
 </main>
 <input ref="fileInput" type="file" multiple hidden aria-label="选择数据附件" @change="addAttachments" /><input ref="folderInput" type="file" webkitdirectory multiple hidden aria-label="选择数据文件夹" @change="addAttachments" /><ApiDialog v-model:open="settingsOpen" />
 <AlertDialogRoot :open="!!deleteTarget" @update:open="closeDelete">
  <AlertDialogOverlay class="delete-overlay" />
  <AlertDialogContent class="delete-dialog" @escape-key-down="deleting && $event.preventDefault()">
   <AlertDialogTitle class="delete-title">删除“{{ deleteTarget?.title }}”？</AlertDialogTitle>
   <AlertDialogDescription class="delete-description">这将永久删除此项目的全部对话、上传附件、代码、计算结果和论文文件，无法恢复。</AlertDialogDescription>
   <p v-if="deleteError" role="alert" class="danger-text">{{ deleteError }}</p>
   <footer><AlertDialogCancel :disabled="deleting">取消</AlertDialogCancel><button class="delete-confirm" :disabled="deleting" @click="confirmDelete">{{ deleting ? '正在删除…' : '永久删除' }}</button></footer>
  </AlertDialogContent>
 </AlertDialogRoot>
 <div v-if="renameId" class="modal-backdrop" @click.self="renameId = ''"><form class="rename-dialog" role="dialog" aria-modal="true" aria-label="重命名项目" @submit.prevent="renameProject"><h2>重命名项目</h2><input v-model="renameText" aria-label="项目名称" maxlength="100" autofocus /><footer><button type="button" @click="renameId = ''">取消</button><button class="primary-button" type="submit" :disabled="!renameText.trim()">保存</button></footer></form></div>
</div>
</template>
<style scoped src="./workspace.css"></style>

<style scoped>
.markdown-body :deep(p) { margin:0 0 12px; }
.markdown-body :deep(p:last-child) { margin-bottom:0; }
.markdown-body :deep(ul), .markdown-body :deep(ol) { padding-left:24px;margin:12px 0; }
.markdown-body :deep(ul) { list-style:disc; }
.markdown-body :deep(ol) { list-style:decimal; }
.markdown-body :deep(li) { margin:5px 0; }
.markdown-body :deep(h1), .markdown-body :deep(h2), .markdown-body :deep(h3) { font-size:16px;font-weight:600;margin:20px 0 10px; }
.markdown-body :deep(pre) { padding:14px 16px;background:#f7f7f7;border:1px solid #eaeaea;border-radius:8px;overflow:auto;font:12px/1.7 Consolas,monospace;margin:14px 0; }
.markdown-body :deep(code) { font-size:.9em; }
.markdown-body :deep(a) { text-decoration:underline;text-underline-offset:3px; }
.markdown-body :deep(table) { display:block;overflow:auto;border-collapse:collapse;max-width:100%;margin:14px 0; }
.markdown-body :deep(th), .markdown-body :deep(td) { padding:8px 12px;border:1px solid #e5e5e5;text-align:left; }
</style>
