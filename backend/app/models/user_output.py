"""论文成稿的组装器。

各章节由 Writer 分段产出，本模块负责：
1. 汇总各章节内容；
2. 把正文中的行内引用 ``{[^n]: 出处}`` 归并去重；
3. 按论文骨架顺序拼接，并将引用重排为连续编号；
4. 追加参考文献列表并落盘（res.json / res.md）。
"""

import json
import re
import uuid
from pathlib import Path
from typing import Any

from app.schemas.A2A import WriterResponse
from app.utils.data_recorder import DataRecorder

_INLINE_REF = re.compile(r"\{\[\^(\d+)\]:\s*(.*?)\}", re.DOTALL)
_UUID_REF = re.compile(r"\[([a-f0-9-]{36})\]")


class UserOutput:
    """一次建模任务的写作成果集合与终稿渲染。"""

    def __init__(
        self, work_dir: str, ques_count: int, data_recorder: DataRecorder | None = None
    ) -> None:
        self.work_dir = work_dir
        self.ques_count = ques_count
        self.data_recorder = data_recorder
        self.cost_time = 0.0
        self.initialized = True
        self.res: dict[str, dict[str, Any]] = {}
        self.footnotes: dict[str, dict[str, Any]] = {}
        self.seq = self._section_order(ques_count)

    @staticmethod
    def _section_order(ques_count: int) -> list[str]:
        """论文骨架：前置部分 → EDA → 各小问 → 检验与评价。"""
        questions = [f"ques{i}" for i in range(1, ques_count + 1)]
        return [
            "firstPage",  # 标题、摘要、关键词
            "RepeatQues",  # 问题重述
            "analysisQues",  # 问题分析
            "modelAssumption",  # 模型假设
            "symbol",  # 符号说明
            "eda",  # 数据预处理
            *questions,  # 模型建立与求解
            "sensitivity_analysis",  # 灵敏度分析
            "judge",  # 模型评价与推广
        ]

    # ---- 章节读写 ----

    def set_res(self, key: str, writer_response: WriterResponse) -> None:
        """登记一个章节的写作产出。"""
        self.res[key] = {
            "response_content": writer_response.response_content,
            "footnotes": writer_response.footnotes,
        }

    def get_res(self) -> dict[str, dict[str, Any]]:
        """返回全部章节的原始产出。"""
        return self.res

    def get_model_build_solve(self) -> str:
        """把各小问的求解结果压成一段摘要，供评审章节引用。"""
        return ",".join(
            f"{key}-{value}"
            for key, value in self.res.items()
            if key.startswith("ques") and key != "ques_count"
        )

    # ---- 引用归并 ----

    def _dedupe_refs(self, text: str) -> str:
        """把行内引用替换为占位 UUID；相同出处复用同一 UUID。"""
        for ref_num, ref_body in _INLINE_REF.findall(text):
            content = ref_body.strip().rstrip(".")
            placeholder = next(
                (uid for uid, meta in self.footnotes.items() if meta["content"] == content),
                None,
            )
            if placeholder is None:
                placeholder = str(uuid.uuid4())
                self.footnotes[placeholder] = {"content": content}
            text = re.sub(
                rf"\{{\[\^{ref_num}\]:.*?\}}",
                f"[{placeholder}]",
                text,
                count=1,
                flags=re.DOTALL,
            )
        return text

    def _number_refs(self, section_texts: dict[str, str]) -> dict[str, str]:
        """按骨架顺序把 UUID 占位重排为 [^1]、[^2]… 连续编号。"""
        ordered: dict[str, str] = {}
        next_index = 1
        for key in self.seq:
            text = section_texts[key]
            for uid in _UUID_REF.findall(text):
                text = text.replace(f"[{uid}]", f"[^{next_index}]")
                if self.footnotes[uid].get("number") is None:
                    self.footnotes[uid]["number"] = next_index
                next_index += 1
            ordered[key] = text
        return ordered

    def _reference_list(self) -> str:
        """渲染参考文献区块。"""
        lines = ["\n\n ## 参考文献"]
        for _, meta in sorted(self.footnotes.items(), key=lambda kv: kv[1]["number"]):
            lines.append(f"\n\n[^{meta['number']}]: {meta['content']}")
        return "".join(lines)

    # ---- 终稿 ----

    def get_result_to_save(self) -> str:
        """组装完整论文：去重引用 → 重编号 → 拼接 → 附参考文献。

        每次调用都重置脚注，保证评审重写章节后二次导出仍然幂等。
        """
        self.footnotes = {}
        deduped = {
            key: self._dedupe_refs(value["response_content"])
            for key, value in self.res.items()
        }
        ordered = self._number_refs(deduped)
        body = "\n\n".join(ordered[key] for key in self.seq)
        return body + self._reference_list()

    def save_result(self) -> None:
        """导出结构化章节（res.json）与成稿（res.md）。"""
        root = Path(self.work_dir)
        (root / "res.json").write_text(
            json.dumps(self.res, ensure_ascii=False, indent=4), encoding="utf-8"
        )
        (root / "res.md").write_text(self.get_result_to_save(), encoding="utf-8")
