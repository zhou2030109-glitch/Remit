# Remit 三级方法检索

Remit 在建模阶段前，为 Coordinator 拆出的每个正式小问独立返回 Top-K 建模方法。实现借鉴层级检索的公开思想，但检索代码、字段设计和方法库均由 Remit 独立实现，不复制第三方仓库代码或数据。

## 检索层级

```text
领域（预测、优化、评价与决策等）
  → 子领域（时间序列预测、数学规划等）
    → 具体方法（SARIMA、线性规划等）
```

检索文本由不可修改的小问原文、数据校正版逐题分析、用户要求、数据画像和文献摘要组成。逐题分析显式提供目标、数据、变量、约束、依赖、风险和验证要求，避免只按题面关键词选型。系统分别计算领域、子领域和方法相关性，再按 `0.2 / 0.3 / 0.5` 合成总分。候选按总分稳定排序并按方法 ID 去重，因此相同输入和方法库会得到相同结果。

## 输出与工作流

每个候选包含：

- 方法 ID、名称和三级来源；
- 总分、三级分项得分和命中关键词；
- 方法摘要、适用前提、常见失败模式和验证建议。

默认每题返回 6 个候选，并写入任务目录的 `method_recommendations.json`。同一结果会注入主 Modeler 和独立 Scout，也会显示在模型选择审批中。候选只作为可审查证据：Agent 必须结合题意判断，可以拒绝高分方法，但应说明理由。

断点恢复时优先复用检查点中的候选，避免同一任务中途漂移；如果 JSON 产物缺失，系统会用检查点内容自动重建。人工退回 Modeler 并给出修订意见后，系统会结合新要求重新检索。

## 配置

```dotenv
METHOD_RETRIEVAL_ENABLED=true
METHOD_RETRIEVAL_TOP_K=6
METHOD_LIBRARY_PATH=
```

内置方法库位于 `backend/app/core/knowledge/modeling_methods.json`。`METHOD_LIBRARY_PATH` 可指向自己的 JSON 方法库。自定义库仍需保持“领域数组 → `subdomains` → `methods`”三级结构，每个节点必须有唯一 `id`；方法建议同时填写 `name`、`summary`、`keywords`、`assumptions`、`failure_modes` 和 `validation`。

方法检索不依赖通用 RAG、向量数据库或外部 API，关闭 `RAG_ENABLED` 时仍可正常工作。
