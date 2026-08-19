<script setup lang="ts">
import { Button } from "@/components/ui/button";
import { Moon, Sun } from "lucide-vue-next";
import { onMounted, ref } from "vue";

const isDark = ref(false);

const syncTheme = () => {
	isDark.value = document.documentElement.classList.contains("dark");
};

const toggleTheme = () => {
	isDark.value = !isDark.value;
	document.documentElement.classList.toggle("dark", isDark.value);
	window.localStorage.setItem(
		"remit-theme",
		isDark.value ? "dark" : "light",
	);
};

onMounted(syncTheme);
</script>

<template>
  <Button
    type="button"
    variant="ghost"
    size="icon"
    class="h-9 w-9 text-muted-foreground"
    :aria-label="isDark ? '切换到浅色模式' : '切换到深色模式'"
    :title="isDark ? '浅色模式' : '深色模式'"
    @click="toggleTheme"
  >
    <Sun v-if="isDark" class="h-4 w-4" aria-hidden="true" />
    <Moon v-else class="h-4 w-4" aria-hidden="true" />
  </Button>
</template>
