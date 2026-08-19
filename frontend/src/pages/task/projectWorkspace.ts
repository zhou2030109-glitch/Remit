export type StageKey =
	| "overview"
	| "problem"
	| "data"
	| "literature"
	| "model"
	| "solve"
	| "results"
	| "paper";

export type StageStatus =
	| "not_started"
	| "running"
	| "awaiting_approval"
	| "completed"
	| "warning"
	| "failed";

export interface ProjectStage {
	key: StageKey;
	label: string;
	status: StageStatus;
}

export interface ProjectAssetCount {
	key: "datasets" | "code" | "charts" | "experiments" | "paper" | "references";
	label: string;
	count: number;
}
