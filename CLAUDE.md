# CLAUDE.md

本文件为 AI 编码助手提供本仓库的工作指引。

## 项目概述

Remit 是本地优先的数学建模工作台，通过多 Agent 协作完成建模、代码生成、结果验证和论文撰写。核心工作流：CoordinatorAgent 分析问题 → ModelerAgent 建模 → CoderAgent 编码执行 → WriterAgent 撰写论文，期间穿插人工审核节点（checkpoint approval）。

## 常用命令

### 后端

```bash
cd backend

# 安装依赖
uv sync

# 启动开发服务器（需要先启动 Redis）
ENV=DEV uvicorn app.main:app --host 0.0.0.0 --port 18000 --ws-ping-interval 60 --ws-ping-timeout 120 --reload

# 测试
.\.venv\Scripts\python.exe -m pytest tests -q

# Lint
.\.venv\Scripts\python.exe -m ruff check app
```

### 前端

```bash
cd frontend

pnpm i              # 安装依赖
pnpm run dev        # 开发服务器
pnpm run build      # 类型检查 + 构建
npx biome check src/          # Lint
npx biome check --write src/  # 自动修复
```

### Docker

```bash
docker-compose up -d    # 后台启动
docker-compose down     # 停止
```

## 项目结构

```
backend/
  app/
    core/
      agents/          # Agent 实现（继承统一的 Agent 基类）
      llm/             # LLM 调用层：类型、四家供应商适配、工厂
      prompts/         # 各 Agent 的提示词
      flows/           # 编排逻辑（问题拆分、子任务管理）
      workflow.py      # 工作流主入口
    routers/           # FastAPI 路由（REST + WebSocket）
    schemas/           # Pydantic 模型（请求/响应/枚举，即线协议）
    services/          # Redis 管理、WebSocket 管理
    tools/             # 代码解释器（本地 Jupyter / E2B 云端）、文献检索
    utils/             # 日志、进度跟踪、数据记录等
    config/            # 配置（Pydantic Settings）与论文模板

frontend/
  src/
    apis/              # 后端 API 调用封装
    components/        # 业务组件 + shadcn-vue UI 库（components/ui/ 不要修改）
    pages/             # 页面（home、task/）
    stores/            # Pinia 状态（apiKeys、task）
    utils/             # 类型定义、WebSocket 客户端、Markdown 渲染
```

## 代码约定

### 后端（Python）

- 模块级、类级、公共方法使用 Google 风格 docstring（Args/Returns/Raises）
- 类型注解使用 `str | None`，不用 `Optional[str]`
- 全程 async/await，FastAPI 路由均为 async def
- 中文注释，解释 WHY 而非 WHAT

### 前端（Vue 3 + TypeScript）

- SFC 使用 `<script setup lang="ts">`
- TypeScript 接口和 API 函数使用 JSDoc `/** */` 注释
- tab 缩进、双引号，由 Biome 管理 lint 与格式化

## Git 提交

格式：`<type>: <描述>`，type 取值：`feat` / `fix` / `refactor` / `chore` / `enhance` / `docs`。

## 边界

### 不要修改的内容

- `frontend/src/components/ui/` — shadcn-vue 生成的第三方 UI 组件
- 已有的 `# type: ignore` 注释 — 经过验证的类型抑制，非遗留问题
- `.env` 相关文件中的实际配置值

### 线协议（前后端共有契约，改名需双端同步）

- `AgentType` 枚举值（`CoordinatorAgent` 等）与消息 `msg_type` 取值
- 章节键（`firstPage` / `RepeatQues` / `quesN` 等）
- 环境变量名（`COORDINATOR_API_KEY` 等）与 REST 路径

### 运行环境

- Python 3.12+，包管理用 uv（非 pip）
- Node.js + pnpm
- Redis 必须运行（任务队列和 WebSocket 广播）
- 后端虚拟环境路径：`backend/.venv/`
