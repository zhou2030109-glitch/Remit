import "katex/dist/katex.min.css";
import katex from "katex";
import { type MarkedOptions, marked } from "marked";

const BASE_OPTIONS: MarkedOptions = {
	breaks: true,
	gfm: true,
};

/** 行内/块级公式的分隔符正则 */
const BLOCK_MATH_PATTERN = /\$\$([\s\S]*?)\$\$/g;
const INLINE_MATH_PATTERN = /\\\(([\s\S]*?)\\\)/g;

/** 本地图片引用（相对路径的常见图片扩展名） */
const LOCAL_IMAGE_PATTERN =
	/!\[(.*?)\]\(((?!https?:\/\/).*?\.(?:png|jpg|jpeg|gif|bmp|webp))\)/g;

function typeset(tex: string, displayMode: boolean): string {
	try {
		return katex.renderToString(tex, {
			displayMode,
			throwOnError: false,
			strict: false,
		});
	} catch (error) {
		console.error("KaTeX rendering error:", error);
		return tex;
	}
}

/** 先把 $...$ / \(...\) 公式替换成 KaTeX HTML，再交给 marked 解析。 */
function typesetMath(content: string): string {
	return content
		.replace(BLOCK_MATH_PATTERN, (_match, tex: string) => {
			const html = typeset(tex.trim(), true);
			return `<div class="math-block my-2 overflow-x-auto">${html}</div>`;
		})
		.replace(INLINE_MATH_PATTERN, (_match, tex: string) =>
			typeset(tex.trim(), false),
		);
}

/**
 * 论文里引用的相对路径图片改指到后端静态目录。
 * 依赖 localStorage 里的 currentTaskId 定位任务工作区。
 */
function resolveLocalImages(markdown: string): string {
	const apiBase = import.meta.env.VITE_API_BASE_URL || "http://localhost:18000";
	const taskId = window.localStorage.getItem("currentTaskId") || "";
	return markdown.replace(
		LOCAL_IMAGE_PATTERN,
		(_match, alt: string, src: string) =>
			`![${alt}](${apiBase}/static/${taskId}/${src})`,
	);
}

marked.use({
	hooks: {
		preprocess: resolveLocalImages,
	},
});

/** 将 Markdown 文本同步渲染为 HTML。 */
export function renderMarkdown(
	content: string,
	options: MarkedOptions = {},
): string {
	// 某些模型会把 \[ \] 单独成行，先归一化方便 KaTeX 处理
	const normalized = content
		.replace(/\\\[\s*\n/g, "\\[")
		.replace(/\n\s*\\\]/g, "\\]");
	const result = marked.parse(typesetMath(normalized), {
		...BASE_OPTIONS,
		...options,
		async: false,
	});
	return typeof result === "string" ? result : "";
}

export function getMarkdownLines(content: string): number {
	return content.split("\n").length;
}

export { marked };
