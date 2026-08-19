import request from "@/utils/request";

/** 工作区文件条目（列表接口返回 filename/file_type，其余字段为可选补充信息） */
export interface WorkspaceFile {
	filename: string;
	file_type: string;
	name?: string;
	size?: number;
	modified_time?: string | number;
	type?: string;
}

/** CSV 预览响应 */
export interface CsvPreview {
	filename: string;
	columns: string[];
	rows: Array<Record<string, string>>;
	truncated: boolean;
}

/** 列出任务工作区的全部文件 */
export function getFiles(task_id: string) {
	return request.get<WorkspaceFile[]>("/files", { params: { task_id } });
}

/** 取单个文件的下载地址 */
export function getFileDownloadUrl(task_id: string, filename: string) {
	return request.get<{ download_url: string }>("/download_url", {
		params: { task_id, filename },
	});
}

/** 取整包（all.zip）的下载地址 */
export function getAllFilesDownloadUrl(task_id: string) {
	return request.get<{ download_url: string }>("/download_all_url", {
		params: { task_id },
	});
}

/** 读取 CSV 的列名与前若干行用于表格预览 */
export function previewCsv(task_id: string, filename: string, max_rows = 20) {
	return request.get<CsvPreview>("/preview_csv", {
		params: { task_id, filename, max_rows },
	});
}
