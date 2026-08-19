<script setup lang="ts">
import {
	type AgentApiConfigStatus,
	getApiConfigStatus,
	saveApiConfig,
	validateApiKey,
	validateOpenalexEmail,
} from "@/apis/apiKeyApi";
import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
	Select,
	SelectContent,
	SelectGroup,
	SelectItem,
	SelectLabel,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { useApiKeyStore } from "@/stores/apiKeys";
import type { ModelConfig } from "@/utils/interface";
import {
	CheckCircle,
	CircleAlert,
	LoaderCircle,
	XCircle,
} from "lucide-vue-next";
import { computed, reactive, ref, watch } from "vue";

const props = defineProps<{ open: boolean }>();
const emit = defineEmits<(e: "update:open", value: boolean) => void>();

const apiKeyStore = useApiKeyStore();

/** API 类型选项（取值即后端 ApiType 线协议） */
const API_TYPE_OPTIONS = [
	{ value: "openai-chat", label: "OpenAI Chat" },
	{ value: "openai-responses", label: "OpenAI Responses" },
	{ value: "anthropic", label: "Anthropic" },
	{ value: "gemini", label: "Gemini 原生" },
];

type AgentKey =
	| "coordinator"
	| "modeler"
	| "coder"
	| "writer"
	| "model_scout"
	| "model_critic";

interface AgentFormConfig {
	apiKey: string;
	baseUrl: string;
	modelId: string;
	apiType: string;
	contextWindow: number;
}

interface AgentFieldMeta {
	key: AgentKey;
	label: string;
	defaultContextWindow: number;
	councilOnly: boolean;
}

/** 六个可配置 Agent 的表单元数据 */
const AGENT_FIELDS: AgentFieldMeta[] = [
	{
		key: "coordinator",
		label: "协调者模型配置",
		defaultContextWindow: 128000,
		councilOnly: false,
	},
	{
		key: "modeler",
		label: "主建模手模型配置",
		defaultContextWindow: 128000,
		councilOnly: false,
	},
	{
		key: "coder",
		label: "代码手模型配置",
		defaultContextWindow: 128000,
		councilOnly: false,
	},
	{
		key: "writer",
		label: "论文手模型配置",
		defaultContextWindow: 128000,
		councilOnly: false,
	},
	{
		key: "model_scout",
		label: "候选模型探索（独立）",
		defaultContextWindow: 262144,
		councilOnly: true,
	},
	{
		key: "model_critic",
		label: "方案盲审与质疑（独立）",
		defaultContextWindow: 262144,
		councilOnly: true,
	},
];

function blankAgentForm(contextWindow: number): AgentFormConfig {
	return { apiKey: "", baseUrl: "", modelId: "", apiType: "", contextWindow };
}

function buildEmptyAgentForms(): Record<AgentKey, AgentFormConfig> {
	return Object.fromEntries(
		AGENT_FIELDS.map((field) => [
			field.key,
			blankAgentForm(field.defaultContextWindow),
		]),
	) as Record<AgentKey, AgentFormConfig>;
}

/** 本地表单数据 */
const agentForms = reactive(buildEmptyAgentForms());
const modelCouncilEnabled = ref(false);
const openalexEmail = ref("");

/** 验证加载状态 */
const validating = ref(false);
const statusLoading = ref(false);
const statusError = ref("");
const effectiveAgents = ref<Partial<Record<AgentKey, AgentApiConfigStatus>>>(
	{},
);

type Verdict = { valid: boolean; message: string };

function blankVerdicts(): Record<AgentKey | "openalex_email", Verdict> {
	const verdicts = {} as Record<AgentKey | "openalex_email", Verdict>;
	for (const field of AGENT_FIELDS) {
		verdicts[field.key] = { valid: false, message: "" };
	}
	verdicts.openalex_email = { valid: false, message: "" };
	return verdicts;
}

/** 各配置项的验证结果 */
const validationResults = ref(blankVerdicts());

/** 当前需要展示的 Agent 配置区块（评审组仅在启用时出现） */
const visibleFields = computed(() =>
	AGENT_FIELDS.filter(
		(field) => !field.councilOnly || modelCouncilEnabled.value,
	),
);

/** 判断所有验证是否都通过 */
const allValid = computed(() => {
	const keys: Array<AgentKey | "openalex_email"> = [
		"coordinator",
		"modeler",
		"coder",
		"writer",
		"openalex_email",
	];
	if (modelCouncilEnabled.value) {
		keys.push("model_scout", "model_critic");
	}
	return keys.every((key) => validationResults.value[key].valid);
});

/** 从 store 加载数据到表单 */
function loadFromStore(): void {
	const storeConfigs: Record<AgentKey, ModelConfig> = {
		coordinator: apiKeyStore.coordinatorConfig,
		modeler: apiKeyStore.modelerConfig,
		coder: apiKeyStore.coderConfig,
		writer: apiKeyStore.writerConfig,
		model_scout: apiKeyStore.modelScoutConfig,
		model_critic: apiKeyStore.modelCriticConfig,
	};
	for (const field of AGENT_FIELDS) {
		agentForms[field.key] = {
			...storeConfigs[field.key],
			contextWindow:
				storeConfigs[field.key].contextWindow ?? field.defaultContextWindow,
		};
	}
	modelCouncilEnabled.value = apiKeyStore.modelCouncilEnabled;
	openalexEmail.value = apiKeyStore.openalexEmail;
}

function sourceLabel(source?: AgentApiConfigStatus["source"]): string {
	switch (source) {
		case "runtime":
			return "界面设置";
		case "environment":
			return "后端环境";
		default:
			return "未配置";
	}
}

function apiTypeLabel(value?: string | null): string {
	return (
		API_TYPE_OPTIONS.find((option) => option.value === value)?.label ||
		value ||
		"未设置"
	);
}

/** 读取后端当前真正用于创建 Agent 的有效配置，密钥只返回是否存在。 */
async function loadEffectiveConfig(): Promise<void> {
	statusLoading.value = true;
	statusError.value = "";
	try {
		const response = await getApiConfigStatus();
		const agents = response.data.agents as Record<
			AgentKey,
			AgentApiConfigStatus
		>;
		effectiveAgents.value = agents;
		modelCouncilEnabled.value = response.data.model_council_enabled;
		apiKeyStore.setModelCouncilEnabled(response.data.model_council_enabled);

		for (const field of visibleFields.value) {
			const current = agents[field.key];
			if (!current) {
				continue;
			}
			agentForms[field.key] = {
				...agentForms[field.key],
				apiType: current.api_type || "",
				baseUrl: current.base_url || "",
				modelId: current.model_id || "",
				contextWindow: current.context_window || field.defaultContextWindow,
			};
		}
	} catch (error) {
		console.error("读取当前有效 API 配置失败:", error);
		statusError.value = "无法读取后端当前配置，请确认后端服务已启动";
	} finally {
		statusLoading.value = false;
	}
}

/** 保存表单数据到 store 和后端 */
async function saveToStore(): Promise<void> {
	apiKeyStore.setCoordinatorConfig(agentForms.coordinator);
	apiKeyStore.setModelerConfig(agentForms.modeler);
	apiKeyStore.setCoderConfig(agentForms.coder);
	apiKeyStore.setWriterConfig(agentForms.writer);
	apiKeyStore.setModelScoutConfig(agentForms.model_scout);
	apiKeyStore.setModelCriticConfig(agentForms.model_critic);
	apiKeyStore.setModelCouncilEnabled(modelCouncilEnabled.value);
	apiKeyStore.setOpenalexEmail(openalexEmail.value);

	if (!allValid.value) {
		return;
	}
	try {
		await saveApiConfig({
			...agentForms,
			model_council_enabled: modelCouncilEnabled.value,
			openalex_email: openalexEmail.value,
		});
	} catch (error) {
		console.error("保存配置到后端失败:", error);
	}
}

watch(
	() => props.open,
	(open) => {
		if (!open) {
			return;
		}
		loadFromStore();
		void loadEffectiveConfig();
	},
	{ immediate: true },
);

function updateOpen(value: boolean): void {
	emit("update:open", value);
}

/** 保存并关闭弹窗 */
async function saveAndClose(): Promise<void> {
	await saveToStore();
	updateOpen(false);
}

/** 验证大模型 API Key；留空密钥时尝试沿用后端已生效配置 */
async function validateModelApiKey(
	config: AgentFormConfig,
	key: AgentKey,
): Promise<Verdict> {
	if (!config.apiKey) {
		const effective = effectiveAgents.value[key];
		const unchanged =
			effective?.configured &&
			(effective.api_type || "") === config.apiType &&
			(effective.model_id || "") === config.modelId &&
			(effective.base_url || "") === config.baseUrl;
		if (unchanged) {
			return {
				valid: true,
				message: "✓ 沿用当前后端密钥；为安全起见未重新回显或发送密钥",
			};
		}
		return { valid: false, message: "API Key 为空" };
	}

	if (!config.modelId) {
		return { valid: false, message: "Model ID 为空" };
	}

	try {
		const result = await validateApiKey({
			api_key: config.apiKey,
			base_url: config.baseUrl || "https://api.openai.com/v1",
			model_id: config.modelId,
			api_type: config.apiType || "openai-chat",
		});
		return { valid: result.data.valid, message: result.data.message };
	} catch {
		return { valid: false, message: "✗ 验证失败: 无法连接到验证服务" };
	}
}

/** 一键验证所有 API Keys */
async function validateAllApiKeys(): Promise<void> {
	validating.value = true;
	validationResults.value = blankVerdicts();

	try {
		for (const field of visibleFields.value) {
			validationResults.value[field.key] = {
				valid: false,
				message: "验证中...",
			};
			validationResults.value[field.key] = await validateModelApiKey(
				agentForms[field.key],
				field.key,
			);
			// 避免触发供应商限流
			await new Promise((resolve) => setTimeout(resolve, 1000));
		}

		const emailResult = await validateOpenalexEmail({
			email: openalexEmail.value,
		});
		validationResults.value.openalex_email = emailResult.data;
	} catch (error) {
		console.error("验证过程中发生错误:", error);
		for (const key of Object.keys(validationResults.value) as Array<
			keyof typeof validationResults.value
		>) {
			if (!validationResults.value[key].message) {
				validationResults.value[key] = {
					valid: false,
					message: "验证过程中发生未知错误",
				};
			}
		}
	} finally {
		validating.value = false;
	}
}

/** 重置所有表单数据 */
function resetAll(): void {
	Object.assign(agentForms, buildEmptyAgentForms());
	modelCouncilEnabled.value = false;
	openalexEmail.value = "";
}
</script>

<template>
  <Dialog :open="props.open" @update:open="updateOpen">
    <DialogContent class="max-w-xl max-h-[85vh] overflow-y-auto">
      <DialogHeader>
        <DialogTitle>设置</DialogTitle>
        <DialogDescription>为每个 Agent 配置 API 类型和模型</DialogDescription>
      </DialogHeader>

      <div v-if="statusLoading"
        class="flex items-center gap-2 rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600"
        role="status">
        <LoaderCircle class="h-3.5 w-3.5 animate-spin" />
        正在读取后端当前生效配置…
      </div>
      <div v-else-if="statusError"
        class="flex items-center gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800"
        role="alert">
        <CircleAlert class="h-3.5 w-3.5 shrink-0" />
        {{ statusError }}
      </div>
      <div v-else-if="Object.keys(effectiveAgents).length"
        class="flex items-center justify-between rounded-md border border-emerald-200 bg-emerald-50/70 px-3 py-2 text-xs">
        <span class="flex items-center gap-2 font-medium text-emerald-800">
          <span class="h-2 w-2 rounded-full bg-emerald-500" aria-hidden="true"></span>
          已读取后端实际配置
        </span>
        <span class="text-emerald-700">API Key 仅显示配置状态，不回显内容</span>
      </div>

      <div class="space-y-4 py-2">
        <section class="rounded-lg border border-violet-200 bg-violet-50/60 p-3" aria-labelledby="model-council-title">
          <div class="flex items-start justify-between gap-4">
            <div>
              <div class="flex flex-wrap items-center gap-2">
                <h3 id="model-council-title" class="text-sm font-semibold text-slate-900">多模型建模评审组</h3>
                <span
                  class="rounded-full border border-violet-200 bg-white px-2 py-0.5 text-[10px] font-medium text-violet-700">
                  AI 生成 · 人工最终确认
                </span>
              </div>
              <p class="mt-1 text-[11px] leading-5 text-slate-600">
                独立探索候选 → 匿名盲审 → 主建模手综合 → MATLAB 同口径实测。评审组只能提出方案，不能绕过质量门禁或人工审核。
              </p>
            </div>
            <label class="inline-flex shrink-0 cursor-pointer items-center gap-2 text-xs font-medium text-slate-700">
              <input v-model="modelCouncilEnabled" type="checkbox"
                class="h-4 w-4 rounded border-slate-300 text-violet-600 focus:ring-2 focus:ring-violet-500 focus:ring-offset-2" />
              启用
            </label>
          </div>
          <div v-if="modelCouncilEnabled" class="mt-2 flex flex-wrap gap-1.5" aria-label="模型评审组流程">
            <span class="rounded-full bg-white px-2 py-1 text-[10px] text-slate-600 ring-1 ring-slate-200">主建模方案 A</span>
            <span class="rounded-full bg-white px-2 py-1 text-[10px] text-slate-600 ring-1 ring-slate-200">独立候选 B</span>
            <span class="rounded-full bg-white px-2 py-1 text-[10px] text-slate-600 ring-1 ring-slate-200">匿名质疑</span>
            <span class="rounded-full bg-white px-2 py-1 text-[10px] text-slate-600 ring-1 ring-slate-200">统一实验矩阵</span>
          </div>
        </section>

        <div v-for="field in visibleFields" :key="field.key" class="space-y-2">
          <div class="flex items-center justify-between gap-3">
            <h3 class="text-sm font-medium">{{ field.label }}</h3>
            <span v-if="effectiveAgents[field.key]" :class="[
              'inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium',
              effectiveAgents[field.key]?.configured
                ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
                : 'border-amber-200 bg-amber-50 text-amber-700'
            ]">
              <span :class="[
                'h-1.5 w-1.5 rounded-full',
                effectiveAgents[field.key]?.configured ? 'bg-emerald-500' : 'bg-amber-500'
              ]" aria-hidden="true"></span>
              {{ effectiveAgents[field.key]?.configured ? '当前生效' : '配置不完整' }}
              · {{ sourceLabel(effectiveAgents[field.key]?.source) }}
            </span>
          </div>
          <div v-if="effectiveAgents[field.key]?.configured"
            class="border-l-2 border-emerald-400 bg-slate-50 px-2.5 py-1.5 text-[11px] leading-5 text-slate-600">
            <div class="font-medium text-slate-800">
              {{ apiTypeLabel(effectiveAgents[field.key]?.api_type) }}
              · {{ effectiveAgents[field.key]?.model_id }}
            </div>
            <div class="break-all">{{ effectiveAgents[field.key]?.base_url || '使用供应商 SDK 默认地址' }}</div>
          </div>

          <div class="grid grid-cols-2 gap-2">
            <div class="space-y-1">
              <Label :for="`${field.key}-api-type`" class="text-xs text-muted-foreground">API 类型</Label>
              <Select v-model="agentForms[field.key].apiType">
                <SelectTrigger class="w-full h-7 text-xs">
                  <SelectValue placeholder="选择 API 类型" />
                </SelectTrigger>
                <SelectContent>
                  <SelectGroup>
                    <SelectLabel>API 类型</SelectLabel>
                    <SelectItem v-for="opt in API_TYPE_OPTIONS" :key="opt.value" :value="opt.value">
                      {{ opt.label }}
                    </SelectItem>
                  </SelectGroup>
                </SelectContent>
              </Select>
            </div>

            <div class="space-y-1">
              <Label :for="`${field.key}-api-key`" class="text-xs text-muted-foreground">API Key</Label>
              <Input :id="`${field.key}-api-key`" v-model.trim="agentForms[field.key].apiKey" type="password"
                :placeholder="effectiveAgents[field.key]?.api_key_configured && !agentForms[field.key].apiKey
                  ? '后端已配置（安全起见不回显）'
                  : '请输入 API Key'" class="h-7 text-xs flex-1" />
              <p v-if="effectiveAgents[field.key]?.api_key_configured && !agentForms[field.key].apiKey"
                class="text-[11px] leading-4 text-emerald-700">
                密钥正在使用；留空会继续沿用后端配置
              </p>
              <div v-if="validationResults[field.key].message" class="flex items-center">
                <CheckCircle v-if="validationResults[field.key].valid" class="h-4 w-4 text-green-500" />
                <XCircle v-else class="h-4 w-4 text-red-500" />
              </div>
            </div>
          </div>

          <div class="grid grid-cols-2 gap-2">
            <div class="space-y-1">
              <Label :for="`${field.key}-base-url`" class="text-xs text-muted-foreground">Base URL</Label>
              <Input :id="`${field.key}-base-url`" v-model.trim="agentForms[field.key].baseUrl"
                placeholder="https://api.openai.com/v1" class="h-7 text-xs" />
            </div>
            <div class="space-y-1">
              <Label :for="`${field.key}-model-id`" class="text-xs text-muted-foreground">Model ID</Label>
              <Input :id="`${field.key}-model-id`" v-model.trim="agentForms[field.key].modelId"
                placeholder="gpt-4o / claude-sonnet-4-20250514" class="h-7 text-xs" />
            </div>
          </div>
          <div class="space-y-1">
            <Label :for="`${field.key}-context-window`" class="text-xs text-muted-foreground">
              上下文窗口（token）
            </Label>
            <Input :id="`${field.key}-context-window`" v-model.number="agentForms[field.key].contextWindow"
              type="number" placeholder="128000" class="h-7 text-xs" min="4096" step="1024" />
          </div>
          <div v-if="validationResults[field.key].message" :class="[
            'text-xs px-2 py-1 rounded text-left border',
            validationResults[field.key].valid
              ? 'bg-green-50 text-green-700 border-green-200'
              : 'bg-red-50 text-red-700 border-red-200'
          ]">
            {{ validationResults[field.key].message }}
          </div>
        </div>
      </div>

      <div class="space-y-2">
        <h3 class="text-sm font-medium">其他</h3>
        <Label for="openalex-email" class="text-xs text-muted-foreground">OpenAlex Email</Label>
        <div class="text-xs text-muted-foreground">
          使用 email 注册账号从
          <a href="https://openalex.org/" target="_blank" rel="noopener noreferrer"
            class="text-blue-600 hover:text-blue-800 underline text-xs">OpenAlex</a>
          获取访问文献权利
        </div>
        <Input id="openalex-email" v-model.trim="openalexEmail" placeholder="请输入 OpenAlex Email"
          class="h-7 text-xs flex-1" />
        <div v-if="validationResults.openalex_email.message" :class="[
          'text-xs px-2 py-1 rounded text-left border',
          validationResults.openalex_email.valid
            ? 'bg-green-50 text-green-700 border-green-200'
            : 'bg-red-50 text-red-700 border-red-200'
        ]">
          {{ validationResults.openalex_email.message }}
        </div>
      </div>

      <div class="flex justify-between items-center pt-3 border-t">
        <div class="flex justify-between items-center gap-2">
          <Button variant="secondary" class="h-7 text-xs px-3" :disabled="validating" @click="validateAllApiKeys">
            {{ validating ? '验证中...' : '一键验证' }}
          </Button>
          <Button variant="secondary" class="h-7 text-xs px-3" @click="resetAll">
            重置
          </Button>
        </div>
        <div class="flex space-x-2">
          <Button variant="outline" class="h-7 text-xs px-3" @click="updateOpen(false)">
            取消
          </Button>
          <Button class="h-7 text-xs px-3" @click="saveAndClose">
            保存
          </Button>
        </div>
      </div>
    </DialogContent>
  </Dialog>
</template>
