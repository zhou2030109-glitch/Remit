<script setup lang="ts">
import type { TaskSummary } from "@/apis/commonApi";
import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
	Command,
	FolderOpen,
	KeyRound,
	Plus,
	Search,
	Settings2,
} from "lucide-vue-next";
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";

// ---- Props ----

const props = withDefaults(
	defineProps<{
		tasks?: TaskSummary[];
		contextActions?: Array<{ id: string; label: string; hint?: string }>;
	}>(),
	{ tasks: () => [], contextActions: () => [] },
);

const emit = defineEmits<{
	newProject: [];
	settings: [];
	command: [id: string];
}>();

const modelValue = defineModel<boolean>({ default: false });

// ---- State ----

const query = ref("");
const router = useRouter();

// ---- Computed ----

const normalizedQuery = computed(() =>
	query.value.trim().toLocaleLowerCase("zh-CN"),
);
const visibleTasks = computed(() =>
	props.tasks
		.filter((task) =>
			normalizedQuery.value
				? task.title.toLocaleLowerCase("zh-CN").includes(normalizedQuery.value)
				: true,
		)
		.slice(0, 5),
);

// ---- Methods ----

const close = () => {
	modelValue.value = false;
};

const createProject = () => {
	close();
	emit("newProject");
};

const openSettings = () => {
	close();
	emit("settings");
};

const openTask = async (taskId: string) => {
	close();
	await router.push(`/project/${taskId}/overview`);
};

const runContextAction = (id: string) => {
	close();
	emit("command", id);
};

const handleGlobalShortcut = (event: KeyboardEvent) => {
	if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
		event.preventDefault();
		modelValue.value = !modelValue.value;
	}
};

watch(modelValue, (open) => {
	if (!open) query.value = "";
});

onMounted(() => document.addEventListener("keydown", handleGlobalShortcut));
onBeforeUnmount(() =>
	document.removeEventListener("keydown", handleGlobalShortcut),
);
</script>

<template>
  <Dialog :open="modelValue" @update:open="modelValue = $event">
    <DialogContent class="top-[22%] max-w-xl translate-y-0 gap-0 overflow-hidden p-0 shadow-[var(--shadow-overlay)]">
      <DialogHeader class="sr-only">
        <DialogTitle>命令面板</DialogTitle>
        <DialogDescription>快速创建或打开数学建模项目</DialogDescription>
      </DialogHeader>
      <div class="flex items-center gap-3 border-b px-4">
        <Search class="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        <Input
          v-model="query"
          class="h-12 border-0 bg-transparent px-0 shadow-none focus-visible:ring-0"
          placeholder="搜索项目或输入命令…"
          aria-label="搜索命令和项目"
          autofocus
        />
        <kbd class="rounded border bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">Esc</kbd>
      </div>

      <div class="max-h-[430px] overflow-y-auto p-2">
        <p class="px-2 py-1.5 text-[11px] font-medium text-muted-foreground">操作</p>
        <Button variant="ghost" class="h-10 w-full justify-start gap-3 px-2 font-normal" @click="createProject">
          <span class="flex h-7 w-7 items-center justify-center rounded-md border bg-card">
            <Plus class="h-3.5 w-3.5" aria-hidden="true" />
          </span>
          <span class="flex-1 text-left text-sm">创建新项目</span>
          <kbd class="text-[10px] text-muted-foreground">N</kbd>
        </Button>
        <Button variant="ghost" class="h-10 w-full justify-start gap-3 px-2 font-normal" @click="openSettings">
          <span class="flex h-7 w-7 items-center justify-center rounded-md border bg-card">
            <KeyRound class="h-3.5 w-3.5" aria-hidden="true" />
          </span>
          <span class="flex-1 text-left text-sm">模型与 API 设置</span>
          <Settings2 class="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
        </Button>

        <template v-if="props.contextActions.length">
          <p class="mt-2 px-2 py-1.5 text-[11px] font-medium text-muted-foreground">当前项目</p>
          <Button
            v-for="action in props.contextActions"
            :key="action.id"
            variant="ghost"
            class="h-10 w-full justify-start gap-3 px-2 font-normal"
            @click="runContextAction(action.id)"
          >
            <span class="flex h-7 w-7 items-center justify-center rounded-md border bg-card">
              <Command class="h-3.5 w-3.5" aria-hidden="true" />
            </span>
            <span class="flex-1 text-left text-sm">{{ action.label }}</span>
            <kbd v-if="action.hint" class="text-[10px] text-muted-foreground">{{ action.hint }}</kbd>
          </Button>
        </template>

        <template v-if="visibleTasks.length">
          <p class="mt-2 px-2 py-1.5 text-[11px] font-medium text-muted-foreground">最近项目</p>
          <Button
            v-for="task in visibleTasks"
            :key="task.task_id"
            variant="ghost"
            class="h-11 w-full justify-start gap-3 px-2 font-normal"
            @click="openTask(task.task_id)"
          >
            <span class="flex h-7 w-7 items-center justify-center rounded-md border bg-card">
              <FolderOpen class="h-3.5 w-3.5" aria-hidden="true" />
            </span>
            <span class="min-w-0 flex-1 text-left">
              <span class="block truncate text-sm">{{ task.title }}</span>
              <span class="block text-[11px] text-muted-foreground">{{ task.message_count }} 条运行记录</span>
            </span>
          </Button>
        </template>

        <div v-else-if="normalizedQuery" class="px-3 py-8 text-center text-sm text-muted-foreground">
          没有找到匹配的项目
        </div>
      </div>
    </DialogContent>
  </Dialog>
</template>
