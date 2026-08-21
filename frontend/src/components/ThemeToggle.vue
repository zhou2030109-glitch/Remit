<script setup lang="ts">
import { Moon, Sun } from "lucide-vue-next";
import { onMounted, ref } from "vue";

const isDark = ref(false);

function syncTheme(): void {
	isDark.value = document.documentElement.classList.contains("dark");
}

function toggleTheme(): void {
	isDark.value = !isDark.value;
	document.documentElement.classList.toggle("dark", isDark.value);
	window.localStorage.setItem("remit-theme", isDark.value ? "dark" : "light");
}

onMounted(syncTheme);
</script>

<template>
	<button
		type="button"
		class="theme-switch"
		:aria-label="isDark ? '切换到日间模式' : '切换到夜间模式'"
		:title="isDark ? '日间模式' : '夜间模式'"
		@click="toggleTheme"
	>
		<span :class="{ active: !isDark }"><Sun aria-hidden="true" /></span>
		<span :class="{ active: isDark }"><Moon aria-hidden="true" /></span>
	</button>
</template>

<style scoped>
.theme-switch {
	display: grid;
	height: 40px;
	grid-template-columns: repeat(2, 32px);
	align-items: center;
	border: 1px solid rgb(20 24 20 / 0.1);
	border-radius: 999px;
	background: rgb(255 255 255 / 0.3);
	padding: 3px;
	color: #757a74;
}

:global(.dark .theme-switch) {
	border-color: rgb(255 255 255 / 0.12);
	background: rgb(0 0 0 / 0.22);
	color: rgb(255 255 255 / 0.48);
}

.theme-switch span {
	display: grid;
	width: 32px;
	height: 32px;
	place-items: center;
	border-radius: 50%;
	transition: background-color 180ms ease, color 180ms ease, transform 180ms ease;
}

.theme-switch span.active {
	transform: scale(0.94);
	background: #e7ff2f;
	box-shadow: 0 8px 18px -12px rgb(114 137 0 / 0.8);
	color: #111;
}

.theme-switch svg {
	width: 16px;
	height: 16px;
}

@media (max-width: 680px) {
	.theme-switch {
		grid-template-columns: repeat(2, 27px);
	}

	.theme-switch span {
		width: 27px;
		height: 27px;
	}
}
</style>
