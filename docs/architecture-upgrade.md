# Remit 架构升级路线（渐进式）

分支：`refactor/architecture-v2`。每一步保持对外接口与旧任务数据兼容，单独提交，并有测试兜底。

## 现状诊断（2026-11-03）

| 问题 | 证据 | 影响 |
| --- | --- | --- |
| 阶段模型额度与代码执行额度矛盾 | 24 次调用 vs 48 次执行；10/7 任务在 EDA 耗尽 | 任务中途 `MODEL_STAGE_BUDGET` 停止（已在 `ca7800b` 放宽到 60 次 / 2400 秒） |
| 测试写入生产日志 | `backend/logs/2026-10-07_error.log` 中 `task-test`、`fixture-model` 等占绝大多数 | 真实故障无法从日志中识别 |
| 日志目录取决于启动时的 cwd | `log_util` 使用 `Path.cwd()/logs` | 根目录与 `backend/` 各有一份日志 |
| 代码手工具调用格式容错差 | 大量“缺少非空字符串 code 参数”“不是有效 JSON”；每次重试都消耗阶段额度 | 无谓消耗额度，三次后整轮失败 |
| Redis 是单进程应用中的额外依赖 | 后端始终单 worker；Redis 仅用于实时推送、用户插话、取消标记；消息本体已存 SQLite | 多一个进程与端口；大量“Redis 超时”；启动/退出/持久化逻辑复杂 |
| 检查点写入未 fsync | `WorkflowCheckpoint.save` 只做 tmp + replace | 断电时可能丢最近状态 |
| `workflow.py` 2944 行 | `_solution_node` 一个方法近 700 行；82 处重复的 `publish_message(SystemMessage(...))` 样板 | 难以修改和测试 |

## 阶段一：任务稳定性与 Agent 协作

1. 日志隔离：日志目录锚定到 `backend/logs`（可用 `REMIT_LOG_DIR` 覆盖）；测试运行时不写文件日志。
2. 代码手工具调用容错：参数已是对象、键名变体（`source`/`script`/`python`）、可修复的 JSON、多个工具调用时只执行第一个，均直接接受，不再白白消耗额度。
3. 检查点写入 fsync，保证断电后要么是旧版本，要么是新版本。

## 阶段二：可维护性

1. 新增 `notify(task_id, content, type)` 统一系统消息发布，替换样板代码。
2. 将求解节点（`_solution_node` 及其质量门/复核循环）从 `workflow.py` 拆为独立模块，`RemitWorkFlow` 只负责编排。

## 阶段三：存储与状态简化

1. 用进程内事件总线替换 Redis：实时推送用 asyncio 订阅队列，插话与取消标记存进程内（任务运行与后端同进程，重启后任务本就会被标记为停止）。
2. 保留 `redis_manager` 单例名作为兼容入口，内部不再连接 Redis；启动器、Docker 与依赖中移除 Redis。

## 完成情况

| 阶段 | 提交 | 验证 |
| --- | --- | --- |
| 阶段一 | `4f499e6` | 修复 10/31 遗留的 2 项失败测试；新增工具调用容错测试 |
| 阶段二 | `4872755`、`575a00f`、`1cbe3c7` | `workflow.py` 2827 → 约 1170 行；求解节点拆为准备 / 门禁循环 / 交付 |
| 阶段三 | 本阶段提交 | Redis 依赖、二进制与启动/打包/Docker/CI 配置全部移除；真实启停与历史任务 WebSocket 回放通过 |

每阶段后端全量 905 项测试通过；去掉 Redis 后全量耗时由约 240 秒降至约 150 秒（不再等待连接超时）。

升级后首次运行 `stop_services` 会回收旧版本遗留的 `redis-server` 进程；`tools/redis/` 下的旧 `dump.rdb` 与 `appendonlydir/` 不再使用，可手动删除。

## 不在本轮范围

- 前端结构调整、写作工作区重构。
- 更换模型或调整质量门禁的业务规则。
