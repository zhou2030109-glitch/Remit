export type AgentRecord = Record<string, unknown>;

export function parseAgentRecord(content?: string | null): AgentRecord | null {
	if (!content?.trim()) return null;
	const normalized = content
		.trim()
		.replace(/^```(?:json)?\s*/i, "")
		.replace(/\s*```$/, "");
	try {
		const value: unknown = JSON.parse(normalized);
		return value && typeof value === "object" && !Array.isArray(value)
			? (value as AgentRecord)
			: null;
	} catch {
		return null;
	}
}

export function agentValueText(value: unknown): string {
	if (typeof value === "string") return value.trim();
	if (typeof value === "number" || typeof value === "boolean")
		return String(value);
	if (Array.isArray(value))
		return value.map(agentValueText).filter(Boolean).join("\n");
	if (value && typeof value === "object") return JSON.stringify(value, null, 2);
	return "";
}

export function humanizeAgentKey(key: string): string {
	const known: Record<string, string> = {
		background: "题目背景",
		eda: "探索性数据分析",
		sensitivity_analysis: "敏感性与稳健性分析",
		assumptions: "模型假设",
		limitations: "模型局限",
		validation: "验证方案",
	};
	if (known[key]) return known[key];
	const question = key.match(/^ques(?:tion)?_?(\d+)$/i);
	if (question) return `问题 ${question[1]} 方案`;
	return key.replace(/_/g, " ");
}
