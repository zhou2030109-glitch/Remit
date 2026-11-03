<script setup lang="ts">
import ComposerSelect from "@/pages/team/ComposerSelect.vue";
import FloatingPanel from "@/pages/team/FloatingPanel.vue";
import OverlayDetails from "@/pages/team/OverlayDetails.vue";
import {
	type PaperProposal,
	getPaperProposals,
	decidePaperProposal,
} from "@/apis/teamApi";
import PaperProposalReview from "./PaperProposalReview.vue";
import {
	type PaperWorkspace,
	type PaperMode,
	cancelPaper,
	compilePaper,
	generatePaper,
	getPaperHistory,
	getPaperSource,
	getPaperWorkspace,
	savePaperSource,
	setPaperMain,
	setPaperMode,
	syncPaper,
	writingUrl,
} from "@/apis/writingApi";
import { isAxiosError } from "axios";
import {
	ArrowLeft,
	Check,
	ChevronDown,
	Code2,
	Download,
	FilePlus2,
	FileText,
	FolderOpen,
	History,
	LoaderCircle,
	PanelLeftClose,
	Play,
	RefreshCw,
	Search,
	Square,
	WandSparkles,
	X,
} from "lucide-vue-next";
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { RouterLink, onBeforeRouteLeave } from "vue-router";
import ContestReview from "./ContestReview.vue";
import ChapterPlan from "./ChapterPlan.vue";
import ReviseChapters from "./ReviseChapters.vue";

const props = defineProps<{ task_id: string; embedded?: boolean }>();
const project = ref<PaperWorkspace | null>(null);
const revisionProposal = ref<PaperProposal | null>(null);
const decidingRevision = ref(false);
async function reviewRevision(accept: boolean) {
	const proposal = revisionProposal.value;
	if (!proposal || decidingRevision.value) return;
	decidingRevision.value = true;
	try {
		if (accept && !(await save())) return;
		const result = (
			await decidePaperProposal(props.task_id, proposal.id, accept)
		).data;
		revisionProposal.value = null;
		if (accept) await reloadAccepted();
		else await refresh();
		if (result.compile === "failed")
			error.value = "修改已保存，编译未通过，请查看日志。";
	} catch (cause) {
		error.value = message(cause);
	} finally {
		decidingRevision.value = false;
	}
}
const selected = ref("");
const content = ref("");
const savedContent = ref("");
const version = ref<string | null>(null);
const loading = ref(true);
const saving = ref(false);
const compiling = ref(false);
const stopRequested = ref(false);
const stopping = computed(
	() =>
		stopRequested.value ||
		project.value?.compile.status === "stopping" ||
		project.value?.generation.status === "stopping",
);
const autoCompile = ref(false);
const sourceOpen = ref(false);
const error = ref("");
const logsOpen = ref(false);
const filesOpen = ref(false);
const historyOpen = ref(false);
const histories = ref<{ content: string; saved_at: string }[]>([]);
const newFileOpen = ref(false);
const newFileName = ref("");
const searchOpen = ref(false);
const searchText = ref("");
const sourceInput = ref<HTMLTextAreaElement | null>(null);
const highlightLayer = ref<HTMLPreElement | null>(null);
const numbersLayer = ref<HTMLDivElement | null>(null);
const splitArea = ref<HTMLDivElement | null>(null);
const split = ref(50);
const zoom = ref(100);
const pdfRenderScale = computed(() => (zoom.value >= 125 ? 4 : 2.5));
const cursor = ref({ line: 1, column: 1 });
const savedAt = ref("");
const busy = ref(false);
let saveTimer: ReturnType<typeof setTimeout> | undefined;
let compileTimer: ReturnType<typeof setTimeout> | undefined;
let pollTimer: ReturnType<typeof setTimeout> | undefined;
let pendingSave: Promise<boolean> | null = null;
let disposed = false;
let refreshSequence = 0;
let compileEpoch = 0;
let loadingFile = false;
const dirty = computed(() => content.value !== savedContent.value);
async function changeMode(mode: PaperMode) {
	if (!project.value || busy.value) return;
	busy.value = true;
	try {
		await setPaperMode(
			props.task_id,
			mode,
			project.value.mode_version || "legacy",
		);
		await refresh();
	} catch (cause) {
		error.value = message(cause);
	} finally {
		busy.value = false;
	}
}
const generating = computed(() =>
	["running", "stopping"].includes(project.value?.generation.status || ""),
);
const writingSection = computed(() => {
	const section = project.value?.generation.section || "";
	const names: Record<string, string> = {
		eda: "数据分析",
		sensitivity_analysis: "敏感性分析",
		firstPage: "摘要",
		RepeatQues: "问题重述",
		analysisQues: "问题分析",
		modelAssumption: "模型假设",
		symbol: "符号说明",
		judge: "模型评价",
	};
	return (
		names[section] ||
		(/^ques\d+$/.test(section) ? `问题 ${section.slice(4)}` : "整理草稿")
	);
});
const pdfUrl = computed(() =>
	project.value?.pdf_available
		? `${writingUrl(props.task_id, "/pdf")}?v=${project.value.compile.pdf_revision || ""}#view=FitH`
		: "",
);
const stale = computed(() =>
	Boolean(
		project.value?.pdf_available &&
			(dirty.value ||
				project.value.compile.pdf_revision !== project.value.revision),
	),
);
const lineNumbers = computed(() =>
	Array.from(
		{ length: content.value.split("\n").length },
		(_, i) => i + 1,
	).join("\n"),
);
const outline = computed(() =>
	content.value.split("\n").flatMap((line, index) =>
		[...line.matchAll(/\\(?:sub)*section\*?\{([^}]+)\}/g)].map((match) => ({
			title: match[1],
			line: index + 1,
		})),
	),
);
const plainSource = computed(() => content.value.length > 30000);
const escaped = (text: string) =>
	text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const highlighted = computed(() =>
	!sourceOpen.value || plainSource.value
		? ""
		: `${content.value
				.split("\n")
				.map((line) => {
					const tokens = line.split(/(%.*$|\\[a-zA-Z]+\*?|[{}$])/g);
					return tokens
						.map((token) =>
							token.startsWith("%")
								? `<span class="tex-comment">${escaped(token)}</span>`
								: token.startsWith("\\")
									? `<span class="tex-command">${escaped(token)}</span>`
									: /^[{}$]$/.test(token)
										? `<span class="tex-symbol">${token}</span>`
										: escaped(token),
						)
						.join("");
				})
				.join("\n")}\n`,
);

function message(cause: unknown): string {
	return isAxiosError(cause) && typeof cause.response?.data?.detail === "string"
		? cause.response.data.detail
		: cause instanceof Error
			? cause.message
			: "操作失败，请重试";
}
async function refresh() {
	const requestSequence = ++refreshSequence;
	const taskId = props.task_id;
	const previousMain = project.value?.main;
	const previousRevision = project.value?.revision;
	const response = (await getPaperWorkspace(taskId)).data;
	if (
		disposed ||
		requestSequence !== refreshSequence ||
		taskId !== props.task_id
	)
		return;
	project.value = response;
	const proposalId = response.generation.proposal_id;
	if (
		response.generation.status === "awaiting_review" &&
		proposalId &&
		revisionProposal.value?.id !== proposalId
	) {
		const proposals = (await getPaperProposals(taskId)).data;
		if (
			disposed ||
			requestSequence !== refreshSequence ||
			taskId !== props.task_id
		)
			return;
		if (
			!disposed &&
			requestSequence === refreshSequence &&
			taskId === props.task_id
		)
			revisionProposal.value =
				proposals.find((item) => item.id === proposalId) || null;
	} else if (response.generation.status !== "awaiting_review")
		revisionProposal.value = null;
	if (["stopping", "cancelled"].includes(response.compile.status || ""))
		autoCompile.value = false;
	if (
		!response.compiling &&
		!["running", "stopping"].includes(response.generation.status)
	)
		stopRequested.value = false;
	if (!selected.value && !dirty.value && !saving.value) {
		await loadFile(project.value.main);
	} else if (
		previousMain &&
		project.value.main !== previousMain &&
		selected.value === previousMain &&
		!dirty.value &&
		!saving.value
	) {
		await loadFile(project.value.main);
	} else if (
		previousRevision &&
		previousRevision !== project.value.revision &&
		selected.value === project.value.generation.file &&
		!dirty.value &&
		!saving.value
	) {
		await loadFile(selected.value, true);
	}
}
async function loadFile(name: string, force = false) {
	if (loadingFile || disposed) return;
	if (name === selected.value && !force) return;
	if (!(await save())) return;
	loadingFile = true;
	const snapshot = content.value;
	busy.value = true;
	try {
		const response = await getPaperSource(props.task_id, name);
		if (disposed) return;
		if (content.value !== snapshot) {
			error.value =
				"读取期间你继续编辑了正文，已保留本地内容，请保存后再切换。";
			return;
		}
		selected.value = name;
		version.value = response.data.version;
		savedContent.value = response.data.content;
		content.value = response.data.content;
		error.value = "";
		await nextTick();
		if (sourceInput.value) {
			sourceInput.value.scrollTop = 0;
			sourceInput.value.scrollLeft = 0;
		}
		scrollEditor();
	} catch (cause) {
		error.value = message(cause);
	} finally {
		loadingFile = false;
		busy.value = false;
	}
}
async function save(): Promise<boolean> {
	clearTimeout(saveTimer);
	if (pendingSave) return pendingSave;
	if (!dirty.value || !selected.value) return true;
	saving.value = true;
	pendingSave = (async () => {
		try {
			while (dirty.value) {
				const snapshot = content.value;
				const response = await savePaperSource(
					props.task_id,
					selected.value,
					snapshot,
					version.value,
				);
				version.value = response.data.version;
				savedContent.value = snapshot;
				if (project.value) project.value.revision = response.data.revision;
			}
			savedAt.value = new Date().toLocaleTimeString();
			error.value = "";
			return true;
		} catch (cause) {
			error.value = message(cause);
			return false;
		} finally {
			saving.value = false;
			pendingSave = null;
		}
	})();
	return pendingSave;
}

async function compile() {
	clearTimeout(compileTimer);
	if (disposed || compiling.value || project.value?.compiling || stopping.value || !selected.value) return;
	compiling.value = true;
	const epoch = compileEpoch;
	let submitted: string | undefined;
	try {
		if (!(await save()) || disposed || epoch !== compileEpoch || project.value?.compiling || stopping.value) return;
		submitted = project.value?.revision;
		const result = (await compilePaper(props.task_id)).data;
		await refresh();
		if (result.status !== "completed") logsOpen.value = true;
	} catch (cause) {
		error.value = message(cause);
	} finally {
		compiling.value = false;
		if (
			!disposed &&
			epoch === compileEpoch &&
			autoCompile.value &&
			submitted &&
			(dirty.value || submitted !== project.value?.revision)
		) {
			compileTimer = setTimeout(() => void compile(), 1500);
		}
	}
}
function edited() {
	clearTimeout(saveTimer);
	clearTimeout(compileTimer);
	saveTimer = setTimeout(async () => {
		if (await save()) {
			if (!disposed && autoCompile.value)
				compileTimer = setTimeout(() => void compile(), 1200);
		}
	}, 700);
	updateCursor();
}
function scrollEditor() {
	if (!sourceInput.value) return;
	if (highlightLayer.value)
		highlightLayer.value.style.transform = `translate(${-sourceInput.value.scrollLeft}px, ${-sourceInput.value.scrollTop}px)`;
	if (numbersLayer.value)
		numbersLayer.value.style.transform = `translateY(${-sourceInput.value.scrollTop}px)`;
}
function updateCursor() {
	const before = content.value
		.slice(0, sourceInput.value?.selectionStart ?? 0)
		.split("\n");
	cursor.value = {
		line: before.length,
		column: (before.at(-1)?.length ?? 0) + 1,
	};
}
async function paperContext() {
	if (!(await save())) throw new Error("请先处理源码保存冲突，再提出修改。");
	// DOM 选区按 UTF-16 计数，服务端按 Unicode 字符计数。
	const start = sourceInput.value?.selectionStart ?? 0;
	const end = sourceInput.value?.selectionEnd ?? 0;
	return {
		name: selected.value,
		version: version.value,
		start: [...content.value.slice(0, start)].length,
		end: [...content.value.slice(0, end)].length,
	};
}
async function reloadAccepted() {
	// 请求期间的手动编辑也必须保留，不能静默覆盖。
	if (dirty.value) {
		error.value = "论文建议已应用；你还有本地编辑，请先处理版本冲突。";
		return;
	}
	const name = selected.value;
	selected.value = "";
	await refresh();
	await loadFile(name);
}
defineExpose({ save, paperContext, reloadAccepted });
function jump(line: number) {
	sourceOpen.value = true;
	const input = sourceInput.value;
	if (!input) return;
	const offset = content.value
		.split("\n")
		.slice(0, line - 1)
		.reduce((sum, text) => sum + text.length + 1, 0);
	input.focus();
	input.setSelectionRange(offset, offset);
	input.scrollTop = Math.max(0, (line - 5) * 23);
	scrollEditor();
	updateCursor();
}
function findNext() {
	if (!searchText.value || !sourceInput.value) return;
	const from = sourceInput.value.selectionEnd;
	let at = content.value.indexOf(searchText.value, from);
	if (at < 0) at = content.value.indexOf(searchText.value);
	if (at < 0) return;
	jump(content.value.slice(0, at).split("\n").length);
	sourceInput.value.setSelectionRange(at, at + searchText.value.length);
}
function keydown(event: KeyboardEvent) {
	if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
		event.preventDefault();
		void save();
	}
	if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
		event.preventDefault();
		void compile();
	}
	if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "f") {
		event.preventDefault();
		searchOpen.value = true;
	}
	if (event.key === "Tab" && event.target === sourceInput.value) {
		event.preventDefault();
		const input = sourceInput.value;
		if (!input) return;
		const start = input.selectionStart;
		content.value = `${content.value.slice(0, start)}  ${content.value.slice(input.selectionEnd)}`;
		void nextTick(() => input.setSelectionRange(start + 2, start + 2));
		edited();
	}
}
async function stopPaper() {
	if (stopping.value) return;
	compileEpoch++;
	autoCompile.value = false;
	clearTimeout(compileTimer);
	stopRequested.value = true;
	try {
		await cancelPaper(props.task_id);
		await refresh();
	} catch (cause) {
		stopRequested.value = false;
		error.value = message(cause);
	}
}
async function action(kind: "sync" | "generate" | "main") {
	busy.value = true;
	try {
		if (!(await save())) return;
		if (kind === "sync") await syncPaper(props.task_id);
		if (kind === "generate") await generatePaper(props.task_id);
		if (kind === "main") await setPaperMain(props.task_id, selected.value);
		await refresh();
		error.value = "";
		if (kind === "main") void compile();
	} catch (cause) {
		error.value = message(cause);
	} finally {
		busy.value = false;
	}
}
async function reviseChapters(request: {
	sections: string[];
	instructions: string;
}) {
	if (
		!project.value?.generation.generation_id ||
		!project.value.inputs.revision ||
		busy.value
	)
		return;
	const generationId = project.value.generation.generation_id;
	const inputRevision = project.value.inputs.revision;
	busy.value = true;
	try {
		if (!(await save())) return;
		await refresh();
		await generatePaper(props.task_id, {
			...request,
			generation_id: generationId,
			input_revision: inputRevision,
			source_revision: project.value.revision,
		});
		await refresh();
		error.value = "";
	} catch (cause) {
		error.value = message(cause);
	} finally {
		busy.value = false;
	}
}
async function createFile() {
	if (!newFileName.value.trim()) return;
	try {
		await savePaperSource(
			props.task_id,
			newFileName.value.trim(),
			"% 新文件\n",
			null,
		);
		await refresh();
		await loadFile(newFileName.value.trim());
		newFileOpen.value = false;
		newFileName.value = "";
	} catch (cause) {
		error.value = message(cause);
	}
}
async function showHistory() {
	try {
		histories.value = (
			await getPaperHistory(props.task_id, selected.value)
		).data;
		historyOpen.value = true;
	} catch (cause) {
		error.value = message(cause);
	}
}
function restoreHistory(text: string) {
	content.value = text;
	historyOpen.value = false;
	edited();
}
function dragSplit(event: PointerEvent) {
	(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
}
function moveSplit(event: PointerEvent) {
	if (!(event.currentTarget as HTMLElement).hasPointerCapture(event.pointerId))
		return;
	const bounds = splitArea.value?.getBoundingClientRect();
	if (bounds)
		split.value = Math.max(
			25,
			Math.min(75, (100 * (event.clientX - bounds.left)) / bounds.width),
		);
}
function preventLoss(event: BeforeUnloadEvent) {
	if (dirty.value) {
		event.preventDefault();
		event.returnValue = "";
	}
}
watch(autoCompile, (enabled) => {
	if (enabled) void compile();
	else clearTimeout(compileTimer);
});
onBeforeRouteLeave(async () => await save());
function scheduleRefresh() {
	if (disposed) return;
	pollTimer = setTimeout(async () => {
		if (disposed) return;
		try {
			await refresh();
		} catch {
			/* 编辑内容留在本地，下次轮询重试。 */
		} finally {
			// Wait for the previous request, so slow services cannot stack polls.
			scheduleRefresh();
		}
	}, 4000);
}
onMounted(async () => {
	window.addEventListener("beforeunload", preventLoss);
	try {
		await refresh();
		if (disposed) return;
		await loadFile(project.value?.main || "main.tex");
	} catch (cause) {
		error.value = message(cause);
	} finally {
		loading.value = false;
	}
	scheduleRefresh();
});
onUnmounted(() => {
	disposed = true;
	clearTimeout(saveTimer);
	clearTimeout(compileTimer);
	clearTimeout(pollTimer);
	window.removeEventListener("beforeunload", preventLoss);
});
</script>

<template>
	<div class="paper-editor" :class="{ embedded }" @keydown="keydown">
		<header class="project-bar">
			<RouterLink v-if="!embedded" to="/home" class="project-back" title="返回项目"><ArrowLeft :size="17" /><strong>Remit</strong></RouterLink>
			<div class="project-title"><span>论文写作</span><ChevronDown :size="13" /><small>{{ task_id.slice(0, 8) }}</small></div>
			<div class="project-actions"><button :aria-pressed="sourceOpen" @click="sourceOpen = !sourceOpen">{{ sourceOpen ? "只看论文" : "编辑源码" }}</button><ContestReview :task_id="task_id" /><button @click="showHistory" :disabled="!selected"><History :size="15" />历史</button><a :href="writingUrl(task_id, '/export')"><Download :size="15" />下载项目</a><RouterLink :to="`/project/${task_id}`">团队对话</RouterLink><RouterLink :to="`/project/${task_id}/results`">建模结果</RouterLink></div>
		</header>
		<div v-if="error" class="error-banner" role="alert"><span>{{ error }}</span><button @click="error = ''" aria-label="关闭错误"><X :size="15" /></button></div>
		<ChapterPlan :task-id="task_id" />
		<ReviseChapters v-if="project?.generation.generation_id && project.generation.completed_sections?.length" :key="task_id" :sections="project.generation.completed_sections" :disabled="busy || generating || compiling || !!project.compiling || !project.ready || project.generation.status === 'awaiting_review'" @revise="reviseChapters" />
		<div class="writing-mode"><label>新草稿模式 <ComposerSelect label="新草稿模式" title="新草稿模式" :model-value="project?.mode || 'full_paper'" :disabled="busy || generating || compiling || project?.compiling" @update:model-value="changeMode($event as PaperMode)" :options="[{value:'full_paper',label:'完整论文'},{value:'short_report',label:'短报告'}]" /></label><span>{{ project?.mode === 'short_report' ? '精简篇幅，保留计算证据与局限。' : '按完整章节写作，提交前仍需核验。' }} 模式用于下一份草稿，已有文稿保留。</span><small v-if="project?.pdf_available">当前 PDF：{{ project.compile.pdf_mode === 'short_report' ? '短报告' : '完整论文模式' }}</small></div>
		<PaperProposalReview v-if="revisionProposal" :proposal="revisionProposal" :disabled="decidingRevision || busy || generating || compiling" :processing="decidingRevision" @decide="reviewRevision" />
		<div class="evidence-bar"><div><span class="sync-dot" :class="{ ready: project?.ready }" />{{ project?.ready ? '建模成果已就绪' : '等待建模与计算完成' }}<span class="evidence-count">{{ project?.inputs.sections?.length || 0 }} 个章节 · {{ project?.inputs.asset_count || 0 }} 项素材</span></div><div><button :disabled="busy || !project?.ready" @click="action('sync')"><RefreshCw :size="13" />同步建模素材</button><button v-if="generating" :disabled="stopping" @click="stopPaper"><Square :size="12" />{{ stopping ? "正在停止…" : "停止写作" }}</button><button v-else :disabled="busy || !project?.ready || project.generation.status === 'awaiting_review'" class="generate-button" @click="action('generate')"><WandSparkles :size="14" />{{ project?.mode === "short_report" ? "生成短报告" : "生成论文初稿" }}</button></div></div>
		<div v-if="generating || project?.generation.status === 'failed' || project?.generation.status === 'completed' || project?.generation.status === 'interrupted' || project?.generation.status === 'awaiting_review'" class="generation-bar" role="status"><LoaderCircle v-if="generating" :size="14" class="spin" /><span v-if="generating">墨墨正在撰写{{ writingSection }}。<template v-if="project?.generation.partial">已写 {{ project.generation.completed_sections?.length || 0 }} 个章节，当前为部分草稿，会继续更新。</template><template v-else>首个章节通过校验后会更新正文预览。</template></span><span v-else-if="project?.generation.error">上次自动写作记录：{{ project.generation.error }}（当前文稿的编译结果见 PDF 面板）</span><template v-else><span>{{ project?.generation.message || "初稿已生成，请核对正文与证据。" }}</span><button v-if="project?.generation.file" @click="loadFile(project.generation.file)">打开 {{ project.generation.file }}</button></template></div>
		<div v-if="loading" class="loading-state"><LoaderCircle class="spin" />正在打开论文项目…</div>
		<div v-else class="editor-layout">
			<FloatingPanel v-model:open="filesOpen" title="论文文件" :width="320"><aside class="file-panel floating-file-panel">
				<div class="panel-toolbar"><span>文件</span><div><button @click="newFileOpen = !newFileOpen" title="新建文件" aria-label="新建文件"><FilePlus2 :size="15" /></button><button @click="filesOpen = false" title="隐藏文件树" aria-label="隐藏文件树"><PanelLeftClose :size="15" /></button></div></div>
				<FloatingPanel v-model:open="newFileOpen" title="新建文件" :width="340"><form class="new-file" @submit.prevent="createFile"><input v-model="newFileName" placeholder="sections/model.tex" aria-label="新文件名" /><button type="submit">创建</button></form></FloatingPanel>
				<nav class="file-tree" aria-label="论文文件"><button v-for="file in project?.files" :key="file.name" :class="{ selected: selected === file.name }" :disabled="!file.editable || busy" @click="loadFile(file.name)"><FileText :size="14" /><span :title="file.name">{{ file.name }}</span><small v-if="file.name === project?.main">主</small></button></nav>
				<div class="outline-heading">文档大纲</div><nav class="document-outline" aria-label="文档大纲"><button v-for="section in outline" :key="section.line" @click="jump(section.line)">{{ section.title }}</button><span v-if="!outline.length">章节标题会显示在这里</span></nav>
				<footer><span class="sync-dot ready" />独立保存 · 本地项目</footer>
			</aside></FloatingPanel>
			<div ref="splitArea" class="split-area">
				<section v-show="sourceOpen" class="source-panel" :style="{ width: `${split}%` }" aria-label="LaTeX 源码编辑器">
					<div class="panel-toolbar source-toolbar"><div><button v-if="!filesOpen" @click="filesOpen = true" aria-label="显示文件树"><FolderOpen :size="16" /></button><span class="source-tab"><Code2 :size="14" />源码</span><span class="filename" :title="selected">{{ selected }}</span></div><div><button v-if="selected !== project?.main && selected.endsWith('.tex')" @click="action('main')" :disabled="busy" title="编译时使用这个文件">设为主文件</button><button @click="searchOpen = !searchOpen" aria-label="搜索源码"><Search :size="15" /></button></div></div>
					<FloatingPanel v-model:open="searchOpen" title="搜索源码" :width="350"><form class="search-bar" @submit.prevent="findNext"><input v-model="searchText" placeholder="查找…" aria-label="查找源码" /><button type="submit">下一个</button><button type="button" @click="searchOpen = false" aria-label="关闭搜索"><X :size="14" /></button></form></FloatingPanel>
					<div class="code-area"><div class="line-gutter" aria-hidden="true"><pre ref="numbersLayer">{{ lineNumbers }}</pre></div><div class="code-content"><pre ref="highlightLayer" class="code-highlight" aria-hidden="true" v-html="highlighted" /><textarea ref="sourceInput" v-model="content" class="code-input" :class="{ plain: plainSource }" aria-label="LaTeX 源码" spellcheck="false" wrap="off" autocomplete="off" autocapitalize="off" :disabled="busy || !selected" @input="edited" @scroll="scrollEditor" @click="updateCursor" @keyup="updateCursor" /></div></div>
					<footer class="editor-status"><span><LoaderCircle v-if="saving" :size="12" class="spin" /><Check v-else-if="!dirty" :size="12" />{{ saving ? '正在保存' : dirty ? '有未保存修改' : '所有修改已保存' }}</span><span :title="savedAt">Ln {{ cursor.line }}, Col {{ cursor.column }} · UTF-8</span></footer>
				</section>
				<div v-show="sourceOpen" class="splitter" role="separator" aria-label="调整源码和 PDF 宽度" aria-orientation="vertical" :aria-valuenow="Math.round(split)" aria-valuemin="25" aria-valuemax="75" tabindex="0" @pointerdown="dragSplit" @pointermove="moveSplit" @keydown.left.prevent="split = Math.max(25, split - 2)" @keydown.right.prevent="split = Math.min(75, split + 2)"><span /></div>
				<section class="pdf-panel" aria-label="PDF 预览">
					<div class="panel-toolbar pdf-toolbar"><div class="compile-group"><button class="compile-button" :disabled="compiling || project?.compiling || stopping || !selected" @click="compile"><LoaderCircle v-if="compiling || project?.compiling" :size="14" class="spin" /><Play v-else :size="13" fill="currentColor" />{{ compiling || project?.compiling ? '正在编译…' : '重新编译' }}</button><button v-if="compiling || project?.compiling" :disabled="stopping" aria-label="停止论文编译" @click="stopPaper"><Square :size="13" />{{ stopping ? "正在停止…" : "停止编译" }}</button><label class="auto-compile"><input v-model="autoCompile" type="checkbox" />自动</label></div><div><button @click="logsOpen = !logsOpen" :class="{ 'has-errors': project?.compile.status === 'failed' }" title="日志与错误">日志<span v-if="project?.compile.diagnostics?.length" class="error-count">{{ project.compile.diagnostics.length }}</span></button><a v-if="pdfUrl" :href="writingUrl(task_id, '/pdf')" target="_blank" rel="noopener" title="打开或下载 PDF" aria-label="打开 PDF"><Download :size="16" /></a></div></div>
					<div v-if="stale" class="pdf-stale">源码已更新 · 当前显示上次成功编译的 PDF</div>
					<OverlayDetails v-if="project?.compile.layout_review?.issues?.length" class="pdf-stale" title="排版待修订事项"><template #trigger>排版仍需修订（{{ project.compile.layout_review.issues.length }} 项）</template><ul><li v-for="issue in project.compile.layout_review.issues" :key="issue">{{ issue }}</li></ul></OverlayDetails><div v-if="pdfUrl && project?.compile.page_count" class="pdf-view-controls"><span>{{ project.compile.page_count }} 页</span><label>缩放 <ComposerSelect label="PDF 缩放" title="PDF 缩放" :model-value="String(zoom)" @update:model-value="zoom = Number($event)" :options="[{value:'75',label:'75%'},{value:'100',label:'适合宽度'},{value:'125',label:'125%'},{value:'150',label:'150%'}]" /></label></div><div class="pdf-surface"><div v-if="pdfUrl && project?.compile.page_count" class="pdf-pages" aria-label="论文 PDF 页面"><figure v-for="page in project.compile.page_count" :key="`${project.compile.pdf_revision}-${page}`" :style="{ width: `${zoom}%` }"><img :src="writingUrl(task_id, `/pdf/pages/${page - 1}?revision=${project.compile.pdf_revision}&scale=${pdfRenderScale}`)" :alt="`论文 PDF 第 ${page} 页`" loading="lazy" /><figcaption>{{ page }} / {{ project.compile.page_count }}</figcaption></figure></div><div v-else class="pdf-empty"><FileText :size="44" /><h2>{{ compiling ? '正在排版你的论文' : 'PDF 预览' }}</h2><p>{{ compiling ? 'XeLaTeX 编译完成后，PDF 会显示在这里。' : '点击重新编译，查看左侧源码的排版效果。' }}</p><button v-if="project?.compile.status === 'failed'" @click="logsOpen = true">查看编译错误</button></div></div>
					<FloatingPanel v-model:open="logsOpen" title="编译日志" :width="620"><div class="compile-logs floating-compile-logs"><header><strong>{{ project?.compile.status === 'completed' ? '编译成功' : '编译日志' }}</strong><button @click="logsOpen = false" aria-label="关闭编译日志"><X :size="14" /></button></header><div class="diagnostics"><button v-for="(diagnostic, index) in project?.compile.diagnostics" :key="index" @click="jump(diagnostic.line)">第 {{ diagnostic.line }} 行：{{ diagnostic.message }}</button></div><pre>{{ project?.compile.log || '尚无编译日志。' }}</pre></div></FloatingPanel>
					<footer class="pdf-status"><span>XeLaTeX · {{ project?.main }}</span><span>{{ project?.compile.status === 'completed' ? '编译成功' : project?.compile.status === 'failed' ? '编译失败 · 查看日志' : project?.compile.status === 'cancelled' ? '已停止 · 保留上次 PDF' : stopping ? '正在停止…' : '等待编译' }}</span></footer>
				</section>
			</div>
		</div>
		<div v-if="historyOpen" class="modal-backdrop" @click.self="historyOpen = false"><section class="history-dialog" role="dialog" aria-modal="true" aria-label="源码历史"><header><div><h2>版本历史</h2><p>{{ selected }}</p></div><button @click="historyOpen = false" aria-label="关闭历史"><X :size="18" /></button></header><p v-if="!histories.length">此文件还没有历史版本。</p><article v-for="entry in histories" :key="entry.saved_at"><div><time>{{ new Date(entry.saved_at).toLocaleString() }}</time><button @click="restoreHistory(entry.content)">恢复此版本</button></div><pre>{{ entry.content.slice(0, 250) }}</pre></article></section></div>
	</div>
</template>

<style scoped>
.writing-mode { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; padding: 8px 16px; font-size: 12px; border-bottom: 1px solid var(--border); }
.writing-mode select { border: 1px solid var(--border); border-radius: 6px; padding: 4px 8px; background: var(--background); color: inherit; }
.writing-mode span, .writing-mode small { color: var(--muted-foreground); }
.code-input.plain{color:#334155;-webkit-text-fill-color:#334155}.line-gutter pre{font:inherit;line-height:23px;margin:0}.pdf-panel{min-width:0}

.paper-editor.embedded { height:100%; min-height:0; }
.embedded .project-bar { height:35px; background:#fafafa; color:#333; border-bottom:1px solid #ddd; }
.embedded .project-title,.embedded .project-actions>a:nth-last-child(-n+2) { display:none; }
.embedded .project-actions { width:100%; justify-content:flex-end; }
.embedded .project-actions>button:hover,.embedded .project-actions>a:hover { background:#eee; }
.embedded .file-panel { width:160px; background:#fafafa; }
.embedded .pdf-toolbar .compile-button,.embedded .generate-button { background:#deff40; color:#202020; border-color:#d4e88a; }
.embedded .source-tab { border-top-color:#deff40; }
.embedded .file-tree button.selected { background:#ebebeb; color:#222; }
.embedded .auto-compile input { accent-color:#333; }

.paper-editor{height:100dvh;display:flex;flex-direction:column;background:#fff;color:#343b43;font-family:Inter,"Microsoft YaHei",sans-serif;overflow:hidden;font-size:12px}.paper-editor button,.paper-editor a{transition:background-color .12s}.paper-editor button:disabled{opacity:.45;cursor:not-allowed}.paper-editor button:focus-visible,.paper-editor a:focus-visible,.splitter:focus-visible{outline:2px solid #7daf56;outline-offset:-2px}.project-bar{height:48px;flex-shrink:0;background:#293138;color:#eef0f2;display:flex;align-items:center;justify-content:space-between;padding:0 17px;gap:22px}.project-back{display:flex;align-items:center;gap:12px}.project-back strong{font-size:19px;letter-spacing:-.6px}.project-title{display:flex;align-items:center;gap:10px;flex:1}.project-title small{color:#a0abb4;border-left:1px solid #56606a;padding-left:14px}.project-actions{display:flex;gap:5px;align-items:center}.project-actions>a,.project-actions>button{display:flex;align-items:center;gap:6px;padding:8px 11px;border-radius:4px;font-size:11px}.project-actions>a:hover,.project-actions>button:hover{background:#3c454d}.evidence-bar{min-height:43px;flex-shrink:0;border-bottom:1px solid #dce0e3;background:#f8faf7;padding:7px 16px;display:flex;align-items:center;justify-content:space-between;gap:12px;font-size:11px}.evidence-bar>div{display:flex;align-items:center;gap:12px}.evidence-count{color:#869084}.sync-dot{display:inline-block;flex-shrink:0;width:6px;height:6px;border-radius:50%;background:#b6bec4}.sync-dot.ready{background:#6b9950}.evidence-bar button{display:flex;align-items:center;gap:6px;padding:5px 9px;border-radius:4px}.generate-button{border:1px solid #c9d7bf;color:#386222;background:#f0f6e9}.error-banner{display:flex;justify-content:space-between;gap:14px;background:#fff0f0;border-bottom:1px solid #ebc8c8;color:#a23131;padding:10px 18px}.generation-bar{display:flex;align-items:center;gap:10px;background:#f1f5fc;border-bottom:1px solid #d3e0f3;color:#426080;padding:9px 18px;font-size:11px}.generation-bar button{text-decoration:underline}.loading-state{flex:1;display:flex;gap:12px;align-items:center;justify-content:center}.editor-layout{display:flex;flex:1;min-height:0;min-width:0}.file-panel{width:218px;flex-shrink:0;border-right:1px solid #c8cdd2;background:#f4f5f6;display:flex;flex-direction:column;min-height:0}.panel-toolbar{height:40px;flex-shrink:0;padding:0 10px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #d7dbdf;background:#f7f8f9;gap:7px}.panel-toolbar>div{display:flex;align-items:center;gap:4px;min-width:0}.panel-toolbar button,.panel-toolbar a{display:inline-flex;align-items:center;justify-content:center;gap:5px;padding:6px;border-radius:3px;white-space:nowrap}.panel-toolbar button:hover{background:#e7e9eb}.file-tree{overflow:auto;min-height:110px;flex:1;padding:10px 0}.file-tree button{display:flex;align-items:center;gap:8px;text-align:left;width:100%;padding:8px 13px;color:#505a64;font-size:11px}.file-tree button>svg{flex-shrink:0}.file-tree button>span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.file-tree button>small{margin-left:auto;color:#748263;font-size:9px}.file-tree button.selected{background:#dbe7d2;color:#263e1c}.file-tree button:not(:disabled):hover{background:#e5e9e1}.outline-heading{padding:12px 13px;border-top:1px solid #d6dce0;font-size:11px;font-weight:600}.document-outline{max-height:32%;min-height:120px;overflow:auto;padding:0 8px 15px;display:flex;flex-direction:column;gap:2px}.document-outline button{text-align:left;padding:7px 8px;color:#596773;font-size:11px;border-radius:4px}.document-outline button:hover{background:#e7eaec}.document-outline>span{color:#91989e;padding:10px;font-size:11px}.file-panel>footer{padding:12px;color:#7b858b;border-top:1px solid #d6dce0;font-size:10px;display:flex;align-items:center;gap:7px}.split-area{display:flex;flex:1;min-width:0;min-height:0}.source-panel{display:flex;flex-direction:column;min-width:0;min-height:0;background:#fff}.source-toolbar{padding-left:0}.source-tab{height:40px;border-top:2px solid #4e7c33;border-right:1px solid #d7dbdf;display:flex;align-items:center;gap:6px;padding:0 13px;background:white;font-size:11px}.filename{font-size:10px;color:#7a858e;padding:0 7px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.code-area{display:flex;flex:1;overflow:hidden;min-height:0;position:relative}.line-gutter{width:46px;flex-shrink:0;overflow:hidden;background:#f9fafb;border-right:1px solid #eff1f3;text-align:right;color:#a2a8ae;font:12px/23px Consolas,monospace;padding:14px 10px 0 0;user-select:none}.code-content{flex:1;min-width:0;position:relative;overflow:hidden}.code-input,.code-highlight{font:13px/23px Consolas,"Cascadia Code","Microsoft YaHei",monospace;letter-spacing:0;tab-size:2;padding:14px 20px;margin:0;border:0;white-space:pre;box-sizing:border-box}.code-highlight{position:absolute;top:0;left:0;color:#303c48;min-width:100%;pointer-events:none}.code-input{position:absolute;inset:0;width:100%;height:100%;color:transparent;background:transparent;caret-color:#273b49;resize:none;outline:none;overflow:auto}.code-input::selection{background:#a6c4e56b;color:transparent}.code-highlight :deep(.tex-command){color:#246ab6}.code-highlight :deep(.tex-symbol){color:#a66632}.code-highlight :deep(.tex-comment){color:#739166;font-style:italic}.editor-status,.pdf-status{height:26px;flex-shrink:0;display:flex;align-items:center;justify-content:space-between;gap:10px;padding:0 10px;border-top:1px solid #dde1e4;background:#f7f8f9;font-size:9px;color:#86919a}.editor-status>span{display:flex;align-items:center;gap:5px}.splitter{width:7px;flex-shrink:0;background:#e1e4e7;border-inline:1px solid #c9cfd4;cursor:col-resize;display:flex;align-items:center;justify-content:center;touch-action:none}.splitter span{height:28px;width:2px;background:#adb6bd;border-radius:2px}.splitter:hover{background:#bfd0b5}.pdf-panel{flex:1;min-width:0;display:flex;flex-direction:column;min-height:0}.pdf-toolbar{height:40px;background:#f6f7f8}.pdf-toolbar .compile-button{background:#4f8838;color:#fff;padding:7px 12px;min-width:106px;font-size:11px;border-radius:4px}.pdf-toolbar .compile-button:hover{background:#40752d}.auto-compile{display:flex;gap:5px;align-items:center;color:#68776b;font-size:10px;margin-left:5px}.auto-compile input{accent-color:#4f8838}.pdf-surface{flex:1;min-height:0;background:#d1d5d8;position:relative}.pdf-view-controls{height:31px;display:flex;align-items:center;justify-content:space-between;padding:0 15px;background:#e2e5e7;border-bottom:1px solid #c4cbd0;color:#64717a;font-size:10px}.pdf-view-controls select{background:transparent;border:0;outline:none;font-size:10px}.pdf-pages{height:100%;overflow:auto;padding:18px 20px;box-sizing:border-box}.pdf-pages figure{margin:0 auto 20px;min-width:100px}.pdf-pages img{width:100%;height:auto;display:block;box-shadow:0 2px 8px #0003;background:white}.pdf-pages figcaption{font-size:10px;color:#6f7a83;text-align:center;padding-top:8px}.pdf-empty{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;gap:14px;padding:30px;color:#79858e}.pdf-empty>svg{color:#9aa5ad}.pdf-empty h2{font-size:18px;font-weight:500;color:#616f79}.pdf-empty p{font-size:11px;line-height:1.8;max-width:230px}.pdf-empty button{text-decoration:underline}.pdf-stale{font-size:10px;color:#8b6a2b;background:#fff8e8;padding:6px 12px;border-bottom:1px solid #eee0bf}.compile-logs{max-height:40%;height:230px;background:#fff;display:flex;flex-direction:column;border-top:1px solid #c7ced3}.compile-logs header{display:flex;justify-content:space-between;align-items:center;padding:9px 12px;background:#f4f5f6;flex-shrink:0}.compile-logs pre{margin:0;padding:12px;overflow:auto;white-space:pre-wrap;font:10px/1.7 Consolas,monospace}.diagnostics{display:flex;flex-direction:column;max-height:90px;overflow:auto;flex-shrink:0}.diagnostics button{text-align:left;padding:7px 12px;color:#a53939;background:#fff1ee;border-bottom:1px solid #f0d5ce;font-size:10px}.has-errors{color:#ae3434}.error-count{border-radius:8px;background:#b63838;color:white;padding:0 4px;font-size:9px}.new-file,.search-bar{display:flex;gap:5px;background:#f7f8f9;padding:8px;border-bottom:1px solid #d7dbdf}.new-file input,.search-bar input{min-width:0;width:100%;background:#fff;border:1px solid #c8cfd4;padding:5px;font-size:11px}.new-file button,.search-bar button{flex-shrink:0;font-size:10px}.modal-backdrop{position:fixed;inset:0;z-index:100;background:#1d252766;display:flex;align-items:center;justify-content:center;padding:30px}.history-dialog{background:#fff;border-radius:8px;width:600px;max-height:80vh;overflow:auto;padding:24px;box-shadow:0 20px 100px #0003}.history-dialog header{display:flex;justify-content:space-between;margin-bottom:20px}.history-dialog h2{font-size:19px;font-weight:600}.history-dialog p{color:#85919a;margin-top:6px}.history-dialog article{border-top:1px solid #e0e4e7;padding:16px 0}.history-dialog article>div{display:flex;justify-content:space-between}.history-dialog article button{color:#4f7d35}.history-dialog pre{background:#f7f8fa;padding:10px;margin-top:10px;font-size:10px;white-space:pre-wrap;max-height:110px;overflow:hidden}.spin{animation:spin 1s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}@media(prefers-reduced-motion:reduce){.spin{animation:none}}@media(max-width:1100px){.file-panel{width:175px}.evidence-count{display:none}.filename{max-width:120px}.project-title small{display:none}}@media(max-width:760px){.file-panel{display:none}.project-bar{gap:12px;padding:0 10px}.project-actions>a:last-child{display:none}.project-actions>a,.project-actions>button{padding:7px 5px;font-size:10px}.project-title{font-size:11px}.evidence-bar{padding-inline:9px;font-size:10px}.evidence-bar>div{gap:4px}.evidence-bar button{padding:4px}.split-area{flex-direction:column}.source-panel{width:100%!important;height:50%;flex-shrink:0}.splitter{display:none}.pdf-panel{min-height:200px}.filename{max-width:180px}}

.floating-file-panel,.embedded .floating-file-panel{display:flex;width:100%;border:0;background:transparent;max-height:none}.floating-file-panel .file-tree{min-height:0}.floating-file-panel .document-outline{max-height:none;min-height:0}.floating-compile-logs{max-height:none;height:auto;border:0}.floating-compile-logs>header{display:none}
</style>
