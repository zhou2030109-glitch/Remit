"""PDF 赛题结构化文本解析工具。"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

import pymupdf


MAX_PROBLEM_PDF_BYTES = 25 * 1024 * 1024


class PdfParseError(ValueError):
    """PDF 无法安全转换为可用题面文本。"""


@dataclass(frozen=True)
class ParsedProblemPdf:
    """PDF 题面解析结果。"""

    text: str
    page_count: int
    char_count: int


@dataclass(frozen=True)
class _MarkdownElement:
    """已转换为 Markdown 的定位页面元素。"""

    top: float
    left: float
    bbox: tuple[float, float, float, float]
    text: str


def _normalize_page_text(text: str) -> str:
    """清理提取文本，保留段落和小问换行。"""
    lines = [line.strip() for line in text.replace("\x00", "").splitlines()]
    normalized: list[str] = []
    previous_blank = False
    for line in lines:
        blank = not line
        if blank and previous_blank:
            continue
        normalized.append(line)
        previous_blank = blank
    return "\n".join(normalized).strip()


def _reflow_pdf_lines(text: str) -> str:
    """合并 PDF 的视觉折行，不在中文词语中插入多余空格。"""
    lines = [line.strip() for line in text.replace("\x00", "").splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return ""

    result = lines[0]
    cjk_or_punctuation = re.compile(r"[\u3400-\u9fff，。；：！？、（）《》【】]$")
    starts_cjk_or_punctuation = re.compile(
        r"^[\u3400-\u9fff，。；：！？、（）《》【】]"
    )
    for line in lines[1:]:
        separator = " "
        if cjk_or_punctuation.search(result) or starts_cjk_or_punctuation.search(line):
            separator = ""
        result = f"{result}{separator}{line}"
    return result


def _normalize_table_cell(value: object) -> str:
    """将 PDF 单元格转换为安全的单行 Markdown 单元格。"""
    if value is None:
        return ""
    text = re.sub(r"[ \t]+", " ", _reflow_pdf_lines(str(value))).strip()
    return text.replace("|", r"\|")


def _table_to_markdown(rows: list[list[str | None]]) -> str:
    """保留检测到的表格行列关系。"""
    if not rows:
        return ""

    column_count = max(len(row) for row in rows)
    normalized_rows = [
        [
            _normalize_table_cell(row[index] if index < len(row) else "")
            for index in range(column_count)
        ]
        for row in rows
    ]
    header = normalized_rows[0]
    separator = ["---"] * column_count
    body = normalized_rows[1:]

    def markdown_row(row: list[str]) -> str:
        return f"| {' | '.join(row)} |"

    return "\n".join(
        [
            markdown_row(header),
            markdown_row(separator),
            *(markdown_row(row) for row in body),
        ]
    )


def _region_contains_most_of(
    bbox: tuple[float, float, float, float],
    regions: list[tuple[float, float, float, float]],
) -> bool:
    """判断文本块是否属于表格或已重建公式。"""
    rect = pymupdf.Rect(bbox)
    center = pymupdf.Point((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)
    area = max(rect.get_area(), 1.0)
    for region_bbox in regions:
        region = pymupdf.Rect(region_bbox)
        if region.contains(center):
            return True
        intersection = rect & region
        if not intersection.is_empty and intersection.get_area() / area >= 0.25:
            return True
    return False


def _extract_markdown_tables(page: pymupdf.Page) -> list[_MarkdownElement]:
    """检测有边框表格并转换为 Markdown。"""
    try:
        tables = page.find_tables().tables
    except (AttributeError, RuntimeError, ValueError):
        return []

    elements: list[_MarkdownElement] = []
    for table in tables:
        markdown = _table_to_markdown(table.extract())
        if not markdown:
            continue
        bbox = tuple(float(value) for value in table.bbox)
        elements.append(
            _MarkdownElement(top=bbox[1], left=bbox[0], bbox=bbox, text=markdown)
        )
    return elements


def _horizontal_overlap(first: pymupdf.Rect, second: pymupdf.Rect) -> float:
    return max(0.0, min(first.x1, second.x1) - max(first.x0, second.x0))


def _math_to_latex(text: str) -> str:
    """将常见 PDF 数学字形和英文名称标准化为 LaTeX。"""
    value = unicodedata.normalize("NFKC", text).strip()
    value = value.replace("−", "-").replace("–", "-").replace("—", "-")
    greek = {
        "μ": r"\mu",
        "σ": r"\sigma",
        "α": r"\alpha",
        "β": r"\beta",
        "γ": r"\gamma",
        "δ": r"\delta",
    }
    for symbol, latex in greek.items():
        value = value.replace(symbol, latex)
    value = re.sub(
        r"(?<!\\)\b(mu|sigma|alpha|beta|gamma|delta)\b",
        lambda match: rf"\{match.group(1).lower()}",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"\s*([=+\-*/])\s*", r"\1", value)
    return re.sub(r"\s+", "", value)


def _extract_fraction_formulas(
    page: pymupdf.Page,
    excluded_regions: list[tuple[float, float, float, float]],
) -> list[_MarkdownElement]:
    """根据分数线和文字坐标重建上下分式。"""
    spans: list[tuple[str, pymupdf.Rect]] = []
    text_dict = page.get_text("dict", sort=False)
    for block in text_dict.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = str(span.get("text", "")).strip()
                bbox = tuple(float(value) for value in span.get("bbox", (0, 0, 0, 0)))
                if text and not _region_contains_most_of(bbox, excluded_regions):
                    spans.append((text, pymupdf.Rect(bbox)))

    fraction_bars: list[pymupdf.Rect] = []
    for drawing in page.get_drawings():
        bar = pymupdf.Rect(drawing["rect"])
        if bar.width < 8 or bar.height > 2.5:
            continue
        if _region_contains_most_of(tuple(bar), excluded_regions):
            continue
        fraction_bars.append(bar)

    elements: list[_MarkdownElement] = []
    used_spans: set[int] = set()
    for bar in fraction_bars:
        numerator_candidates = [
            (index, text, rect)
            for index, (text, rect) in enumerate(spans)
            if index not in used_spans
            and rect.y1 <= bar.y0 + 1.5
            and 0 <= bar.y0 - rect.y1 <= 10
            and _horizontal_overlap(rect, bar) >= min(rect.width, bar.width) * 0.4
        ]
        denominator_candidates = [
            (index, text, rect)
            for index, (text, rect) in enumerate(spans)
            if index not in used_spans
            and rect.y0 >= bar.y1 - 1.5
            and 0 <= rect.y0 - bar.y1 <= 10
            and _horizontal_overlap(rect, bar) >= min(rect.width, bar.width) * 0.4
        ]
        if not numerator_candidates or not denominator_candidates:
            continue

        numerator = min(
            numerator_candidates,
            key=lambda item: (bar.y0 - item[2].y1, abs(item[2].x0 - bar.x0)),
        )
        denominator = min(
            denominator_candidates,
            key=lambda item: (item[2].y0 - bar.y1, abs(item[2].x0 - bar.x0)),
        )
        formula_vertical_center = (numerator[2].y0 + denominator[2].y1) / 2
        prefix_candidates = [
            (index, text, rect)
            for index, (text, rect) in enumerate(spans)
            if index not in used_spans
            and "=" in text
            and rect.x1 <= bar.x0 + 2
            and 0 <= bar.x0 - rect.x1 <= 80
            and rect.y0 - 4 <= formula_vertical_center <= rect.y1 + 4
        ]
        if not prefix_candidates:
            continue

        prefix = max(prefix_candidates, key=lambda item: item[2].x1)
        prefix_latex = _math_to_latex(prefix[1])
        numerator_latex = _math_to_latex(numerator[1])
        denominator_latex = _math_to_latex(denominator[1])
        if not prefix_latex.endswith("="):
            continue

        formula_bbox = prefix[2] | numerator[2] | denominator[2] | bar
        used_spans.update((prefix[0], numerator[0], denominator[0]))
        elements.append(
            _MarkdownElement(
                top=formula_bbox.y0,
                left=formula_bbox.x0,
                bbox=tuple(formula_bbox),
                text=(
                    f"$${prefix_latex}\\frac"
                    f"{{{numerator_latex}}}{{{denominator_latex}}}$$"
                ),
            )
        )
    return elements


def _extract_page_markdown(page: pymupdf.Page) -> str:
    """提取单页内容，同时保留表格和上下分式结构。"""
    table_elements = _extract_markdown_tables(page)
    table_regions = [element.bbox for element in table_elements]
    formula_elements = _extract_fraction_formulas(page, table_regions)
    excluded_regions = table_regions + [element.bbox for element in formula_elements]

    elements = [*table_elements, *formula_elements]
    for block in page.get_text("blocks", sort=False):
        if len(block) < 7 or block[6] != 0:
            continue
        bbox = tuple(float(value) for value in block[:4])
        if _region_contains_most_of(bbox, excluded_regions):
            continue
        text = _reflow_pdf_lines(str(block[4]))
        if text:
            elements.append(
                _MarkdownElement(
                    top=bbox[1],
                    left=bbox[0],
                    bbox=bbox,
                    text=text,
                )
            )

    elements.sort(key=lambda element: (round(element.top, 1), element.left))
    return _normalize_page_text("\n\n".join(element.text for element in elements))


def parse_problem_pdf_bytes(
    content: bytes,
    *,
    require_text: bool = True,
) -> ParsedProblemPdf:
    """从 PDF 字节中提取按阅读顺序排列的结构化题面 Markdown。

    Args:
        content: PDF 字节内容。
        require_text: 提取不到文字时是否直接报错。识图流程会先放行扫描版
            PDF，改由多模态模型转录，因此传 False。

    Raises:
        PdfParseError: PDF 无效、加密，或 ``require_text`` 为真时无可提取文字。
    """
    if not content:
        raise PdfParseError("PDF 文件为空")
    if not content.startswith(b"%PDF-"):
        raise PdfParseError("文件内容不是有效的 PDF")

    try:
        with pymupdf.open(stream=content, filetype="pdf") as document:
            if document.needs_pass:
                raise PdfParseError("PDF 已加密，请先移除密码后重新上传")

            pages = [_extract_page_markdown(page) for page in document]
            text = "\n\n".join(page for page in pages if page).strip()
            page_count = document.page_count
    except PdfParseError:
        raise
    except (RuntimeError, ValueError, pymupdf.FileDataError) as exc:
        raise PdfParseError("PDF 文件损坏或格式不受支持") from exc

    if not text and require_text:
        raise PdfParseError(
            "未检测到可提取文字；如果是扫描版 PDF，请先进行 OCR 后重新上传"
        )

    return ParsedProblemPdf(
        text=text,
        page_count=page_count,
        char_count=len(text),
    )
