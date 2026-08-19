import type { OutputItem } from "./response";

/** 代码单元 */
export interface CodeCell {
	type: "code";
	content: string;
}

/** 执行结果单元 */
export interface ResultCell {
	type: "result";
	code_results: OutputItem[];
}

/** notebook 单元联合类型 */
export type NoteCell = CodeCell | ResultCell;

/** 单个 Agent 的模型接入配置 */
export interface ModelConfig {
	apiKey: string;
	baseUrl: string;
	modelId: string;
	apiType: string;
	/** 上下文窗口（token），用于记忆压缩阈值 */
	contextWindow?: number;
}
