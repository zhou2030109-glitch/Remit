# Remit 配置

后端按顺序读取 `backend/.env.dev`、`backend/.env.council` 和
`backend/.env.user`。界面“验证并保存”或“保存”成功后会原子写入
`.env.user`，因此后端重启后密钥仍然生效。该文件包含本机 API 凭据，
已被 Git 忽略，请勿分享或提交。手工修改环境文件后需要重启后端。

可在后端进程启动前设置 `REMIT_USER_CONFIG_PATH`，指定用户配置文件的绝对路径；
未设置或留空时仍使用 `backend/.env.user`。界面保存、默认配置加载和
`Settings.from_env()` 均使用这个路径。Docker 可设置
`REMIT_USER_CONFIG_PATH=/app/config/.env.user` 并持久化整个 `/app/config` 目录。
请勿只绑定挂载 `.env.user` 单个文件：保存会先写同目录临时文件，再原子替换目标文件。
这个路径选项需要通过进程环境变量传入，不能写在待加载的 `.env.user` 内。

## 核心模型

四个核心前缀为：

- `COORDINATOR`
- `MODELER`
- `CODER`
- `WRITER`

每个前缀支持：

```dotenv
<PREFIX>_API_TYPE=openai-responses
<PREFIX>_API_KEY=your-key
<PREFIX>_MODEL=your-model
<PREFIX>_BASE_URL=https://your-provider.example/
<PREFIX>_MAX_TOKENS=8192
<PREFIX>_CONTEXT_WINDOW=128000
<PREFIX>_REASONING_EFFORT=high
```

`API_TYPE` 可用值为 `openai-chat`、`openai-responses`、`anthropic` 和 `gemini`。推理强度由具体供应商决定是否支持；题目理解建议将 `COORDINATOR_REASONING_EFFORT` 设为 `high`。

协调器需要同时转录原题并生成逐问结构化分析，建议单独设置
`COORDINATOR_MAX_TOKENS=32768`。程序检测到供应商因输出上限截断时，
会自动扩大本次重试预算（最高 65536），其他角色仍可按任务需要配置。

## 模型评审组

启用 `MODEL_COUNCIL_ENABLED=true` 后，还需要完整配置：

- `MODEL_SCOUT_*`
- `MODEL_CRITIC_*`

两者应使用不同供应商或模型。默认
`MODEL_COUNCIL_REQUIRE_DIVERSE_BACKENDS=true`；如果 Scout、Critic 与主建模手
三者是完全相同的接入点，系统会跳过额外评审调用，避免同一故障域重复计费和重试。
单次 Critic 与 fallback 的超时分别由
`MODEL_COUNCIL_CRITIC_TIMEOUT_SECONDS`、
`MODEL_COUNCIL_FALLBACK_TIMEOUT_SECONDS` 控制，默认均为 180 秒。

## 三级方法检索

Remit 默认在 Modeler 开始前，按“领域 → 子领域 → 具体方法”对每个正式小问独立检索，并返回带适用前提、失败模式、验证建议和分项得分的 Top-K 候选：

```dotenv
METHOD_RETRIEVAL_ENABLED=true
METHOD_RETRIEVAL_TOP_K=6
METHOD_LIBRARY_PATH=
```

`METHOD_LIBRARY_PATH` 留空时使用 Remit 内置方法库。该功能是离线、确定性的，不依赖向量数据库，也不会额外调用模型或外部服务。结果写入当前任务目录的 `method_recommendations.json`，同时交给主 Modeler、独立 Scout 和人工模型选择审批。详细结构见[三级方法检索](./method-retrieval.md)。

## 执行环境

```dotenv
CODE_EXECUTION_BACKEND=matlab
MATLAB_EXECUTABLE=
MATLAB_STARTUP_TIMEOUT_SECONDS=90
MATLAB_EXECUTION_TIMEOUT_SECONDS=300
MATLAB_FALLBACK_TO_PYTHON=true
PYTHON_EXECUTION_TIMEOUT_SECONDS=300
CODE_EXECUTION_HARD_LIMIT_SECONDS=300
CODE_EXECUTION_HEARTBEAT_SECONDS=15
CODE_EXECUTION_CANCEL_GRACE_SECONDS=10
CODE_COMPLEXITY_GUARD_ENABLED=true
CODE_LITERAL_LOOP_ITERATION_LIMIT=2000000
LATEX_ENGINE=xelatex
LATEX_COMPILE_TIMEOUT_SECONDS=120
PAPER_MIN_PDF_PAGES=8
```

`MATLAB_EXECUTABLE` 留空时会从 PATH 和常见安装目录查找。
MATLAB 与本地 Python 都受 `CODE_EXECUTION_HARD_LIMIT_SECONDS` 硬上限约束；
运行期间会定期发送心跳。Python 在工作线程执行，超时后重启 Jupyter 内核；
MATLAB 超时后退出并在下一次调用重建 Engine。复杂度保护器会拒绝无界循环、
阶乘级全排列，以及“元启发式搜索 × 重复实验 × O(n²) 成对校验”等高置信度失控模式。

终稿固定交付 `res.tex` 与由该文件编译得到的 `res.pdf`，不再交付 Markdown
或 DOCX。运行环境必须提供 XeLaTeX（Windows 推荐 MiKTeX，macOS/Linux 推荐
TeX Live）；编译需连续两次成功，PDF 还会接受纸型、空白页、越界文本、可提取
正文和抽样渲染检查。`PAPER_MIN_PDF_PAGES` 控制最低正文页数。

## 运行与重试预算

```dotenv
API_TIMEOUT_SECONDS=180
API_HARD_TIMEOUT_SECONDS=180
MAX_RETRIES=3
GATEWAY_MAX_RETRIES=4
LLM_HARD_RETRY_LIMIT=4
LLM_RETRY_AFTER_MAX_SECONDS=60
MAX_CHAT_TURNS=20
MAX_CODE_EXECUTIONS_PER_RUN=12
TASK_TIMEOUT_SECONDS=7200
TASK_AUTO_RESUME_LIMIT=1
TASK_AUTO_RESUME_BASE_DELAY_SECONDS=30
```

LLM 层独占网络重试权；Coder 不会在其外层再次重放同一请求。整任务自动续跑
只针对网络/供应商瞬断，计算超时、复杂度拒绝和质量门失败不会原样自动重跑。

## 可选服务

```dotenv
TAVILY_API_KEY=
OPENALEX_EMAIL=
OPENALEX_API_KEY=
E2B_API_KEY=
PDF_VISION_ENABLED=true
PDF_VISION_MAX_FIGURES=12
VISION_API_TYPE=
VISION_API_KEY=
VISION_MODEL=
VISION_BASE_URL=
VISION_MAX_TOKENS=8192
```

未启用的服务不影响核心建模流程。

`OPENALEX_EMAIL` 是文献检索与全文抓取的必需配置：OpenAlex 检索用邮箱获取更
宽松的限流，Unpaywall 反查开放获取全文也要求邮箱，未配置时全文抓取会如实降级
为“仅摘要”方法卡。`PDF_VISION_*` 控制赛题 PDF 识图；留空时自动复用协调者的
模型与中转，识图失败只降级为纯文本导入，不阻断赛题解析。

## 本地服务

```dotenv
REDIS_URL=redis://localhost:16379/0
CORS_ALLOW_ORIGINS=http://localhost:15173,http://127.0.0.1:15173
SERVER_HOST=http://localhost:18000
```

Docker 环境中的 Redis 地址应使用 `redis://redis:6379/0`。

## 附件上传限制

```dotenv
UPLOAD_MAX_FILE_BYTES=134217728
UPLOAD_MAX_TOTAL_BYTES=536870912
UPLOAD_MAX_FILES=100
```

默认单文件不超过 128 MiB、每次提交总计不超过 512 MiB、附件不超过 100 个。
上传采用分块暂存，整批通过校验后才写入任务目录；同名附件（不区分大小写）、
系统保留名和工作流内部文件名会被拒绝，已有文件不会被覆盖。

消息历史位于 `backend/logs/messages/messages.sqlite3`。首次访问旧任务时会在事务中
导入原来的 `<task-id>.json`，保留原文件，之后以 SQLite 为准。备份时应停止 Remit 后
一起备份 `backend/logs/messages/` 和 `backend/project/work_dir/`；不要只复制数据库
主文件而遗漏仍在使用的 WAL 文件。损坏的旧 JSON 不影响其他任务，日志会说明跳过原因。

## 密钥安全

- 不要把真实密钥写入示例文件、README、截图或测试。
- 不要提交 `.env.dev` 与 `.env.council`。
- 前端运行时配置只保存在当前页面内存和后端进程内存中。
- 密钥轮换后重启后端，并通过配置状态接口确认所有已启用的 Agent 均已配置。
