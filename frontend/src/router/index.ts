import {
	type RouteRecordRaw,
	createRouter,
	createWebHistory,
} from "vue-router";

const TaskPage = () => import("@/pages/task/index.vue");

/** 路由表：历史入口统一收敛到 /home，任务页同时兼容新旧两种路径。 */
const routes: RouteRecordRaw[] = [
	{ path: "/", redirect: "/home" },
	{ path: "/home", component: () => import("@/pages/home.vue") },
	// 旧版入口保留为重定向，避免外链失效
	{ path: "/landing", redirect: "/home" },
	{ path: "/login", redirect: "/home" },
	{ path: "/chat", redirect: "/home?new=1" },
	{
		path: "/task/:task_id",
		component: TaskPage,
		props: true,
	},
	{
		path: "/project/:projectId/:stage?",
		component: TaskPage,
		props: (route) => ({ task_id: route.params.projectId }),
	},
];

export default createRouter({
	history: createWebHistory(),
	routes,
});
