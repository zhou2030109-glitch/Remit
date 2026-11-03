<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from "vue";
import { getModelBudget, resumeWithModelBudget, type ModelBudgetSnapshot } from "@/apis/teamApi";
import { explainModelingSubmissionFailure } from "@/apis/submitModelingApi";
import FloatingPanel from "./FloatingPanel.vue";

const props = defineProps<{ taskId: string; nodeId: string | null; sending: boolean }>();
const emit = defineEmits<{ resumed: [] }>();
const anchor = ref<HTMLElement | null>(null);
const open = ref(false);
const snapshot = ref<ModelBudgetSnapshot | null>(null);
const busy = ref(false);
const error = ref("");
let version = 0;
let requestId = "";
function reset() { version++; open.value = false; snapshot.value = null; busy.value = false; error.value = ""; requestId = ""; }
watch(() => [props.taskId, props.nodeId], reset);
onBeforeUnmount(() => { version++; });
async function review() {
    if (busy.value || props.sending) return;
    const current = ++version;
    busy.value = true; error.value = ""; snapshot.value = null; requestId = ""; open.value = true;
    try {
        const { data } = await getModelBudget(props.taskId);
        if (current !== version) return;
        if (data.node_id !== props.nodeId) throw new Error("当前阶段已变化，请重新查看。");
        snapshot.value = data;
    } catch (cause) { if (current === version) error.value = explainModelingSubmissionFailure(cause); }
    finally { if (current === version) busy.value = false; }
}
async function confirm() {
    const reviewed = snapshot.value;
    if (!reviewed || busy.value || props.sending) return;
    const current = version;
    busy.value = true; error.value = "";
    requestId ||= crypto.randomUUID().replaceAll("-", "");
    try {
        await resumeWithModelBudget(props.taskId, reviewed.node_id, reviewed.can_resume ? undefined : {
            confirmed: true, request_id: requestId, stage_key: reviewed.stage_key,
            expected_used: reviewed.used, expected_limit: reviewed.limit, expected_seconds: reviewed.seconds_limit,
            additional_calls: 6, additional_seconds: 600,
        });
        if (current !== version) return;
        reset(); emit("resumed");
    } catch (cause) { if (current === version) error.value = explainModelingSubmissionFailure(cause); }
    finally { if (current === version) busy.value = false; }
}
</script>
<template>
    <button ref="anchor" type="button" :disabled="busy || sending" @click="review">查看模型额度并继续</button>
    <FloatingPanel v-model:open="open" :anchor="anchor" title="继续当前阶段" :width="350">
        <div class="model-budget-review">
            <p v-if="busy && !snapshot" role="status">正在读取调用记录…</p>
            <template v-if="snapshot">
                <p>{{ snapshot.label }} · {{ snapshot.phase === 'review' ? '待审查结果' : '待继续执行' }}</p>
                <p>已用 {{ snapshot.used }} / {{ snapshot.limit }} 次调用，累计等待 {{ Math.ceil(snapshot.elapsed_seconds) }} / {{ snapshot.seconds_limit }} 秒。</p>
                <p v-if="snapshot.remaining === 0 && snapshot.phase === 'work' && snapshot.reserved_calls > 0 && snapshot.used < snapshot.limit">当前执行步骤已无可用调用次数；总额度还剩 {{ snapshot.limit - snapshot.used }} 次，须保留给结果审查。</p>
                <p v-else-if="snapshot.remaining === 0">本阶段模型调用次数已用完。</p>
                <p v-if="snapshot.remaining_seconds < snapshot.minimum_request_seconds">当前步骤可用模型等待时间还剩 {{ Math.floor(snapshot.remaining_seconds) }} 秒，发起一次新请求至少需要 {{ Math.ceil(snapshot.minimum_request_seconds) }} 秒。</p>
                <p v-if="snapshot.phase === 'review'">先复核已保存产物，再继续审查。通过检查的计算会复用。</p>
                <p v-if="!snapshot.can_resume">本次仅为当前阶段追加 6 次调用和 600 秒等待额度，原有记录保留。</p>
                <p v-else>可以使用现有额度继续；是否完成取决于后续执行与审查结果。</p>
                <p>这是本阶段的本地运行额度。继续可能调用模型，是否收费取决于你配置的服务；已有成果仍需审核。</p>
                <button type="button" :disabled="busy || sending" @click="confirm">{{ busy ? '正在提交…' : snapshot.can_resume ? '继续当前阶段' : '确认追加并继续' }}</button>
            </template>
            <p v-if="error" role="alert">{{ error }}</p>
        </div>
    </FloatingPanel>
</template>
<style scoped>
.model-budget-review{font-size:13px;line-height:1.7;color:var(--muted-foreground)}p{margin:0 0 10px}button{font-size:12px;text-decoration:underline;text-underline-offset:3px}button:disabled{opacity:.5}button:focus-visible{outline:2px solid currentColor;outline-offset:3px}
</style>
