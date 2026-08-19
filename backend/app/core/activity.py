"""实时活动播报：向前端推送"现在在干嘛"的轻量状态行，失败绝不阻断主流程。"""

from typing import Literal

from app.schemas.response import ActivityMessage
from app.services.redis_manager import redis_manager
from app.utils.log_util import logger

AGENT_LABELS = {
    "CoordinatorAgent": "协调手",
    "ModelerAgent": "建模手",
    "CoderAgent": "代码手",
    "WriterAgent": "论文手",
}

ActivityCategory = Literal["llm", "code", "gate", "repair", "info"]


async def publish_activity(
    task_id: str,
    text: str,
    *,
    category: ActivityCategory = "info",
    detail: str = "",
) -> None:
    """推送一条活动播报；同任务固定 id，前端原位刷新。"""
    if not task_id:
        return
    try:
        await redis_manager.publish_message(
            task_id,
            ActivityMessage(
                id=f"activity:{task_id}",
                content=text,
                category=category,
                detail=detail,
            ),
        )
    except Exception as exc:
        logger.debug(f"活动播报失败: {exc}")
