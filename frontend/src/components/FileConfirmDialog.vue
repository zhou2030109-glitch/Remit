<script setup lang="ts">
import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { ref } from "vue";

/** 控制弹窗显示 */
const visible = ref(false);

/** 挂起中的用户选择 */
let pendingDecision: ((proceed: boolean) => void) | null = null;

/** 打开确认弹窗，resolve 结果表示用户是否选择继续 */
function openConfirmDialog(): Promise<boolean> {
	visible.value = true;
	return new Promise<boolean>((resolve) => {
		pendingDecision = resolve;
	});
}

function settle(proceed: boolean): void {
	visible.value = false;
	pendingDecision?.(proceed);
	pendingDecision = null;
}

defineExpose({ openConfirmDialog });
</script>

<template>
  <Dialog v-model:open="visible">
    <DialogContent>
      <DialogHeader>
        <DialogTitle>确认操作</DialogTitle>
      </DialogHeader>
      <div class="py-4">
        <p class="text-gray-700">您尚未上传数据附件（赛题 PDF 不计入），确定要继续吗？</p>
      </div>
      <DialogFooter>
        <Button variant="outline" @click="settle(false)">返回上传数据</Button>
        <Button @click="settle(true)">无附件继续</Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
</template>
