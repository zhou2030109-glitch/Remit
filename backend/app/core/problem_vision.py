"""赛题 PDF 多模态识图：把插图、坐标图和扫描页转成可建模的文字信息。

识图是纯增强环节：任何一步失败都降级为"只有文本"的原有行为，绝不阻断赛题导入。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from app.core.llm.content import build_image_block, build_text_block
from app.core.llm.llm import LLM
from app.utils.log_util import logger
from app.utils.pdf_figures import ProblemFigure

# 一次请求最多带几张图：过多会拖长单次响应，也更容易被中转按体积拒绝
_FIGURES_PER_CALL = 3
_MAX_CONCURRENT_CALLS = 3

_VISION_PROMPT = """你是数学建模赛题的图像解析助手。用户会给你赛题 PDF 中截取的图像。
对每张图，判断它是否携带建模必需的信息，并把图上能读到的内容转成文字。

要求：
- 只描述图上真实可见的内容，读不清就写"无法辨认"，禁止猜测或补全数值
- 坐标图要写清横纵轴含义、单位、量级范围和主要趋势
- 流程图/示意图要写清节点、连接关系和方向
- 表格截图要按行列把数据转成 Markdown 表格
- 整页扫描图要逐字转录页面上的文字（含小问编号），保持原有段落顺序
- modeling_relevance 说明这张图给建模提供了什么约束、参数或数据

只输出 JSON：
{"figures": [{
  "index": 图片序号（与输入的"图N"一致）,
  "figure_type": "坐标图|流程图|示意图|地图|表格截图|公式截图|扫描页|装饰图",
  "title": "图题或一句话概括",
  "transcription": "图上文字与数据的忠实转录，可用 Markdown 表格",
  "readable_values": ["能读出的关键数值或标签"],
  "modeling_relevance": "对建模的作用；装饰图写'无建模价值'",
  "carries_information": true/false
}]}"""


@dataclass
class FigureInsight:
    """单张图的识图结论。"""

    index: int
    page_number: int
    kind: str
    figure_type: str = ""
    title: str = ""
    transcription: str = ""
    readable_values: list[str] = field(default_factory=list)
    modeling_relevance: str = ""
    carries_information: bool = True

    def to_dict(self) -> dict[str, Any]:
        """转成可落盘、可返回前端的字典。"""
        return {
            "index": self.index,
            "page_number": self.page_number,
            "kind": self.kind,
            "figure_type": self.figure_type,
            "title": self.title,
            "transcription": self.transcription,
            "readable_values": self.readable_values,
            "modeling_relevance": self.modeling_relevance,
            "carries_information": self.carries_information,
        }


@dataclass
class VisionResult:
    """整份 PDF 的识图结果。"""

    status: str
    insights: list[FigureInsight]
    figure_count: int
    error: str = ""

    @property
    def informative_insights(self) -> list[FigureInsight]:
        """只保留真正携带建模信息的图。"""
        return [item for item in self.insights if item.carries_information]


def _parse_payload(content: str) -> dict[str, Any] | None:
    """解析模型返回的 JSON；容忍代码块包裹和前置说明。"""
    text = content.replace("```json", "").replace("```", "").strip()
    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _build_user_content(batch: list[ProblemFigure]) -> list[dict]:
    """把一批图连同页面上下文拼成多模态用户消息。"""
    content: list[dict] = [
        build_text_block(
            f"本批共 {len(batch)} 张图，按顺序给出。请对每张图输出一条结果。"
        )
    ]
    for figure in batch:
        hint = f"【{figure.label}】来自第 {figure.page_number} 页"
        if figure.kind == "full_page":
            hint += "（整页扫描或纯图页，请逐字转录页面全部文字）"
        if figure.nearby_text:
            hint += f"\n图片周边文字：{figure.nearby_text}"
        content.append(build_text_block(hint))
        content.append(
            build_image_block(figure.image_bytes, media_type=figure.media_type)
        )
    return content


async def _describe_batch(llm: LLM, batch: list[ProblemFigure]) -> list[FigureInsight]:
    """识别一批图；失败时返回空列表由调用方降级。"""
    response = await llm.chat(
        history=[
            {"role": "system", "content": _VISION_PROMPT},
            {"role": "user", "content": _build_user_content(batch)},
        ],
        agent_name="CoordinatorAgent",
        publish=False,
        max_retries=2,
    )
    payload = _parse_payload(response.content or "")
    raw_items = (payload or {}).get("figures")
    if not isinstance(raw_items, list):
        return []

    by_index = {figure.index: figure for figure in batch}
    insights: list[FigureInsight] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        figure = by_index.get(index)
        if figure is None:
            continue
        values = item.get("readable_values")
        insights.append(
            FigureInsight(
                index=index,
                page_number=figure.page_number,
                kind=figure.kind,
                figure_type=str(item.get("figure_type", "")),
                title=str(item.get("title", "")),
                transcription=str(item.get("transcription", "")),
                readable_values=[
                    str(value) for value in values[:12] if str(value).strip()
                ]
                if isinstance(values, list)
                else [],
                modeling_relevance=str(item.get("modeling_relevance", "")),
                carries_information=item.get("carries_information") is not False,
            )
        )
    return insights


async def describe_problem_figures(
    figures: list[ProblemFigure],
    llm: LLM,
) -> VisionResult:
    """对赛题图像批量识图。

    Args:
        figures: 从 PDF 裁切出的图像。
        llm: 具备视觉能力的模型。

    Returns:
        识图结果；status 为 completed/partial/failed/skipped。
    """
    if not figures:
        return VisionResult(status="skipped", insights=[], figure_count=0)

    batches = [
        figures[start : start + _FIGURES_PER_CALL]
        for start in range(0, len(figures), _FIGURES_PER_CALL)
    ]
    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_CALLS)

    async def _guarded(batch: list[ProblemFigure]) -> list[FigureInsight]:
        async with semaphore:
            return await _describe_batch(llm, batch)

    results = await asyncio.gather(
        *(_guarded(batch) for batch in batches), return_exceptions=True
    )

    insights: list[FigureInsight] = []
    errors: list[str] = []
    for result in results:
        if isinstance(result, BaseException):
            logger.warning(f"赛题识图批次失败: {result}")
            errors.append(str(result))
            continue
        insights.extend(result)

    insights.sort(key=lambda item: item.index)
    if not insights:
        return VisionResult(
            status="failed",
            insights=[],
            figure_count=len(figures),
            error="；".join(errors)[:300] or "模型未返回可用识图结果",
        )
    return VisionResult(
        status="partial" if errors or len(insights) < len(figures) else "completed",
        insights=insights,
        figure_count=len(figures),
        error="；".join(errors)[:300],
    )


def build_vision_supplement(result: VisionResult) -> str:
    """把识图结论拼成追加到题面末尾的 Markdown 段落。"""
    informative = result.informative_insights
    if not informative:
        return ""

    lines = [
        "## 附：赛题图像识别结果",
        "",
        "以下内容由多模态模型从赛题 PDF 的插图与扫描页中读出，"
        "与正文同等重要；若与正文冲突，以正文为准。",
        "",
    ]
    for insight in informative:
        heading = f"### 图{insight.index}（第 {insight.page_number} 页"
        if insight.figure_type:
            heading += f"，{insight.figure_type}"
        heading += "）"
        lines.append(heading)
        if insight.title:
            lines.append(f"**图题**：{insight.title}")
        if insight.transcription:
            lines.append("")
            lines.append(insight.transcription)
        if insight.readable_values:
            lines.append("")
            lines.append("**可读数值**：" + "；".join(insight.readable_values))
        if insight.modeling_relevance:
            lines.append("")
            lines.append(f"**建模含义**：{insight.modeling_relevance}")
        lines.append("")
    return "\n".join(lines).strip()
