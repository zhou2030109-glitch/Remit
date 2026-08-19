"""按配置装配各角色 LLM 实例的工厂。"""

from collections.abc import Callable

from app.config.setting import Settings, settings
from app.core.llm.llm import LLM


def _agent_llm(cfg: Settings, role: str, task_id: str) -> LLM:
    """按角色前缀（COORDINATOR 等）读取一组配置并实例化。"""
    return LLM(
        api_type=getattr(cfg, f"{role}_API_TYPE"),
        api_key=getattr(cfg, f"{role}_API_KEY"),
        model=getattr(cfg, f"{role}_MODEL"),
        base_url=getattr(cfg, f"{role}_BASE_URL"),
        task_id=task_id,
        max_tokens=getattr(cfg, f"{role}_MAX_TOKENS"),
        reasoning_effort=getattr(cfg, f"{role}_REASONING_EFFORT", None),
    )


class LLMFactory:
    """为一个任务创建全部角色共享 task_id 的 LLM 集合。"""

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id

    def get_all_llms(self) -> tuple[LLM, LLM, LLM, LLM]:
        """返回 ``(协调, 建模, 编码, 写作)`` 四个角色的 LLM。"""
        return (
            _agent_llm(settings, "COORDINATOR", self.task_id),
            _agent_llm(settings, "MODELER", self.task_id),
            _agent_llm(settings, "CODER", self.task_id),
            _agent_llm(settings, "WRITER", self.task_id),
        )

    def get_vision_llm(self) -> LLM:
        """识图模型；VISION_* 未配置时复用协调者接入。

        Raises:
            ValueError: 协调者与 VISION_* 都没有可用配置。
        """
        pick: Callable[[str], object] = lambda name: (  # noqa: E731
            getattr(settings, f"VISION_{name}", None)
            or getattr(settings, f"COORDINATOR_{name}", None)
        )
        if not pick("API_KEY") or not pick("MODEL"):
            raise ValueError("识图未配置模型：请填写 VISION_* 或 COORDINATOR_* 配置")
        return LLM(
            api_type=pick("API_TYPE"),  # type: ignore[arg-type]
            api_key=pick("API_KEY"),  # type: ignore[arg-type]
            model=pick("MODEL"),  # type: ignore[arg-type]
            base_url=pick("BASE_URL"),  # type: ignore[arg-type]
            task_id=self.task_id,
            max_tokens=settings.VISION_MAX_TOKENS,
        )

    def get_model_council_llms(self) -> tuple[LLM, LLM]:
        """返回评审组使用的 ``(探索者, 盲审者)`` LLM。

        Raises:
            ValueError: 已启用评审组但配置不完整。
        """
        missing = [
            f"{role}_{field}"
            for role in ("MODEL_SCOUT", "MODEL_CRITIC")
            for field in ("API_TYPE", "API_KEY", "MODEL")
            if not getattr(settings, f"{role}_{field}")
        ]
        if missing:
            raise ValueError(
                "已启用模型评审组，但配置不完整：" + "、".join(missing)
            )
        return (
            _agent_llm(settings, "MODEL_SCOUT", self.task_id),
            _agent_llm(settings, "MODEL_CRITIC", self.task_id),
        )
