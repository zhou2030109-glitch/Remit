"""全局配置：环境变量 → 强类型 Settings。

字段名即环境变量名，是用户配置文件的稳定契约，
新增字段注意同步 docs/configuration.md 与 .env.example。
"""

import os
from enum import Enum
from typing import Annotated

from pydantic import BeforeValidator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ApiType(str, Enum):
    """模型接入协议类型。"""

    OPENAI_CHAT = "openai-chat"
    OPENAI_RESPONSES = "openai-responses"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"


def parse_cors(value: str) -> list[str]:
    """解析 CORS 配置：``*`` 或逗号分隔的来源列表。"""
    if value == "*":
        return ["*"]
    if "," in value:
        return [origin.strip() for origin in value.split(",")]
    return [value]


class Settings(BaseSettings):
    """进程级配置单例，从环境变量与 .env 文件装配。"""

    ENV: str = "dev"
    REVIEW_MODEL: str | None = None

    # ---- 推理档位：每 Agent 独立，缺省回落全局档 ----
    MODEL_REASONING_EFFORT: str | None = None
    COORDINATOR_REASONING_EFFORT: str | None = None
    MODELER_REASONING_EFFORT: str | None = None
    CODER_REASONING_EFFORT: str | None = None
    WRITER_REASONING_EFFORT: str | None = None

    # ---- 备用模型：主模型持续失败时接管；不配置则保持原行为 ----
    FALLBACK_API_TYPE: ApiType | None = None
    FALLBACK_API_KEY: str | None = None
    FALLBACK_MODEL: str | None = None
    FALLBACK_BASE_URL: str | None = None
    FALLBACK_REASONING_EFFORT: str | None = None

    DISABLE_RESPONSE_STORAGE: bool = True
    API_TIMEOUT_SECONDS: float = 600.0

    # ---- 四个核心 Agent 的独立模型接入 ----
    COORDINATOR_API_TYPE: ApiType | None = None
    COORDINATOR_API_KEY: str | None = None
    COORDINATOR_MODEL: str | None = None
    COORDINATOR_BASE_URL: str | None = None
    COORDINATOR_MAX_TOKENS: int | None = None
    COORDINATOR_CONTEXT_WINDOW: int = 128000

    MODELER_API_TYPE: ApiType | None = None
    MODELER_API_KEY: str | None = None
    MODELER_MODEL: str | None = None
    MODELER_BASE_URL: str | None = None
    MODELER_MAX_TOKENS: int | None = None
    MODELER_CONTEXT_WINDOW: int = 128000

    CODER_API_TYPE: ApiType | None = None
    CODER_API_KEY: str | None = None
    CODER_MODEL: str | None = None
    CODER_BASE_URL: str | None = None
    CODER_MAX_TOKENS: int | None = None
    CODER_CONTEXT_WINDOW: int = 128000

    WRITER_API_TYPE: ApiType | None = None
    WRITER_API_KEY: str | None = None
    WRITER_MODEL: str | None = None
    WRITER_BASE_URL: str | None = None
    WRITER_MAX_TOKENS: int | None = None
    WRITER_CONTEXT_WINDOW: int = 128000

    # ---- 赛题 PDF 多模态识图；VISION_* 留空时复用协调者接入 ----
    PDF_VISION_ENABLED: bool = True
    PDF_VISION_MAX_FIGURES: int = 12
    VISION_API_TYPE: ApiType | None = None
    VISION_API_KEY: str | None = None
    VISION_MODEL: str | None = None
    VISION_BASE_URL: str | None = None
    VISION_MAX_TOKENS: int | None = 8192

    # ---- 模型评审组：独立探索者 + 匿名盲审者 ----
    MODEL_COUNCIL_ENABLED: bool = False

    MODEL_SCOUT_API_TYPE: ApiType | None = None
    MODEL_SCOUT_API_KEY: str | None = None
    MODEL_SCOUT_MODEL: str | None = None
    MODEL_SCOUT_BASE_URL: str | None = None
    MODEL_SCOUT_MAX_TOKENS: int | None = 16384
    MODEL_SCOUT_CONTEXT_WINDOW: int = 262144

    MODEL_CRITIC_API_TYPE: ApiType | None = None
    MODEL_CRITIC_API_KEY: str | None = None
    MODEL_CRITIC_MODEL: str | None = None
    MODEL_CRITIC_BASE_URL: str | None = None
    MODEL_CRITIC_MAX_TOKENS: int | None = 16384
    MODEL_CRITIC_CONTEXT_WINDOW: int = 262144

    # ---- 运行控制 ----
    MAX_CHAT_TURNS: int | None = None
    MAX_RETRIES: int | None = None
    GATEWAY_MAX_RETRIES: int = 12
    E2B_API_KEY: str | None = None
    CODE_EXECUTION_BACKEND: str = "matlab"
    MATLAB_EXECUTABLE: str | None = None
    MATLAB_STARTUP_TIMEOUT_SECONDS: float = 90.0
    MATLAB_EXECUTION_TIMEOUT_SECONDS: float = 3000.0
    MATLAB_FALLBACK_TO_PYTHON: bool = True
    LOG_LEVEL: str = "DEBUG"
    DEBUG: bool = True
    REDIS_URL: str = "redis://redis:6379/0"
    REDIS_MAX_CONNECTIONS: int = 32
    CORS_ALLOW_ORIGINS: Annotated[list[str] | str, BeforeValidator(parse_cors)] = "*"
    SERVER_HOST: str = "http://localhost:18000"
    DEEPSEEK_MODEL: str | None = None
    DEEPSEEK_BASE_URL: str | None = None

    # ---- 文献检索（OpenAlex） ----
    OPENALEX_EMAIL: str | None = None
    OPENALEX_API_KEY: str | None = None

    # ---- Web 搜索（Tavily） ----
    TAVILY_API_KEY: str | None = None
    SEARCH_CACHE_TTL: int = 86400
    SEARCH_ENABLED: bool = False

    # ---- RAG 知识库 ----
    RAG_ENABLED: bool = False
    RAG_DB_PATH: str = "data/chromadb"
    RAG_TOP_K: int = 5
    RAG_EMBEDDING_MODEL: str = "BAAI/bge-m3"
    RAG_RERANKER_MODEL: str = "BAAI/bge-reranker-v2-m3"

    # ---- 三级建模方法库：离线确定性检索 ----
    METHOD_RETRIEVAL_ENABLED: bool = True
    METHOD_RETRIEVAL_TOP_K: int = 6
    METHOD_LIBRARY_PATH: str | None = None

    # ---- 人机协作审批 ----
    HIL_ENABLED: bool = True
    HIL_TIMEOUT: int = 300
    HIL_CHECKPOINTS: dict = {
        "problem_split": True,
        "model_selection": True,
        "code_review": False,
        "paper_review": True,
    }

    model_config = SettingsConfigDict(
        env_file=(".env.dev", ".env.council"),
        env_file_encoding="utf-8",
        extra="allow",
    )

    @classmethod
    def from_env(cls, env: str | None = None) -> "Settings":
        """按环境名加载 ``.env.<env>``；缺省读 ENV 环境变量。"""
        env_name = env or os.getenv("ENV", "dev")
        return cls(
            _env_file=(f".env.{env_name.lower()}", ".env.council"),
            _env_file_encoding="utf-8",
        )  # type: ignore[call-arg]


settings = Settings()
