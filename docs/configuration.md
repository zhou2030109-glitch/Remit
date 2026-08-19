# Remit 配置

后端默认读取 `backend/.env.dev`，模型评审组的独立配置可放在 `backend/.env.council`。修改环境文件后需要重启后端。

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

## 模型评审组

启用 `MODEL_COUNCIL_ENABLED=true` 后，还需要完整配置：

- `MODEL_SCOUT_*`
- `MODEL_CRITIC_*`

两者可以使用不同供应商与模型。

## 三级方法检索

Remit 默认在 Modeler 开始前，按“领域 → 子领域 → 具体方法”对每个正式小问独立检索，并返回带适用前提、失败模式、验证建议和分项得分的 Top-K 候选：

```dotenv
METHOD_RETRIEVAL_ENABLED=true
METHOD_RETRIEVAL_TOP_K=6
METHOD_LIBRARY_PATH=
```

`METHOD_LIBRARY_PATH` 留空时使用 Remit 内置方法库。该功能是离线、确定性的，不依赖 `RAG_ENABLED`，也不会额外调用模型或外部服务。结果写入当前任务目录的 `method_recommendations.json`，同时交给主 Modeler、独立 Scout 和人工模型选择审批。详细结构见[三级方法检索](./method-retrieval.md)。

## 执行环境

```dotenv
CODE_EXECUTION_BACKEND=matlab
MATLAB_EXECUTABLE=
MATLAB_STARTUP_TIMEOUT_SECONDS=90
MATLAB_EXECUTION_TIMEOUT_SECONDS=3000
MATLAB_FALLBACK_TO_PYTHON=true
```

`MATLAB_EXECUTABLE` 留空时会从 PATH 和常见安装目录查找。

## 可选服务

```dotenv
SEARCH_ENABLED=false
TAVILY_API_KEY=
OPENALEX_EMAIL=
OPENALEX_API_KEY=
E2B_API_KEY=
RAG_ENABLED=false
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

## 密钥安全

- 不要把真实密钥写入示例文件、README、截图或测试。
- 不要提交 `.env.dev` 与 `.env.council`。
- 前端运行时配置只保存在当前页面内存和后端进程内存中。
- 密钥轮换后重启后端，并通过配置状态接口确认所有已启用的 Agent 均已配置。
