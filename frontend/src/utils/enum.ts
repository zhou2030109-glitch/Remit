/** 流水线角色标识（与后端线协议一致） */
export enum AgentType {
	COORDINATOR = "CoordinatorAgent",
	MODELER = "ModelerAgent",
	CODER = "CoderAgent",
	WRITER = "WriterAgent",
	MODEL_SCOUT = "ModelScoutAgent",
	MODEL_CRITIC = "ModelCriticAgent",
}

/** 模型接入协议（与后端 ApiType 一致） */
export enum ApiType {
	OPENAI_CHAT = "openai-chat",
	OPENAI_RESPONSES = "openai-responses",
	ANTHROPIC = "anthropic",
	GEMINI = "gemini",
}
