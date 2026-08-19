import request from "@/utils/request";

/** 单张赛题插图的识图结论 */
export interface ProblemFigureInsight {
	index: number;
	page_number: number;
	kind: string;
	figure_type: string;
	title: string;
	transcription: string;
	readable_values: string[];
	modeling_relevance: string;
	carries_information: boolean;
}

export interface ProblemPdfParseResult {
	filename: string;
	text: string;
	page_count: number;
	char_count: number;
	/** 识别出建模信息的插图数量 */
	figure_count: number;
	vision_status: "completed" | "partial" | "failed" | "skipped" | "disabled";
	vision_error: string;
	figures: ProblemFigureInsight[];
}

/** 解析赛题 PDF，并返回可直接交给建模流程的完整文本。 */
export function parseProblemPdf(file: File) {
	const formData = new FormData();
	formData.append("file", file);

	// 识图会额外调用多模态模型，比纯文本解析慢得多，超时必须放宽
	return request.post<ProblemPdfParseResult>("/parse-problem-pdf", formData, {
		timeout: 300000,
	});
}

/**
 * 提交数学建模任务
 * @param problem 问题描述
 * @param files 上传的数据文件
 */
export function submitModelingTask(
	problem: {
		ques_all: string;
		user_requirements?: string;
		comp_template?: string;
		format_output?: string;
	},
	files?: File[],
) {
	const formData = new FormData();
	// 添加问题数据
	formData.append("ques_all", problem.ques_all);
	formData.append("user_requirements", problem.user_requirements || "");
	formData.append("comp_template", "CHINA");
	formData.append("format_output", problem.format_output || "Markdown");

	for (const file of files ?? []) {
		formData.append("files", file);
	}

	return request.post<{
		task_id: string;
		status: string;
	}>("/modeling", formData, {
		timeout: 30000,
	});
}
