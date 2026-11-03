# 可靠性与安全边界回归

本轮修复覆盖人工返修、自动模式 Pilot、项目切换、断线补偿、取消、文件接口、上传、
Markdown 渲染、Windows 启停和镜像构建边界。升级保持原有 REST 路径和消息类型，
新增事件序号及可选生命周期字段，并兼容旧任务消息档案。

## 行为保证

| 场景 | 保证 | 回归位置 |
| --- | --- | --- |
| 人工返修与恢复 | 返修重新计算；本轮计算完成后写作中断，恢复时不重复计算 | `backend/tests/test_workflow_revision_execution.py` |
| 自动模式选型 | 正式求解采用 Pilot 保存的新方案；探索降级时沿用原方案 | 同上 |
| 模型请求取消 | 取消、超时和重复停止等待 Provider 子任务释放资源 | `backend/tests/test_agent_cancellation.py` |
| 消息持久化 | 旧 JSON 一次迁移、并发追加不丢失、序号分页、删除不复活 | `backend/tests/test_message_archive.py` |
| 断线/漏播 | 历史审批重放、实时消息和持久化漏播补偿 | `backend/tests/test_message_delivery.py` |
| 文件上传 | 目录越界拒绝、同名文件拒绝、整批大小校验及分块写入 | `backend/tests/test_file_boundaries.py` |
| 接收与调度 | 入队失败清理新目录，总线故障仍可取消，排队任务禁止删除 | `backend/tests/test_intake_lifecycle.py` |
| 页面状态 | 项目切换、迟到响应、重连审批、旧进度与 CSV 预览隔离 | `frontend/tests/` |
| HTML 渲染 | 最终 HTML 净化，保留 KaTeX/MathML 的安全渲染 | `frontend/tests/` |
| Windows 进程 | PID 与启动时间/路径匹配才停止，不追溯终止用户终端 | `tests/test_win_stop_launcher.py` |
| 桌面与安装 | 启停互斥、安装版单实例、安装检查失败返回非零 | `backend/tests/test_desktop_launcher.py`、`test_prod_launcher.py` |

## 本地验证

后端运行 `uv run pytest tests -q`、`uv run ruff check app tests`。
前端运行 `pnpm install --frozen-lockfile`、`pnpm test`、`pnpm lint`、`pnpm build`。
仓库根目录使用后端虚拟环境运行 `python -m pytest tests -q`。

单元测试隔离本机消息档案与任务总线状态，测试运行时不写入生产错误日志。
模型与桌面进程的回归采用替身，不能替代真实模型预算验收或 Windows 安装包验收。

Docker 使用允许清单控制构建上下文，并显式复制 `app/` 和依赖清单；真实配置、
工作目录及日志不进入镜像。源代码测试通过不等同于镜像或安装程序已实构建。

## 工作记忆、探索状态与 MATLAB 读写

### 工作记忆

模型返回的真实输入 token 数会校准上下文计数，新助手消息与工具回复继续计入预算。工具调用和回复完整配对后，在下一次请求前检查压缩；不会在未完成的工具调用中插入摘要。

代码手的长工具输出保存到任务目录 `.agent-context/`，模型历史保留文件位置、开头和末尾。任务要求和最新用户修正保留原文。摘要失败时仍保留这些要求和最近完整工具对；剩余内容超过可用窗口时明确报错，不静默丢掉约束继续请求。

### 探索状态

- **通过结构检查**：探索结果及选型结论已经保存，科学结论仍按验收规则处理。
- **跳过、未验证**：候选比较不满足要求，沿用原方案；主页面持续显示限制。
- **技术故障**：保留检查点并交由已有技术恢复策略处理，不直接跳入正式求解。
- **待验收**：仍需用户做决定，不由自动检查代签。

论文输入快照包含探索状态和限制。兼容旧检查点中仅有 `pilot_skipped` 的记录，也会显示未验证。

### MATLAB 函数

Remit 初始化 MATLAB Engine 时自动加入随项目分发的函数目录。它们不依赖项目私有附件。

```matlab
% 明确将编号读成文本，保留前导零；保留原始中文列名。
[T, profile] = remit_read_table('measurements.csv', ["编号"]);

% 缺失保持 NaN，非数值文本会报告行号，不会自动填零。
values = remit_numeric_column(T, "测量值");

% 用真实计算结果构造 struct；必需字段可以一次指定多个。
payload = struct('sample_count', height(T), 'values', values);
saved = remit_write_json('measurements_summary.json', payload, ["sample_count", "values"]);
```

`remit_read_table` 适用于单表头表格；多标题、多分区附件仍需先识别真实表结构。数值列类型来自导入检测，标识符等不应数值化的列必须明确放入 `textColumns`。

`remit_write_json` 接受工作目录中的普通 `.json` 结果文件名，拒绝覆盖输入清单内的附件及核心应用状态。写入前校验，使用 UTF-8 临时文件回读，再以同目录原子替换提交。原子替换不可用会报错，保留原文件。

以 `pilot_` 开头的结果还检查候选字段、逻辑值、有限指标和非负耗时，汇总结构错误。失败候选使用 `ran_ok=false`，未知数值用 `[]`，并写明原因。保存时传入已经产生的完整候选列表；函数不自动合并旧候选，也不生成或修正科学指标。工作流仍负责核对协议和完整性。

### 本地验证

```text
cd backend
uv run pytest tests/test_context_budget.py tests/test_pilot_outcomes.py tests/test_matlab_discovery.py
```

已安装 MATLAB 时，在 MATLAB 中运行以下代码；测试仅使用新建临时目录中的合成小表：

```matlab
repo = '你的 Remit 仓库路径';
addpath(fullfile(repo, 'backend', 'tests', 'matlab'));
test_io_helpers(fullfile(repo, 'backend', 'app', 'tools', 'matlab_helpers'));
```

这些检查验证执行协议和读写可靠性，不替代真实模型调用、科学结果复核或整题验收。
