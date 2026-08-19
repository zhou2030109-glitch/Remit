import request from "@/utils/request";

/** 单个 Agent 的模型接入参数 */
export interface AgentCredentials {
	apiKey: string;
	baseUrl: string;
	modelId: string;
	apiType: string;
}

/** 验证 API Key 请求参数 */
export interface ValidateApiKeyRequest {
	api_key: string;
	base_url?: string;
	model_id: string;
	api_type?: string;
}

/** 验证 API Key 响应 */
export interface ValidateApiKeyResponse {
	valid: boolean;
	message: string;
}

/** 后端实际生效的单个 Agent 配置元数据（永不包含密钥） */
export interface AgentApiConfigStatus {
	configured: boolean;
	api_key_configured: boolean;
	api_type: string | null;
	model_id: string | null;
	base_url: string | null;
	context_window: number;
	source: "environment" | "runtime" | "missing";
}

/** 全部 Agent 的配置状态 */
export interface ApiConfigStatusResponse {
	configured: boolean;
	model_council_enabled: boolean;
	agents: Record<string, AgentApiConfigStatus>;
}

/** 保存 API 配置请求参数 */
export interface SaveApiConfigRequest {
	coordinator: AgentCredentials;
	modeler: AgentCredentials;
	coder: AgentCredentials;
	writer: AgentCredentials;
	model_scout?: AgentCredentials;
	model_critic?: AgentCredentials;
	model_council_enabled?: boolean;
	openalex_email: string;
}

/** 验证 OpenAlex Email 请求 / 响应 */
export interface ValidateOpenalexEmailRequest {
	email: string;
}
export interface ValidateOpenalexEmailResponse {
	valid: boolean;
	message: string;
}

/** 验证一组模型接入参数是否可用 */
export function validateApiKey(params: ValidateApiKeyRequest) {
	return request.post<ValidateApiKeyResponse>("/validate-api-key", params);
}

/** 查询后端各 Agent 的配置来源与完整度 */
export function getApiConfigStatus() {
	return request.get<ApiConfigStatusResponse>("/api-config-status");
}

/** 校验 OpenAlex 联系邮箱 */
export function validateOpenalexEmail(params: ValidateOpenalexEmailRequest) {
	return request.post<ValidateOpenalexEmailResponse>(
		"/validate-openalex-email",
		params,
	);
}

/** 把界面填写的模型接入配置写入后端运行时 */
export function saveApiConfig(params: SaveApiConfigRequest) {
	return request.post<{ success: boolean; message: string }>(
		"/save-api-config",
		params,
	);
}
