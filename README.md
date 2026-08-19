# Remit

<p align="center">
  <img src="./assets/remit-icon.png" alt="Remit 标志" width="160" />
</p>

Remit 是一个本地优先的数学建模工作台。它把赛题分析、模型设计、代码执行、结果验证和论文写作组织成可检查、可恢复的多智能体工作流。

## 核心能力

- 四角色协作：协调、建模、编码和写作 Agent 分阶段交付。
- 多模型接入：每个 Agent 可独立配置 API 类型、模型、密钥与中转地址。
- 本地执行：支持 MATLAB，无法使用时可回退到 Python。
- 任务工作区：代码、图表、状态、日志和论文成果按任务保存。
- 中断恢复：支持检查点、继续执行、人工审批和失败重试。
- 层级方法检索：为每个小问按“领域 → 子领域 → 方法”返回可解释 Top-K 候选。
- 文献全文精读与方法卡：按小问筛选论文、抓取开放获取全文、提取可落地方法。
- 赛题 PDF 识图：插图、坐标图与扫描页由多模态模型转成文字，与正文同等参与建模。
- 可选增强：模型评审组、Tavily 搜索、OpenAlex、RAG 与 E2B。

## 工作流

```text
题目输入
  → 原题忠实提取（源字段只读）
  → Coordinator 逐题结构化初步分析
  → 附件数据侦察与文献调研（全文精读 + 方法卡）
  → 基于真实证据校正题目理解并人工审批
  → Remit 方法库逐题检索 Top-K
  → Modeler 设计与修订模型
  → Coder 编写、执行并验证代码
  → Writer 汇总证据并生成论文
  → 人工验收与成果导出
```

更详细的模块说明见 [架构文档](./docs/architecture.md)。

## 快速启动

### Windows 桌面模式

1. 将 `backend/.env.example` 复制为 `backend/.env.dev`，填写模型配置。
2. 确保 `backend/.venv` 和 `frontend/node_modules` 已准备好。
3. 双击 `win_start.bat`。
4. 浏览器模式访问 <http://127.0.0.1:15173>；后端状态位于 <http://127.0.0.1:18000>。

启动器会自动启动本地 Redis、FastAPI 后端和 Vue 前端。使用 `win_stop.bat` 可停止服务。

### 从源码运行

```powershell
cd backend
uv sync

cd ..\frontend
pnpm install

cd ..
.\win_start.bat
```

### Docker

```bash
docker compose up --build
```

Docker Compose 从 `backend/.env.dev` 加载后端配置。

## 打包发布（Windows 安装包）

一键生成可分发的桌面安装包：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\package_win.ps1
```

要点：

- **输入**：`backend/.venv`（Python 3.13 虚拟环境，含全部依赖）、`frontend/node_modules`、基础 Python 3.13（`%LocalAppData%\Programs\Python\Python313`）、`tools/redis`（捆绑的 Windows 二进制）。
- **产物**：`E:\codex\remit-build\output\RemitSetup.exe`（Inno Setup；可通过脚本参数 `-BuildRoot` 修改输出目录）。
- 安装包内置 Python 运行时与 Redis，前端以静态文件由后端直接托管（FastAPI SPA 回退），目标电脑**无需安装 Node、Python**。
- **MATLAB**：默认 `CODE_EXECUTION_BACKEND=matlab`、`MATLAB_FALLBACK_TO_PYTHON=true`；目标电脑没有 MATLAB 时自动回退到内置 Python 执行环境，功能完整。
- 安装包**不含模型密钥**：用户首次打开后在界面右上角「API 配置」对话框填写（仅保存在内存，不落盘）。
- 生成的安装包使用固定 GUID，可覆盖升级；卸载时自动停止后台服务。

## 模型配置

四个核心 Agent 使用相同的字段结构：

```dotenv
COORDINATOR_API_TYPE=openai-responses
COORDINATOR_API_KEY=your-key
COORDINATOR_MODEL=your-model
COORDINATOR_BASE_URL=https://your-provider.example/
COORDINATOR_MAX_TOKENS=8192
```

将 `COORDINATOR` 分别替换为 `MODELER`、`CODER`、`WRITER` 即可配置其他 Agent。完整字段、模型评审组和可选服务见 [配置文档](./docs/configuration.md)。

不要提交 `backend/.env.dev` 或 `backend/.env.council`；它们已经列入 `.gitignore`。

## 项目结构

```text
backend/      FastAPI、工作流、Agent、模型调用与执行器
frontend/     Vue 3 工作台
tools/        Windows 桌面壳、服务启动器和本地 Redis
assets/       Remit 品牌资产
docs/         架构与配置文档
tests/        Windows 启动器回归测试
```

运行产生的任务文件位于 `backend/project/work_dir/<task-id>/`，服务日志位于 `logs/`。

## 开发与验证

```powershell
# 后端
cd backend
.\.venv\Scripts\python.exe -m ruff check app
.\.venv\Scripts\python.exe -m pytest tests

# 前端
cd ..\frontend
pnpm run build
npx biome check src

# Windows 启动器
cd ..
.\win_start.bat --check
```

## 数据与安全

- API 密钥由本地环境文件或当前进程内的运行时配置提供。
- 前端不会把模型密钥持久化到浏览器存储。
- 任务目录可能包含题目、数据、生成代码和论文，请按敏感数据处理。
- 当前应用面向可信本机环境；暴露到公网前需要另行增加认证、权限隔离和网络边界。

## 品牌

Remit 名称、图标和产品文案为本项目自有内容。
