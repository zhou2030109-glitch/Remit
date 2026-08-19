/** 对应后端 response.py 的消息结构定义 */

import type { AgentType } from "./enum";

/** 系统消息类型 */
export type SystemMessageType = "info" | "warning" | "success" | "error";

/** 消息基础接口 */
export interface BaseMessage {
	id: string;
	created_at?: string;
	msg_type:
		| "system"
		| "agent"
		| "user"
		| "tool"
		| "approval"
		| "execution_summary"
		| "progress"
		| "activity";
	content?: string | null;
}

/** 工具调用消息 */
export interface ToolMessage extends BaseMessage {
	msg_type: "tool";
	tool_name: "execute_code" | "search_scholar";
	input: Record<string, unknown> | null;
	output: string[] | OutputItem[] | null;
}

/** 系统通知消息 */
export interface SystemMessage extends BaseMessage {
	msg_type: "system";
	type: SystemMessageType;
}

/** 用户消息 */
export interface UserMessage extends BaseMessage {
	msg_type: "user";
}

/** Agent 消息基类 */
export interface AgentMessage extends BaseMessage {
	msg_type: "agent";
	agent_type: AgentType;
}

/** 建模手消息 */
export interface ModelerMessage extends AgentMessage {
	agent_type: AgentType.MODELER;
}

/** 协调者消息 */
export interface CoordinatorMessage extends AgentMessage {
	agent_type: AgentType.COORDINATOR;
}

/** 代码执行结果格式类型 */
export type ExecutionFormat =
	| "text"
	| "html"
	| "markdown"
	| "png"
	| "jpeg"
	| "svg"
	| "pdf"
	| "latex"
	| "json"
	| "javascript";

/** 代码执行结果基类 */
export interface BaseCodeExecution {
	res_type: "stdout" | "stderr" | "result" | "error";
	msg?: string;
}

/** 标准输出执行结果 */
export interface StdOutExecution extends BaseCodeExecution {
	res_type: "stdout";
}

/** 标准错误执行结果 */
export interface StdErrExecution extends BaseCodeExecution {
	res_type: "stderr";
}

/** 执行结果 */
export interface ResultExecution extends BaseCodeExecution {
	res_type: "result";
	format: ExecutionFormat;
}

/** 执行错误 */
export interface ErrorExecution extends BaseCodeExecution {
	res_type: "error";
	name: string;
	value: string;
	traceback: string;
}

/** 代码执行输出项 */
export type OutputItem =
	| StdOutExecution
	| StdErrExecution
	| ResultExecution
	| ErrorExecution;

/** 文献搜索工具消息 */
export interface ScholarMessage extends ToolMessage {
	tool_name: "search_scholar";
	input: Record<string, never>;
	output: string[];
}

/** 代码执行工具消息 */
export interface InterpreterMessage extends ToolMessage {
	tool_name: "execute_code";
	input: {
		code: string;
	} | null;
	output: OutputItem[] | null;
}

/** 代码手消息 */
export interface CoderMessage extends AgentMessage {
	agent_type: AgentType.CODER;
}

/** 论文手消息 */
export interface WriterMessage extends AgentMessage {
	agent_type: AgentType.WRITER;
	sub_title?: string;
}

/** 评审组给出的候选方案。 */
export interface ApprovalCandidate {
	question: string;
	name: string;
	role: string;
	reason: string;
}

/** 经过题面提取和附件证据核验的逐题结构化理解。 */
export interface StructuredQuestionAnalysis {
	objective: string;
	input_data: string[];
	decision_variables: string[];
	constraints: string[];
	expected_outputs: string[];
	dependencies: string[];
	risks: string[];
	validation_requirements: string[];
	data_evidence: string[];
}

/** 知情审批扩展：做了什么/关键数字/下一步/退回建议/候选方案。 */
export interface ApprovalExplain {
	what_happened?: string;
	key_numbers?: MetricExplanation[];
	next_step?: string;
	revise_hint?: string;
	candidates?: ApprovalCandidate[];
	question_analyses?: Record<string, StructuredQuestionAnalysis>;
	evidence_issues?: string[];
	/** 探索实验的候选对比表（真实小样本结果） */
	pilot_table?: TablePreview;
	/** 每篇被引文献经代码验证后的采用 / 修改 / 放弃裁决 */
	citation_table?: TablePreview;
}

/** 工作流节点等待人工验收的消息。 */
export interface ApprovalMessage extends BaseMessage {
	msg_type: "approval";
	checkpoint_id: string;
	node_id: string;
	node_label: string;
	summary: string;
	artifacts: string[];
	quality_report: Record<string, unknown>;
	revision_count: number;
	revision_targets: Array<{ node_id: string; label: string }>;
	options: Array<"approve" | "revise">;
	explain?: ApprovalExplain;
}

/** 实时活动播报；同任务固定 id，前端原位刷新。 */
export interface ActivityMessage extends BaseMessage {
	msg_type: "activity";
	category: "llm" | "code" | "gate" | "repair" | "info";
	detail: string;
}

/** 工作流中的一个可视化阶段。 */
export interface ProgressStage {
	node_id: string;
	label: string;
	plain_label: string;
	description: string;
	status: "completed" | "warning" | "failed" | "running" | "pending";
}

/** 实时工作流进度快照；同任务固定 id，只保留最新一条。 */
export interface ProgressMessage extends BaseMessage {
	msg_type: "progress";
	stages: ProgressStage[];
	current_node: string | null;
	completed_count: number;
	total_count: number;
	total_known: boolean;
	percent: number;
}

export type AuditStatus = "completed" | "warning" | "failed" | "pending";

export interface StageAuditOutcome {
	status: AuditStatus;
	summary: string;
	issues: string[];
	data_status?: string;
	literature_status?: string;
	profiled_file_count?: number;
	paper_count?: number;
	question_count?: number;
	expected_question_count?: number;
}

export interface AnalysisAuditSlice {
	analysis_summary: string;
	question_analyses: Record<string, StructuredQuestionAnalysis>;
	outcome?: StageAuditOutcome;
}

/** 方法卡中某条结论在原文中的位置。 */
export interface SourceLocation {
	section: string;
	page: number | null;
	quote: string;
}

/** 从一篇文献提取、建模 Agent 可直接使用的方法卡。 */
export interface MethodCard {
	card_id: string;
	question_key: string;
	title: string;
	citation: string;
	publication_year: number | null;
	doi: string | null;
	url: string;
	/** full_text 表示读到了开放获取全文，abstract_only 表示只有摘要 */
	evidence_level: "full_text" | "abstract_only";
	fulltext_source: string;
	relevance_reason: string;
	problem_solved: string;
	method: string;
	key_steps: string[];
	key_parameters: string[];
	applicable_conditions: string[];
	strengths: string[];
	limitations: string[];
	source_locations: SourceLocation[];
	competition_adaptation: string;
}

/** 一个候选方案及其文献溯源。 */
export interface MethodCandidate {
	question_key: string;
	name: string;
	role: string;
	approach: string;
	source_card_id: string;
	adaptation: string;
}

/** 代码验证后对一篇文献的去留裁决。 */
export interface CitationEntry {
	card_id: string;
	question_key: string;
	decision: "adopted" | "modified" | "rejected";
	decision_label: string;
	evidence: string;
	influence: string;
	candidate_name: string;
	adaptation: string;
	is_selected_model: boolean;
	title: string;
	citation: string;
	publication_year: number | null;
	doi: string | null;
	url: string;
	evidence_level: string;
	method: string;
}

/** 入选文献的简要信息。 */
export interface SelectedPaper {
	title: string;
	publication_year: number | null;
	citation_format: string;
	relevance_reason: string;
	doi: string | null;
	url: string;
	is_oa: boolean;
}

/** 文献 → 方法卡 → 候选 → 验证 → 引用的完整证据链。 */
export interface MethodEvidence {
	method_cards: MethodCard[];
	selected_papers: Record<string, SelectedPaper[]>;
	fulltext_stats: { attempted?: number; succeeded?: number };
	candidates: MethodCandidate[];
	citation_entries: CitationEntry[];
	final_citations: CitationEntry[];
}

export interface TaskWorkspaceSnapshot {
	task_id: string;
	status: string;
	source: {
		original_problem: string;
		title: string;
		background: string;
		ques_count: number;
		questions: Record<string, string>;
	};
	preliminary_analysis: AnalysisAuditSlice;
	research: {
		outcome: StageAuditOutcome;
		data_profile: Record<string, unknown>;
		literature_review: Record<string, unknown>;
		literature_brief: string;
	};
	refined_analysis: AnalysisAuditSlice & { outcome: StageAuditOutcome };
	method_evidence: MethodEvidence;
	method_recommendations: Record<string, unknown>;
	modeler_response: Record<string, unknown>;
	progress: ProgressMessage;
}

/** 一项可核对的运行指标。 */
export interface ExecutionMetric {
	name: string;
	model_value: number;
	baseline_value?: number | null;
	higher_is_better?: boolean | null;
	relative_improvement?: number | null;
}

/** 代码文件及 notebook 章节位置。 */
export interface CodeLocation {
	path: string;
	section: string;
	language: string;
}

/** 一项指标的小白话解释。 */
export interface MetricExplanation {
	name: string;
	friendly_name: string;
	value_text: string;
	meaning: string;
	verdict: "good" | "ok" | "poor" | "info";
}

/** 结果 CSV 的行列预览。 */
export interface TablePreview {
	filename: string;
	columns: string[];
	rows: Array<Record<string, string>>;
	preview_limited_to_rows: number;
}

/** 每个求解节点完成后的结构化运行记录。 */
export interface ExecutionSummaryMessage extends BaseMessage {
	msg_type: "execution_summary";
	node_id: string;
	node_label: string;
	status: "passed" | "refined" | "needs_review";
	run_summary: string;
	selected_model: string;
	candidate_models: string[];
	metrics: ExecutionMetric[];
	code_locations: CodeLocation[];
	artifacts: string[];
	paper_ready_images: string[];
	modeler_verdict: "accept" | "refine" | "manual_review";
	modeler_summary: string;
	modeler_evidence: string[];
	modeler_weaknesses: string[];
	writer_guidance: string;
	revision_count: number;
	metric_explanations?: MetricExplanation[];
	table_previews?: TablePreview[];
}

/** 所有消息类型的联合类型 */
export type Message =
	| SystemMessage
	| UserMessage
	| CoderMessage
	| WriterMessage
	| ModelerMessage
	| CoordinatorMessage
	| ApprovalMessage
	| ExecutionSummaryMessage
	| ProgressMessage
	| ActivityMessage
	| ToolMessage;
