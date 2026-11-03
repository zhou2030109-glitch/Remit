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
<PREFIX>_MAX_TOKENS=32768
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

核心角色的结构化输出默认上限为 32768 token；`.env.example` 也使用该值。
已有用户配置会优先生效，可通过 `COORDINATOR_MAX_TOKENS`、`MODELER_MAX_TOKENS`、
`CODER_MAX_TOKENS`、`WRITER_MAX_TOKENS` 分别调整。截断恢复最多扩到 65536，
仍受最终请求上下文预算及供应商支持范围约束。更大上限不能保证供应商永不截断。

```dotenv
API_TIMEOUT_SECONDS=180
API_HARD_TIMEOUT_SECONDS=180
MAX_RETRIES=3
GATEWAY_MAX_RETRIES=4
LLM_HARD_RETRY_LIMIT=4
LLM_RETRY_AFTER_MAX_SECONDS=60
MAX_CHAT_TURNS=20
MAX_CODE_EXECUTIONS_PER_RUN=12
MAX_CODE_EXECUTIONS_PER_STAGE=48
LLM_STAGE_CALL_LIMIT=60
LLM_STAGE_API_SECONDS=2400
TASK_TIMEOUT_SECONDS=7200
TASK_AUTO_RESUME_LIMIT=1
TASK_AUTO_RESUME_BASE_DELAY_SECONDS=30
```

LLM 层独占网络重试权；Coder 不会在其外层再次重放同一请求。整任务自动续跑
只针对网络/供应商瞬断，计算超时、复杂度拒绝和质量门失败不会原样自动重跑。

模型阶段额度按已观测 API 等待时间和尝试次数累计，恢复与重启不清零。
候选实验 `pilot` 包含多个小问：保存的实验协议与当前小问完全对应时，
该复合阶段的总次数和等待额度乘以小问数；例如四问为 96 次、3600 秒。
协议未形成或不匹配时仍使用基础额度，其他阶段不变。既有调用仍在原账本
计入消耗，不会重新获得一份完整额度。达到上限时显示实际已用值与上限，
不会把额度耗尽误报为密钥或模型能力错误。

代码执行另有持久化的阶段上限，默认 48 次，为单轮 12 次及有限返修预留空间。
同一阶段的自动返修、停止后恢复和进程重启继续累计；用户明确退回修改，或从
已完成节点重新执行时，只为失效节点及其下游开启新额度。不同小问分别计数，
探索阶段的多个子实验共享该阶段额度。达到上限后保留成果并显示原因，普通恢复
不会重置次数；如需继续，可在确认工作范围后调整配置或退回修改。

执行前在项目已有 `.calls.sqlite3` 原子预占。失败、超时及预占后被取消或崩溃的
尝试不退还，以免未知结果绕过限制；预占次数不等于成功执行次数。账本损坏或
不可写时停止执行，不静默清零。旧项目没有历史记录时，从升级后首次使用开始
累计，历史次数未知；无注册项目的独立环境探针仍使用自身的单轮限制。

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
CORS_ALLOW_ORIGINS=http://localhost:15173,http://127.0.0.1:15173
SERVER_HOST=http://localhost:18000
```


## 附件上传限制

```dotenv
UPLOAD_MAX_FILE_BYTES=134217728
UPLOAD_MAX_TOTAL_BYTES=536870912
UPLOAD_MAX_FILES=100
```

默认单文件不超过 128 MiB、每次提交总计不超过 512 MiB、附件不超过 100 个。
上传采用分块暂存，整批通过校验后才写入任务目录；同名附件（不区分大小写）、
系统保留名和工作流内部文件名会被拒绝，已有文件不会被覆盖。

源码启动默认将消息保存在 `backend/logs/messages/messages.sqlite3`。安装版启动器将
消息、项目和设置放在 `%LOCALAPPDATA%\Remit\data` 下；自定义 `REMIT_DATA_DIR`
时使用指定目录。启动器通过进程环境变量 `REMIT_MESSAGES_DIR` 把消息路径传给后端，
无需手工修改 `.env`。

首次升级会复制旧 `backend/logs/messages`：SQLite 使用在线备份，包含已提交的 WAL 数据；
旧 JSON 文件也保留。消息序号保持不变，原安装中的档案不删除。若用户目录已存在非空
消息档案，以已有档案为准，不合并另一份数据库的序号；旧安装档案仍可另行恢复。
迁移失败保留暂存副本且不写成功标记；日志会给出待核对目录。

首次访问旧 JSON 任务时在事务中导入 `<task-id>.json`，保留原文件，之后以 SQLite 为准。
备份前应停止 Remit，并一起备份数据目录中的 `logs/messages/` 与 `project/`。
源码启动则备份 `backend/logs/messages/` 与 `backend/project/`。
不要在服务运行时仅复制数据库主文件而遗漏 WAL。损坏的旧 JSON 不影响其他任务，日志会说明跳过原因。

## 密钥安全

- 不要把真实密钥写入示例文件、README、截图或测试。
- 不要提交 `.env.dev` 与 `.env.council`。
- 前端运行时配置只保存在当前页面内存和后端进程内存中。
- 密钥轮换后重启后端，并通过配置状态接口确认所有已启用的 Agent 均已配置。


### 阶段执行次数耗尽后的恢复

任务页会显示本阶段已记录次数与上限。点击“查看执行次数并继续”仅查询记录；若确实用完，可填写 1–48 的整数追加量，确认后继续当前未完成阶段。已有剩余次数时直接恢复，不重复追加。失败或中断后的预占仍计入，旧历史次数不会被补猜。

确认保存阶段标识、已用次数和原上限；其间记录变化会拒绝旧确认，需重新查看。同一确认编号只追加一次，也不会重复排入已经提交的任务。若进程恰在持久化提交标记后、实际启动前退出，启动结果可能未知；重新查看剩余次数后可明确恢复，不必再加额度。此机制不承诺跨崩溃恰好执行一次，也不替代成果审核。


### 备用模型的配置与验证

在模型设置中启用“备用模型”，填写连接、自己的上下文容量和单次输出上限，保存后在能力验证中选择“备用模型连接”及需要接管的角色。各角色检查范围沿用现有约定；含图片的请求还需要通过“赛题识图”的备用验证，完全相同配置的识图档案可供其他角色使用。保存配置后界面重新读取档案，连接、容量、输出上限或推理参数变化都会使旧档案失效。

主模型暂时故障达到重试上限后，只有相同服务地址与协议内、能力已验证且本次完整请求能容纳的备用连接才会接管。能力未知或失败、上下文过小、显式输出要求超过备用上限时会停止并保留现有成果，不会沿用主模型的验证结果，也不会偷偷缩短输出要求。认证和确定性参数错误仍直接报错。

`FALLBACK_ENABLED` 控制启用；旧配置默认仍允许尝试备用切换，但必须先补做对应角色的能力验证。`FALLBACK_CONTEXT_WINDOW` 默认 128000，`FALLBACK_MAX_TOKENS` 默认 8192；二者可在界面修改。未填 `FALLBACK_API_TYPE` 或 `FALLBACK_BASE_URL` 时沿用当前角色连接，因此不同角色可能需要分别验证。停用开关保留连接信息，重启后仍保持停用状态。
