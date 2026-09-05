"""全局配置：环境变量 → 强类型 Settings。

字段名即环境变量名，是用户配置文件的稳定契约，
新增字段注意同步 docs/configuration.md 与 .env.example。
"""

import os
from enum import Enum
from pathlib import Path
from typing import Annotated

from pydantic import BeforeValidator, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_ROOT = Path(__file__).resolve().parents[2]
# 容器需要挂载配置目录，使保存时的原子替换发生在同一文件系统。
# 在启动时统一解析路径，设置加载与界面保存必须使用同一个位置。
_USER_CONFIG_OVERRIDE = os.getenv("REMIT_USER_CONFIG_PATH", "").strip()
USER_CONFIG_PATH = (
    Path(_USER_CONFIG_OVERRIDE).expanduser().resolve()
    if _USER_CONFIG_OVERRIDE
    else BACKEND_ROOT / ".env.user"
)


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
    API_TIMEOUT_SECONDS: float = 180.0
    # 即使旧配置仍写着 600 秒，也不能让一次坏请求占住工作流十分钟。
    API_HARD_TIMEOUT_SECONDS: float = 180.0

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
    MODEL_COUNCIL_REQUIRE_DIVERSE_BACKENDS: bool = True
    MODEL_COUNCIL_CRITIC_TIMEOUT_SECONDS: float = 180.0
    MODEL_COUNCIL_FALLBACK_TIMEOUT_SECONDS: float = 180.0

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
    MAX_CHAT_TURNS: int | None = 20
    MAX_CODE_EXECUTIONS_PER_RUN: int = 12
    MAX_RETRIES: int | None = 3
    GATEWAY_MAX_RETRIES: int = 4
    LLM_HARD_RETRY_LIMIT: int = 4
    LLM_RETRY_AFTER_MAX_SECONDS: float = 60.0
    E2B_API_KEY: str | None = None
    CODE_EXECUTION_BACKEND: str = "matlab"
    MATLAB_EXECUTABLE: str | None = None
    MATLAB_STARTUP_TIMEOUT_SECONDS: float = 90.0
    MATLAB_EXECUTION_TIMEOUT_SECONDS: float = 300.0
    MATLAB_FALLBACK_TO_PYTHON: bool = True
    PYTHON_EXECUTION_TIMEOUT_SECONDS: float = 300.0
    CODE_EXECUTION_HARD_LIMIT_SECONDS: float = 300.0
    CODE_EXECUTION_HEARTBEAT_SECONDS: float = 15.0
    CODE_EXECUTION_CANCEL_GRACE_SECONDS: float = 10.0
    CODE_COMPLEXITY_GUARD_ENABLED: bool = True
    CODE_LITERAL_LOOP_ITERATION_LIMIT: int = 2_000_000
    LATEX_ENGINE: str = "xelatex"
    LATEX_COMPILE_TIMEOUT_SECONDS: float = 120.0
    PAPER_MIN_PDF_PAGES: int = 8
    TASK_TIMEOUT_SECONDS: float = 7200.0
    TASK_AUTO_RESUME_LIMIT: int = 1
    TASK_AUTO_RESUME_BASE_DELAY_SECONDS: int = 30
    LOG_LEVEL: str = "DEBUG"
    DEBUG: bool = True
    REDIS_URL: str = "redis://redis:6379/0"
    REDIS_MAX_CONNECTIONS: int = 32
    UPLOAD_MAX_FILE_BYTES: int = Field(default=128 * 1024 * 1024, gt=0)
    UPLOAD_MAX_TOTAL_BYTES: int = Field(default=512 * 1024 * 1024, gt=0)
    UPLOAD_MAX_FILES: int = Field(default=100, gt=0)
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

    # ---- 三级建模方法库：离线确定性检索 ----
    METHOD_RETRIEVAL_ENABLED: bool = True
    METHOD_RETRIEVAL_TOP_K: int = 6
    METHOD_LIBRARY_PATH: str | None = None

    # ---- 人机协作审批 ----
    HIL_ENABLED: bool = True
    HIL_TIMEOUT: int = 300
    HIL_CHECKPOINTS: dict[str, bool] = {
        "problem_split": True,
        "model_selection": True,
        "code_review": False,
        "paper_review": True,
    }

    model_config = SettingsConfigDict(
        env_file=(
            str(BACKEND_ROOT / ".env.dev"),
            str(BACKEND_ROOT / ".env.council"),
            str(USER_CONFIG_PATH),
        ),
        env_file_encoding="utf-8",
        extra="allow",
    )

    @classmethod
    def from_env(cls, env: str | None = None) -> "Settings":
        """按环境名加载 ``.env.<env>``；缺省读 ENV 环境变量。"""
        env_name = env or os.getenv("ENV", "dev")
        return cls(
            _env_file=(
                str(BACKEND_ROOT / f".env.{env_name.lower()}"),
                str(BACKEND_ROOT / ".env.council"),
                str(USER_CONFIG_PATH),
            ),
            _env_file_encoding="utf-8",
        )  # type: ignore[call-arg]


settings = Settings()


def effective_api_timeout_seconds() -> float:
    """兼容旧配置，同时执行不可绕过的单请求超时上限。"""
    return max(
        1.0,
        min(float(settings.API_TIMEOUT_SECONDS), float(settings.API_HARD_TIMEOUT_SECONDS)),
    )
