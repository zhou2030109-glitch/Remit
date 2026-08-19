<script setup lang="ts">
// ---- Props ----

/** 组件属性 */
interface Props {
	/** 表头列名 */
	columns: string[];
	/** 行数据（列名 → 值） */
	rows: Array<Record<string, string>>;
	/** 表格标题，通常是文件名 */
	title?: string;
	/** 是否还有更多行未显示 */
	truncated?: boolean;
}
const props = withDefaults(defineProps<Props>(), {
	title: "",
	truncated: false,
});
</script>

<template>
	<div class="overflow-hidden rounded-lg border" data-testid="csv-preview-table">
		<div
			v-if="props.title"
			class="flex items-center justify-between gap-2 border-b bg-muted/40 px-3 py-1.5"
		>
			<code class="truncate text-[10px]">{{ props.title }}</code>
			<span
				v-if="props.truncated"
				class="shrink-0 text-[10px] text-muted-foreground"
			>
				仅显示前 {{ props.rows.length }} 行
			</span>
		</div>
		<div class="max-h-64 overflow-auto">
			<table class="w-full border-collapse text-[11px]">
				<thead class="sticky top-0 bg-muted/60 backdrop-blur">
					<tr>
						<th
							v-for="column in props.columns"
							:key="column"
							class="whitespace-nowrap border-b px-2 py-1 text-left font-medium"
						>
							{{ column }}
						</th>
					</tr>
				</thead>
				<tbody>
					<tr
						v-for="(row, index) in props.rows"
						:key="index"
						class="odd:bg-muted/20"
					>
						<td
							v-for="column in props.columns"
							:key="column"
							class="whitespace-nowrap border-b border-border/50 px-2 py-1 tabular-nums"
						>
							{{ row[column] }}
						</td>
					</tr>
				</tbody>
			</table>
		</div>
	</div>
</template>
