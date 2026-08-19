<script setup lang="ts">
import UserStepper from "@/components/UserStepper.vue";
import {
	Sheet,
	SheetContent,
	SheetDescription,
	SheetHeader,
	SheetTitle,
} from "@/components/ui/sheet";

const modelValue = defineModel<boolean>({ default: false });
</script>

<template>
  <Sheet :open="modelValue" @update:open="modelValue = $event">
    <SheetContent
      side="right"
      class="flex w-full flex-col gap-0 overflow-hidden border-l bg-background p-0 sm:max-w-[620px]"
    >
      <SheetHeader class="shrink-0 border-b bg-card px-6 py-5 text-left">
        <SheetTitle class="text-lg">创建数学建模项目</SheetTitle>
        <SheetDescription class="leading-5">
          上传数据与赛题 PDF。解析完成后会进入工作台，并在每个关键节点等待你的确认。
        </SheetDescription>
      </SheetHeader>
      <div class="min-h-0 flex-1 overflow-y-auto px-6 py-5">
        <UserStepper />
      </div>
    </SheetContent>
  </Sheet>
</template>
