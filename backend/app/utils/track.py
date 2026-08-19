"""LLM 调用的轻量埋点。"""

from app.utils.log_util import logger


def log_agent_call(agent_name: str, success: bool = True) -> None:
    """记录一次 Agent 模型调用的成败。

    Args:
        agent_name: 发起调用的 Agent 名称。
        success: 调用是否成功返回。
    """
    outcome = "OK" if success else "FAIL"
    logger.info("llm-call | agent={} | result={}", agent_name, outcome)
