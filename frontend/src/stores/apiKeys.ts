import { AgentType } from "@/utils/enum";
import type { ModelConfig } from "@/utils/interface";
import { defineStore } from "pinia";
import { type Ref, computed, ref } from "vue";

/** 常规流水线 Agent 的默认上下文窗口 */
const STANDARD_CONTEXT_WINDOW = 128000;
/** 模型委员会（候选探索 / 盲审）使用更大的上下文窗口 */
const COUNCIL_CONTEXT_WINDOW = 262144;

function emptyModelConfig(contextWindow: number): ModelConfig {
	return {
		apiKey: "",
		baseUrl: "",
		modelId: "",
		apiType: "",
		contextWindow,
	};
}

/** 各角色的默认上下文窗口 */
const CONTEXT_WINDOWS: Record<AgentType, number> = {
	[AgentType.COORDINATOR]: STANDARD_CONTEXT_WINDOW,
	[AgentType.MODELER]: STANDARD_CONTEXT_WINDOW,
	[AgentType.CODER]: STANDARD_CONTEXT_WINDOW,
	[AgentType.WRITER]: STANDARD_CONTEXT_WINDOW,
	[AgentType.MODEL_SCOUT]: COUNCIL_CONTEXT_WINDOW,
	[AgentType.MODEL_CRITIC]: COUNCIL_CONTEXT_WINDOW,
};

/** API Key 和模型配置 Store */
export const useApiKeyStore = defineStore(
	"apiKeys",
	() => {
		// ---- State ----

		const configs = Object.fromEntries(
			(Object.keys(CONTEXT_WINDOWS) as AgentType[]).map((agent) => [
				agent,
				ref<ModelConfig>(emptyModelConfig(CONTEXT_WINDOWS[agent])),
			]),
		) as Record<AgentType, Ref<ModelConfig>>;

		/** 是否启用模型委员会（多候选 + 盲审） */
		const modelCouncilEnabled = ref(false);

		/** OpenAlex 邮箱 */
		const openalexEmail = ref<string>("");

		// ---- Getters ----

		/** 判断所有配置是否为空 */
		const isEmpty = computed(() =>
			Object.values(configs).every((entry) => entry.value.apiKey === ""),
		);

		// ---- Actions ----

		function applyConfig(agent: AgentType, config: ModelConfig) {
			configs[agent].value = { ...config };
		}

		/** 获取所有 Agent 的模型配置 */
		function getAllAgentConfigs() {
			return Object.fromEntries(
				(Object.keys(configs) as AgentType[]).map((agent) => [
					agent,
					configs[agent].value,
				]),
			) as Record<AgentType, ModelConfig>;
		}

		/** 设置 OpenAlex 邮箱 */
		function setOpenalexEmail(email: string) {
			openalexEmail.value = email;
		}

		/** 重置所有配置为默认值 */
		function resetAll() {
			for (const agent of Object.keys(configs) as AgentType[]) {
				configs[agent].value = emptyModelConfig(CONTEXT_WINDOWS[agent]);
			}
			modelCouncilEnabled.value = false;
			openalexEmail.value = "";
		}

		return {
			// 状态（按角色暴露独立的 ref，保持调用方习惯）
			coordinatorConfig: configs[AgentType.COORDINATOR],
			modelerConfig: configs[AgentType.MODELER],
			coderConfig: configs[AgentType.CODER],
			writerConfig: configs[AgentType.WRITER],
			modelScoutConfig: configs[AgentType.MODEL_SCOUT],
			modelCriticConfig: configs[AgentType.MODEL_CRITIC],
			modelCouncilEnabled,
			openalexEmail,
			isEmpty,

			// 方法
			setCoordinatorConfig: (config: ModelConfig) =>
				applyConfig(AgentType.COORDINATOR, config),
			setModelerConfig: (config: ModelConfig) =>
				applyConfig(AgentType.MODELER, config),
			setCoderConfig: (config: ModelConfig) =>
				applyConfig(AgentType.CODER, config),
			setWriterConfig: (config: ModelConfig) =>
				applyConfig(AgentType.WRITER, config),
			setModelScoutConfig: (config: ModelConfig) =>
				applyConfig(AgentType.MODEL_SCOUT, config),
			setModelCriticConfig: (config: ModelConfig) =>
				applyConfig(AgentType.MODEL_CRITIC, config),
			setModelCouncilEnabled: (enabled: boolean) => {
				modelCouncilEnabled.value = enabled;
			},
			setOpenalexEmail,
			getAllAgentConfigs,
			resetAll,
		};
	},
	{
		// 密钥只保留在当前页面内存中；浏览器仅持久化非敏感的 OpenAlex 邮箱。
		persist: { pick: ["openalexEmail"] },
	},
);
