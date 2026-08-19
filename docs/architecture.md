# Remit 架构

Remit 由 Vue 前端、FastAPI 后端、Redis 状态通道和本地执行环境组成。

## 运行组件

- **前端**：提供任务创建、历史记录、项目工作区、模型配置和人工审批界面。
- **后端**：负责 API、WebSocket、任务编排、模型调用、文件管理和任务恢复。
- **Redis**：保存消息广播、任务状态与取消信号。
- **执行器**：优先使用 MATLAB，也可回退到 Python；E2B 为可选远程执行环境。

## Agent 工作流

1. Coordinator 忠实提取题面，保存不可被返修覆盖的原题字段，并为每问生成目标、输入数据、决策变量、约束、输出、依赖、风险和验证要求。
2. Research 扫描附件结构、字段、规模与质量；文献调研会检索 → 按小问筛选
   → 抓取开放获取全文 → 提取“方法卡”（问题、方法、适用条件、原文位置），
   并记录每篇文献从被引用到被采用的完整台账。
3. Analysis 使用真实附件画像和文献证据修正逐题理解，将 `problem_analysis.json` 提交人工审批；审批卡不再用原题截断文本代替分析。
4. 方法检索器使用数据校正版分析，按领域、子领域和具体方法逐层评分，为每个正式小问返回 Top-K。
5. Modeler 为每个问题建立候选模型和验证计划；启用评审组时 Scout 读取同一批分析与候选并独立探索。
6. Coder 生成并执行代码，保存数值结果和图表。
7. Modeler 根据执行证据审查或修订方案。
8. Writer 汇总可验证结果并形成论文材料。
9. 工作流在配置的检查点等待人工确认，然后继续或回退；题意返修会携带上一版分析和累计人工意见。

## 数据边界

每个任务使用独立的 `backend/project/work_dir/<task-id>/` 目录。检查点、模型输出、代码、图表和最终文档都应保存在该目录，避免跨任务共享可变状态。

模型配置由后端环境文件加载，也可由前端在当前后端进程内临时覆盖。运行时接口只返回配置是否存在，不返回密钥本身。

## 关键入口

- `backend/app/main.py`：FastAPI 应用。
- `backend/app/core/workflow.py`：主工作流。
- `backend/app/core/data_scout.py`：附件数据画像与质量侦察。
- `backend/app/core/method_retrieval.py`：消费校正版题意的三级建模方法检索与 Top-K 产物持久化。
- `backend/app/core/literature.py`：文献检索、筛选、全文精读与方法卡提取。
- `backend/app/core/citations.py`：最终参考文献台账，只有经过代码验证并采用的文献才进正文。
- `backend/app/core/problem_vision.py`：赛题 PDF 多模态识图，把插图/扫描页转成可建模文字。
- `backend/app/tools/fulltext_fetcher.py`：开放获取全文抓取（OpenAlex/Unpaywall/arXiv/Europe PMC）。
- `backend/app/utils/pdf_figures.py`：从赛题 PDF 裁切图像区域供识图。
- `backend/app/core/knowledge/modeling_methods.json`：Remit 自有建模方法库。
- `backend/app/core/llm/`：模型抽象与供应商实现。
- `backend/app/tools/`：MATLAB、Python 与文档执行工具。
- `frontend/src/pages/`：工作台页面。
- `tools/desktop_app.py`：Windows 桌面壳。
