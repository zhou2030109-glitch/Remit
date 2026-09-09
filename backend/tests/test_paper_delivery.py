"""PDF + LaTeX 终稿交付契约回归测试。"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.config.setting import settings
from app.core.paper_judge import judge_paper
from app.core.paper_quality import audit_paper_style
from app.models.user_output import UserOutput
from app.schemas.enums import CompTemplate
from app.utils.paper_polish import render_paper_deliverables


FONT_PATH = Path(__file__).resolve().parents[1] / "fonts" / "simhei.ttf"


class PaperDeliveryTests(unittest.TestCase):
    def test_structured_save_does_not_emit_markdown_final(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = UserOutput(tmp, 1)

            output.save_result()

            root = Path(tmp)
            self.assertFalse((root / "res.md").exists())
            self.assertFalse((root / "res.json").exists())
            self.assertTrue((root / ".remit" / "paper_sections.json").is_file())

    def test_style_audit_rejects_template_phrases_and_vague_attribution(self) -> None:
        text = (
            "研究表明该模型发挥了重要作用，具有重要意义。"
            "不仅提高了精度，而且奠定了坚实基础。"
            "此外，结果表现出色。此外，性能优越。此外，仍可推广。"
        )

        audit = audit_paper_style(text)

        self.assertEqual(audit.status, "fail")
        self.assertLess(audit.score, 85)
        self.assertTrue(any("模板措辞" in issue for issue in audit.issues))
        self.assertTrue(any("模糊归因" in issue for issue in audit.issues))

    def test_style_audit_accepts_evidence_specific_prose(self) -> None:
        references = "\n".join(
            f"[^{index}]: Author {index}. Verified study {index}. Journal, 2024."
            for index in range(1, 7)
        )
        text = (
            "测试集的均方根误差为12.4，比线性基线低8.2%。"
            "误差下降来自分组验证减少了同一主体进入训练集和测试集的泄漏[^1]。"
            "当参数变化20%时，目标值变化2.7%，该结论只适用于本题观测区间。\n"
            + references
        )

        audit = audit_paper_style(text)

        self.assertEqual(audit.status, "pass")
        self.assertEqual(audit.score, 100)

    def test_style_audit_does_not_score_reference_titles_as_prose(self) -> None:
        references = "\n".join(
            f"[^{index}]: 作者{index}. 该方法具有重要意义. 期刊, 2024."
            for index in range(1, 7)
        )
        text = "观测误差为3.1%，结论由留出集上的逐样本残差支持[^1]。\n" + references

        audit = audit_paper_style(text)

        self.assertEqual(audit.status, "pass")
        self.assertEqual(audit.metrics["template_phrase_hits"], {})

    @unittest.skipUnless(
        shutil.which("xelatex") and FONT_PATH.is_file(),
        "requires XeLaTeX and the local SimHei font",
    )
    def test_latex_source_compiles_to_the_pdf_we_deliver(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copy2(FONT_PATH, root / "simhei.ttf")
            markdown = (
                """# 交付编译测试

本文用同一份 LaTeX 源码生成 PDF。测试值为 12.4，单位为秒。

"""
                + ("该段用于检查中文字体、分页和文本提取，观测值保持为 12.4。\n\n" * 80)
                + """

| 指标 | 数值 |
| --- | ---: |
| RMSE | 12.4 |
"""
            )
            with patch.object(settings, "PAPER_MIN_PDF_PAGES", 1):
                delivery = render_paper_deliverables(markdown, root, CompTemplate.CHINA)

            self.assertTrue(delivery.tex_path.is_file())
            self.assertTrue(delivery.pdf_path.is_file())
            self.assertGreater(delivery.pdf_path.stat().st_size, 1_000)
            self.assertFalse((root / "res.md").exists())
            self.assertIn(
                "\\begin{document}",
                delivery.tex_path.read_text(encoding="utf-8"),
            )


class PaperReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_deterministic_findings_are_forwarded_to_reviewer(self) -> None:
        llm = SimpleNamespace(
            chat=AsyncMock(
                return_value=SimpleNamespace(
                    content=(
                        '{"scores":{"abstract":8,"modeling":8,'
                        '"solution_validation":8,"evidence":8,"style":7,'
                        '"writing":8,"innovation":7},"overall":8,'
                        '"weakest_sections":[],"summary":"ok"}'
                    )
                )
            )
        )

        review = await judge_paper(
            llm,
            "# 摘要\n测试正文",
            1,
            deterministic_findings=["发现 1 处无可核验引用的模糊归因"],
        )

        self.assertIsNotNone(review)
        history = llm.chat.await_args.kwargs["history"]
        self.assertIn("确定性扫描", history[1]["content"])
        self.assertIn("模糊归因", history[1]["content"])


if __name__ == "__main__":
    unittest.main()
