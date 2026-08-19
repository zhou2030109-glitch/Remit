"""赛题多模态识图回归测试：图像裁切、Provider 转换与题面补充。"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock

import pymupdf

from app.core.llm.content import (
    ImageBlock,
    build_image_block,
    build_text_block,
    has_image_content,
    iter_content_blocks,
)
from app.core.llm.providers.anthropic import AnthropicProvider
from app.core.llm.providers.gemini import GeminiProvider
from app.core.llm.providers.openai_responses import OpenAIResponsesProvider
from app.core.llm.types import StandardResponse
from app.core.problem_vision import (
    build_vision_supplement,
    describe_problem_figures,
)
from app.utils.pdf_figures import extract_problem_figures
from app.utils.pdf_parser import parse_problem_pdf_bytes


def _pdf_with_figure_and_scanned_page() -> bytes:
    """第 1 页正常文字加矢量插图，第 2 页几乎无文字（模拟扫描页）。"""
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 90), "Problem A: minimize cost under capacity limits.")
    page.insert_text((72, 110), "Question 1: model the network shown below.")
    page.draw_rect(pymupdf.Rect(100, 200, 400, 480), width=1.5)
    page.draw_line(pymupdf.Point(120, 240), pymupdf.Point(380, 440))
    page.draw_circle(pymupdf.Point(250, 330), 60)

    scanned = document.new_page()
    scanned.draw_rect(pymupdf.Rect(60, 60, 520, 700), width=1)

    content = document.tobytes()
    document.close()
    return content


class ContentBlockTests(unittest.TestCase):
    def test_plain_string_becomes_single_text_block(self) -> None:
        blocks = list(iter_content_blocks("你好"))

        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].text, "你好")

    def test_parses_data_url_into_image_block(self) -> None:
        content = [build_text_block("看图"), build_image_block(b"png-bytes")]

        blocks = list(iter_content_blocks(content))

        self.assertIsInstance(blocks[1], ImageBlock)
        self.assertEqual(blocks[1].media_type, "image/png")
        self.assertTrue(has_image_content([{"content": content}]))

    def test_ignores_remote_image_url_without_base64(self) -> None:
        content = [{"type": "image_url", "image_url": {"url": "https://x/y.png"}}]

        self.assertEqual(list(iter_content_blocks(content)), [])


class ProviderMultimodalConversionTests(unittest.TestCase):
    """四个 Provider 都必须能把同一份图文消息翻译成各自协议。"""

    def setUp(self) -> None:
        self.content = [build_text_block("看这张图"), build_image_block(b"fake-png")]
        self.messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": self.content},
        ]

    def test_openai_responses_uses_input_image(self) -> None:
        items = OpenAIResponsesProvider()._messages_to_input(self.messages)

        parts = items[1]["content"]
        self.assertEqual(parts[0]["type"], "input_text")
        self.assertEqual(parts[1]["type"], "input_image")
        self.assertTrue(parts[1]["image_url"].startswith("data:image/png;base64,"))

    def test_anthropic_uses_base64_image_source(self) -> None:
        system, converted = AnthropicProvider()._convert_messages(self.messages)

        self.assertEqual(system, "sys")
        blocks = converted[0]["content"]
        self.assertEqual(blocks[0]["type"], "text")
        self.assertEqual(blocks[1]["source"]["media_type"], "image/png")

    def test_gemini_uses_inline_data(self) -> None:
        _, contents = GeminiProvider()._convert_messages(self.messages)

        parts = contents[0]["parts"]
        self.assertEqual(parts[0]["text"], "看这张图")
        self.assertEqual(parts[1]["inlineData"]["mimeType"], "image/png")

    def test_plain_text_messages_keep_original_shape(self) -> None:
        """纯文本请求体不得因为支持图像而改变形状。"""
        plain = [{"role": "user", "content": "hi"}]

        self.assertEqual(
            OpenAIResponsesProvider()._messages_to_input(plain),
            [{"role": "user", "content": "hi"}],
        )
        self.assertEqual(
            AnthropicProvider()._convert_messages(plain)[1],
            [{"role": "user", "content": "hi"}],
        )


class FigureExtractionTests(unittest.TestCase):
    def test_extracts_vector_figure_and_scanned_page(self) -> None:
        figures = extract_problem_figures(_pdf_with_figure_and_scanned_page())

        kinds = [figure.kind for figure in figures]
        self.assertIn("vector_figure", kinds)
        self.assertIn("full_page", kinds)
        self.assertTrue(all(figure.image_bytes for figure in figures))

    def test_scanned_pdf_no_longer_blocks_text_extraction(self) -> None:
        """扫描件靠识图转录，因此解析阶段不能再直接报错。"""
        document = pymupdf.open()
        document.new_page()
        blank = document.tobytes()
        document.close()

        parsed = parse_problem_pdf_bytes(blank, require_text=False)

        self.assertEqual(parsed.text, "")
        self.assertEqual(parsed.page_count, 1)

    def test_ignores_non_pdf_content(self) -> None:
        self.assertEqual(extract_problem_figures(b"not a pdf"), [])


class VisionSupplementTests(unittest.TestCase):
    def _describe(self, payload: str):
        figures = extract_problem_figures(_pdf_with_figure_and_scanned_page())
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value=StandardResponse(content=payload))
        return figures, asyncio.run(describe_problem_figures(figures, llm))

    def test_builds_markdown_supplement_from_model_output(self) -> None:
        payload = """{"figures": [
            {"index": 1, "figure_type": "示意图", "title": "运输网络",
             "transcription": "节点 A→B→C", "readable_values": ["容量 30"],
             "modeling_relevance": "给出网络拓扑与容量上限",
             "carries_information": true},
            {"index": 2, "figure_type": "装饰图", "title": "边框",
             "transcription": "", "readable_values": [],
             "modeling_relevance": "无建模价值", "carries_information": false}
        ]}"""

        _, result = self._describe(payload)
        supplement = build_vision_supplement(result)

        self.assertEqual(result.status, "completed")
        self.assertEqual(len(result.informative_insights), 1)
        self.assertIn("运输网络", supplement)
        self.assertIn("节点 A→B→C", supplement)
        self.assertIn("容量 30", supplement)
        # 无建模价值的装饰图不得污染题面
        self.assertNotIn("边框", supplement)

    def test_unparseable_output_degrades_to_failed(self) -> None:
        _, result = self._describe("模型今天不想输出 JSON")

        self.assertEqual(result.status, "failed")
        self.assertEqual(build_vision_supplement(result), "")


if __name__ == "__main__":
    unittest.main()
