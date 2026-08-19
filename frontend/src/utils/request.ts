import axios from "axios";

/** 后端 REST 客户端；基地址由 Vite 环境变量注入 */
const http = axios.create({
	baseURL: import.meta.env.VITE_API_BASE_URL,
	timeout: 10_000,
});

http.interceptors.request.use(
	(config) => config,
	(error) => Promise.reject(error),
);

http.interceptors.response.use(
	(response) => response,
	(error) => Promise.reject(error),
);

export default http;
