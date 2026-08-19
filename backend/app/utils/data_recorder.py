"""运行过程数据落盘：对话历史、原始补全响应与 token 开销。"""

import json
from pathlib import Path
from typing import Any

from app.utils.log_util import logger

# 每 1000 token 的参考价格（元），未列出的模型走默认档位
_PRICE_PER_K: dict[str, dict[str, float]] = {
    "gpt-4-turbo-preview": {"prompt": 0.01, "completion": 0.03},
    "gpt-4": {"prompt": 0.03, "completion": 0.06},
    "gpt-3.5-turbo": {"prompt": 0.0005, "completion": 0.0015},
    "qwen-max-latest": {"prompt": 0.0024, "completion": 0.0096},
}
_DEFAULT_PRICE = {"prompt": 0.0001, "completion": 0.0001}


class DataRecorder:
    """按 Agent 归集会话与用量数据，并同步写入任务目录。"""

    def __init__(self, log_work_dir: str = "") -> None:
        self.log_work_dir = log_work_dir
        self.agents_chat_history: dict[str, list[dict]] = {}
        self.chat_completion: dict[str, list[dict]] = {}
        self.token_usage: dict[str, dict[str, float | int]] = {}
        self.total_cost = 0.0
        self.initialized = True

    # ---- 内部工具 ----

    def _dump(self, payload: dict, file_name: str) -> None:
        """把工作区内的 JSON 快照落盘；目录为空则静默跳过。"""
        if not self.log_work_dir:
            return
        target = Path(self.log_work_dir) / file_name
        try:
            target.write_text(
                json.dumps(payload, ensure_ascii=False, indent=4), encoding="utf-8"
            )
        except OSError as exc:
            logger.error(f"写入 {file_name} 失败: {exc}")

    def _ensure_usage_slot(self, agent_name: str) -> dict[str, float | int]:
        return self.token_usage.setdefault(
            agent_name,
            {
                "completion_tokens": 0,
                "prompt_tokens": 0,
                "total_tokens": 0,
                "chat_count": 0,
                "cost": 0.0,
            },
        )

    # ---- 记录入口 ----

    def append_chat_history(self, msg: dict, agent_name: str) -> None:
        """追加一条 Agent 对话消息并刷新快照。"""
        self.agents_chat_history.setdefault(agent_name, []).append(msg)
        self._dump(self.agents_chat_history, "chat_history.json")

    def append_chat_completion(self, completion: Any, agent_name: str) -> None:
        """登记一次原始补全响应，同时累计 token 与费用。"""
        self.chat_completion.setdefault(agent_name, []).append(
            self.chat_completion_to_dict(completion)
        )
        self.update_token_usage(completion, agent_name)
        self._dump(self.chat_completion, "chat_completion.json")

    def update_token_usage(self, completion: Any, agent_name: str) -> None:
        """按响应中的 usage 字段累加用量与估算费用。"""
        usage = getattr(completion, "usage", None)
        if usage is None:
            return

        slot = self._ensure_usage_slot(agent_name)
        slot["completion_tokens"] += usage.completion_tokens
        slot["prompt_tokens"] += usage.prompt_tokens
        slot["total_tokens"] += usage.total_tokens
        slot["chat_count"] += 1

        cost = self.calculate_cost(
            completion.model, usage.prompt_tokens, usage.completion_tokens
        )
        slot["cost"] += cost
        self.total_cost += cost
        self._dump(self.token_usage, "token_usage.json")

    # ---- 序列化与计价 ----

    def chat_completion_to_dict(self, completion: Any) -> dict:
        """把 OpenAI 风格的 ChatCompletion 对象摊平成可 JSON 化的结构。"""
        choices = []
        for choice in completion.choices:
            message = choice.message
            tool_calls = None
            if hasattr(message, "tool_calls") and message.tool_calls:
                tool_calls = [
                    {
                        "id": tc.id,
                        "type": tc.type,
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in message.tool_calls
                ]
            choices.append(
                {
                    "index": choice.index,
                    "message": {
                        "role": message.role,
                        "content": message.content,
                        "tool_calls": tool_calls,
                    },
                    "finish_reason": choice.finish_reason,
                }
            )

        usage = getattr(completion, "usage", None)
        return {
            "id": completion.id,
            "choices": choices,
            "created": completion.created,
            "model": completion.model,
            "usage": (
                {
                    "completion_tokens": usage.completion_tokens,
                    "prompt_tokens": usage.prompt_tokens,
                    "total_tokens": usage.total_tokens,
                }
                if usage
                else None
            ),
            "system_fingerprint": getattr(completion, "system_fingerprint", None),
        }

    def calculate_cost(
        self, model: str, prompt_tokens: int, completion_tokens: int
    ) -> float:
        """按价目表估算单次调用费用（元）。"""
        price = _PRICE_PER_K.get(model, _DEFAULT_PRICE)
        return (prompt_tokens * price["prompt"] + completion_tokens * price["completion"]) / 1000.0

    # ---- 汇总 ----

    def print_summary(self) -> None:
        """在终端打印各 Agent 的 token 与费用汇总表。"""
        from app.utils.RichPrinter import RichPrinter

        headers = ["Agent", "Chats", "Prompt", "Completion", "Total", "Cost ($)"]
        rows = [
            [
                name,
                usage["chat_count"],
                usage["prompt_tokens"],
                usage["completion_tokens"],
                usage["total_tokens"],
                f"{usage['cost']:.4f}",
            ]
            for name, usage in self.token_usage.items()
        ]
        rows.append(
            [
                "TOTAL",
                sum(u["chat_count"] for u in self.token_usage.values()),
                sum(u["prompt_tokens"] for u in self.token_usage.values()),
                sum(u["completion_tokens"] for u in self.token_usage.values()),
                sum(u["total_tokens"] for u in self.token_usage.values()),
                f"{self.total_cost:.4f}",
            ]
        )
        RichPrinter.table(
            headers=headers,
            rows=rows,
            title="Token Usage and Cost Summary",
            column_styles=["cyan", "magenta", "blue", "blue", "blue", "green"],
        )
