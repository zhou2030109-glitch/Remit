"""论文后处理逻辑的轻量测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from app.utils.paper_polish import (
    compact_abstract,
    merge_image_blocks,
    normalize_common_math,
)


class TestPaperPolish(unittest.TestCase):
    """验证论文后处理的关键规则。"""

    def test_compact_abstract_adds_bold_lead(self) -> None:
        """摘要首段应自动加粗问题导语。"""
        markdown = """## 摘要

针对问题一，本文构建了稳定模型，并给出结果。该结果表明方案有效。

**关键词：** A；B；C
"""
        polished = compact_abstract(markdown)
        self.assertIn("**针对问题一：**", polished)
        self.assertIn("**关键词：**", polished)

    def test_merge_image_blocks_builds_composite(self) -> None:
        """相邻图片应合并成一张复合图。"""
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            image1 = work_dir / "a.png"
            image2 = work_dir / "b.png"
            Image.new("RGB", (120, 80), "red").save(image1)
            Image.new("RGB", (120, 80), "blue").save(image2)

            markdown = "\n".join(
                [
                    "先看图。",
                    "![图A](a.png)",
                    "",
                    "![图B](b.png)",
                    "",
                    "后面还有正文。",
                ]
            )
            polished = merge_image_blocks(markdown, work_dir)

            self.assertIn("paper_composites/composite_001.png", polished)
            self.assertTrue(
                (work_dir / "paper_composites" / "composite_001.png").exists()
            )

    def test_normalize_common_math_keeps_display_equations_intact(self) -> None:
        """公式块内的 R^2 不应被拆坏成错误的行内数学。"""
        markdown = "$$\nR^2\n=1-\\frac{a}{b}.\n$$\n"
        polished = normalize_common_math(markdown)
        self.assertIn("R^2 =1-\\frac{a}{b}.", polished)
        self.assertNotIn("$R^2$", polished)

    def test_compact_abstract_stays_short(self) -> None:
        """摘要压缩后应明显收束到一页级别。"""
        markdown = """## 摘要

针对问题一，本文建立稳健回归模型，并给出显著结论。结果表明，孕周每增加1周，Y染色体浓度平均增加0.0013；BMI每增加1单位，浓度平均降低0.0022。

针对问题二，本文建立离散时间风险模型，并给出稳健方案。结果显示，最终采用单组第12周策略。

针对问题三，本文建立广义加性混合模型，并保留保守方案。结果显示，该复杂模型未超过基线。

针对问题四，本文建立PLS-Firth模型，并仅用于人工复核。结果表明，其未通过外部门槛。

**关键词：** A；B；C
"""
        polished = compact_abstract(markdown)
        body = polished.split("## 摘要", 1)[1]
        self.assertLess(len(body), 700)
        self.assertIn("**针对问题一：**", polished)
        self.assertIn("**结果表明", polished)


if __name__ == "__main__":
    unittest.main()
