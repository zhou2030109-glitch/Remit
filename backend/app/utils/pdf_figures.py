"""从赛题 PDF 中裁切图像区域，供多模态模型识图。

纯文本提取会丢掉赛题里的示意图、流程图、坐标图和扫描页，这些往往是约束条件
和数据关系的唯一载体。本模块只负责"截图"，不做任何语义理解；识图由
:mod:`app.core.problem_vision` 调用视觉模型完成。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pymupdf


# 单页文字少于该字数时视为扫描页或纯图页，直接整页送识图
_SCANNED_PAGE_TEXT_THRESHOLD = 60
# 过滤装饰线、分隔符和页眉 logo：区域必须同时超过绝对尺寸和页面占比
_MIN_FIGURE_POINTS = 60.0
_MIN_PAGE_AREA_RATIO = 0.02
# 渲染倍率：2 倍足以让模型读清坐标轴刻度，又不至于把请求体撑爆
_RENDER_ZOOM = 2.0
_MAX_RENDER_PIXELS = 2200

FigureKind = Literal["embedded_image", "vector_figure", "full_page"]


@dataclass(frozen=True)
class ProblemFigure:
    """赛题 PDF 中一块待识别的图像区域。"""

    index: int
    page_number: int
    kind: FigureKind
    image_bytes: bytes
    media_type: str
    nearby_text: str

    @property
    def label(self) -> str:
        """面向用户和模型的稳定编号。"""
        return f"图{self.index}"


def _rect_is_significant(rect: pymupdf.Rect, page_rect: pymupdf.Rect) -> bool:
    """排除线条、色块和页眉页脚小图标。"""
    if rect.is_empty or rect.is_infinite:
        return False
    if rect.width < _MIN_FIGURE_POINTS or rect.height < _MIN_FIGURE_POINTS:
        return False
    page_area = max(page_rect.get_area(), 1.0)
    return rect.get_area() / page_area >= _MIN_PAGE_AREA_RATIO


def _merge_overlapping(rects: list[pymupdf.Rect]) -> list[pymupdf.Rect]:
    """合并重叠区域，避免同一张图被拆成多次识图调用。"""
    merged: list[pymupdf.Rect] = []
    for rect in sorted(rects, key=lambda item: (item.y0, item.x0)):
        for index, existing in enumerate(merged):
            if not (rect & existing).is_empty:
                merged[index] = existing | rect
                break
        else:
            merged.append(pymupdf.Rect(rect))
    return merged


def _render(page: pymupdf.Page, clip: pymupdf.Rect | None) -> bytes | None:
    """把页面或页面上的一块区域渲染成 PNG。"""
    target = clip or page.rect
    longest = max(target.width, target.height) * _RENDER_ZOOM
    zoom = _RENDER_ZOOM
    if longest > _MAX_RENDER_PIXELS:
        zoom = _RENDER_ZOOM * _MAX_RENDER_PIXELS / longest
    try:
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip)
    except (RuntimeError, ValueError):
        return None
    if pixmap.width < 24 or pixmap.height < 24:
        return None
    return pixmap.tobytes("png")


def _nearby_text(page: pymupdf.Page, clip: pymupdf.Rect | None) -> str:
    """取图像周边文字作为识图上下文（通常是图题和坐标轴说明）。"""
    if clip is None:
        return " ".join(page.get_text("text").split())[:400]
    context = pymupdf.Rect(clip)
    context.y0 -= 40
    context.y1 += 40
    context.x0 -= 20
    context.x1 += 20
    try:
        text = page.get_text("text", clip=context & page.rect)
    except (RuntimeError, ValueError):
        return ""
    return " ".join(text.split())[:400]


def _page_figure_rects(page: pymupdf.Page) -> list[pymupdf.Rect]:
    """收集单页上值得识图的区域。"""
    page_rect = page.rect
    rects: list[pymupdf.Rect] = []

    for image in page.get_images(full=True):
        try:
            placements = page.get_image_rects(image[0])
        except (RuntimeError, ValueError):
            continue
        rects.extend(
            pymupdf.Rect(rect)
            for rect in placements
            if _rect_is_significant(pymupdf.Rect(rect), page_rect)
        )

    # 矢量绘制的流程图、坐标图不在 get_images 里，必须按绘图指令聚类
    try:
        clusters = page.cluster_drawings()
    except (AttributeError, RuntimeError, ValueError):
        clusters = []
    rects.extend(
        pymupdf.Rect(rect)
        for rect in clusters
        if _rect_is_significant(pymupdf.Rect(rect), page_rect)
    )

    return _merge_overlapping(rects)


def extract_problem_figures(
    content: bytes,
    *,
    max_figures: int = 12,
) -> list[ProblemFigure]:
    """裁切赛题 PDF 中的图像区域。

    Args:
        content: PDF 字节内容。
        max_figures: 最多返回多少张图，防止长赛题把识图成本放大。

    Returns:
        按页码和位置排序的图像列表；PDF 无图或解析失败时返回空列表。
    """
    figures: list[ProblemFigure] = []
    try:
        with pymupdf.open(stream=content, filetype="pdf") as document:
            if document.needs_pass:
                return []
            for page_index, page in enumerate(document, start=1):
                if len(figures) >= max_figures:
                    break

                page_text = page.get_text("text").strip()
                if len(page_text) < _SCANNED_PAGE_TEXT_THRESHOLD:
                    # 扫描页整页送识图，等价于 OCR；再去裁切局部只会丢上下文
                    image_bytes = _render(page, None)
                    if image_bytes:
                        figures.append(
                            ProblemFigure(
                                index=len(figures) + 1,
                                page_number=page_index,
                                kind="full_page",
                                image_bytes=image_bytes,
                                media_type="image/png",
                                nearby_text=" ".join(page_text.split())[:400],
                            )
                        )
                    continue

                for rect in _page_figure_rects(page):
                    if len(figures) >= max_figures:
                        break
                    image_bytes = _render(page, rect)
                    if not image_bytes:
                        continue
                    figures.append(
                        ProblemFigure(
                            index=len(figures) + 1,
                            page_number=page_index,
                            kind="embedded_image"
                            if page.get_images()
                            else "vector_figure",
                            image_bytes=image_bytes,
                            media_type="image/png",
                            nearby_text=_nearby_text(page, rect),
                        )
                    )
    except (RuntimeError, ValueError, pymupdf.FileDataError):
        return []
    return figures
