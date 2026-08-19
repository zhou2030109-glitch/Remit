import App from "@/App.vue";
import router from "@/router";
import { createPinia } from "pinia";
import piniaPluginPersistedstate from "pinia-plugin-persistedstate";
import { createApp } from "vue";
import "@/assets/style.css";

/** 在挂载前确定主题，避免首屏明暗闪烁。 */
function applyInitialTheme(): void {
	const stored = window.localStorage.getItem("remit-theme");
	const systemDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
	const useDark = stored ? stored === "dark" : systemDark;
	document.documentElement.classList.toggle("dark", useDark);
}

applyInitialTheme();

const pinia = createPinia();
pinia.use(piniaPluginPersistedstate);

createApp(App).use(router).use(pinia).mount("#app");
