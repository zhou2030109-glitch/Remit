"""论文中文字体选择及实际 LaTeX 交付回归。"""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import patch

import pymupdf
import pytest

from app.config.setting import settings
from app.schemas.enums import CompTemplate
from app.utils.paper_polish import (
    PaperRenderError,
    _compile_latex,
    _convert_markdown_to_latex,
    build_pdf_header,
    render_paper_deliverables,
)


def test_bundled_chinese_font_keeps_existing_header(tmp_path: Path) -> None:
    (tmp_path / "simhei.ttf").touch()

    header = build_pdf_header(tmp_path)

    assert (
        "\\setCJKmainfont[\n  Path=./,\n  BoldFont=simhei.ttf\n]{simhei.ttf}" in header
    )
    assert "Noto Sans CJK SC" not in header


def test_missing_bundled_font_uses_system_fonts(tmp_path: Path) -> None:
    header = build_pdf_header(tmp_path)

    assert "\\IfFontExistsTF{Noto Sans CJK SC}" in header
    assert "\\setCJKmainfont{Noto Sans CJK SC}" in header
    assert "\\setCJKmainfont[BoldFont=FandolSong-Bold]{FandolSong-Regular}" in header
    assert "simhei.ttf" not in header
    assert "Path=./" not in header


@pytest.mark.parametrize("bundled_font", [False, True])
def test_pandoc_includes_selected_font_in_delivered_source(
    tmp_path: Path, bundled_font: bool
) -> None:
    if bundled_font:
        (tmp_path / "simhei.ttf").touch()
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    tex_path = tmp_path / "res.tex"

    _convert_markdown_to_latex(
        "# 中文字体回归测试\n\n**中文加粗正文**，误差为 12.4%。",
        tex_path,
        tmp_path,
        build_dir,
        CompTemplate.CHINA,
    )

    source = tex_path.read_text(encoding="utf-8")
    assert build_pdf_header(tmp_path).strip() in source
    assert "\\begin{document}" in source
    assert "中文字体回归测试" in source
    assert ("simhei.ttf" in source) is bundled_font


def test_missing_latex_engine_reports_installation_instructions(tmp_path: Path) -> None:
    with (
        patch.object(settings, "LATEX_ENGINE", "xelatex"),
        patch("app.utils.paper_polish.shutil.which", return_value=None),
        pytest.raises(
            PaperRenderError,
            match="找不到 LaTeX 编译器 xelatex.*MiKTeX.*TeX Live.*PATH",
        ),
    ):
        _compile_latex(tmp_path / "res.tex", tmp_path)


@pytest.mark.skipif(not shutil.which("xelatex"), reason="requires XeLaTeX")
def test_system_chinese_font_compiles_without_bundled_font(tmp_path: Path) -> None:
    markdown = (
        "# 中文字体回归测试\n\n"
        "**中文加粗正文**。使用系统中文字体，误差为 12.4%。\n\n"
        "| 指标 | 数值 |\n| --- | ---: |\n| RMSE | 12.4 |\n"
        + "\n\n该段用于检查中文字体、分页和文本提取，观测值保持为 12.4。"
        * 40
    )

    with patch.object(settings, "PAPER_MIN_PDF_PAGES", 1):
        delivery = render_paper_deliverables(markdown, tmp_path, CompTemplate.CHINA)

    assert not (tmp_path / "simhei.ttf").exists()
    assert "simhei.ttf" not in delivery.tex_path.read_text(encoding="utf-8")
    with pymupdf.open(delivery.pdf_path) as document:
        text = "".join(page.get_text() for page in document)
    assert "中文字体回归测试" in text
    assert "中文加粗正文" in text
