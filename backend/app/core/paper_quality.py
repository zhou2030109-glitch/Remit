"""终稿的证据表达与模板化写作审计。

这里不尝试猜测文本是否由 AI 生成；任何所谓“AI 检测率”都不是稳定、
可复现的质量指标。审计只检查评委真正能观察到的问题：空泛套话、无来源
归因、机械连接词、重复句子以及参考文献不足。
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any


_TEMPLATE_PHRASES = (
    "标志着",
    "奠定了坚实基础",
    "发挥了重要作用",
    "具有重要意义",
    "深入探讨",
    "充分展示",
    "结果令人振奋",
    "取得了良好的效果",
    "表现出色",
    "达到了较高水平",
    "性能优越",
    "相信随着技术的不断发展",
    "not only",
    "first and foremost",
    "shed light on",
    "pave the way for",
    "plays a crucial role",
    "valuable insights",
    "undoubtedly",
    "remarkably",
    "intriguingly",
    "crucially",
)
_ATTRIBUTION_RE = re.compile(
    r"(?:研究表明|专家认为|行业报告显示|据报道|众所周知)", re.IGNORECASE
)
_CITATION_RE = re.compile(
    r"(?:\[\^?\d+(?:[-,，]\d+)*\]|\([A-Z][A-Za-z .,&-]+,?\s*\d{4}\))"
)
_REFERENCE_DEF_RE = re.compile(r"(?m)^\s*\[\^\d+\]:\s*\S+")
_MARKDOWN_RE = re.compile(r"(?:!\[[^\]]*\]\([^)]+\)|[*_`#>|])")


@dataclass(frozen=True)
class PaperStyleAudit:
    """可持久化、可回归测试的终稿表述审计结果。"""

    status: str
    score: int
    issues: tuple[str, ...]
    metrics: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sentences(text: str) -> list[str]:
    plain = _MARKDOWN_RE.sub(" ", text)
    return [
        re.sub(r"\s+", " ", sentence).strip()
        for sentence in re.split(r"(?<=[。！？!?])|(?<=[.])\s+", plain)
        if len(re.sub(r"\s+", "", sentence)) >= 20
        and not sentence.lstrip().startswith("[^")
    ]


def audit_paper_style(text: str) -> PaperStyleAudit:
    """检查可观测的模板化写作风险，返回确定性报告。"""
    # 参考文献题名可能合法包含“重要意义”等字样，不能把文献原题误判成
    # 正文套话；引用数量仍然从完整文本统计。
    prose = re.sub(r"(?m)^\s*\[\^\d+\]:.*$", "", text)
    lowered = prose.casefold()
    phrase_hits = {
        phrase: lowered.count(phrase.casefold())
        for phrase in _TEMPLATE_PHRASES
        if phrase.casefold() in lowered
    }

    sentences = _sentences(prose)
    unsupported_attributions = [
        sentence[:180]
        for sentence in sentences
        if _ATTRIBUTION_RE.search(sentence) and not _CITATION_RE.search(sentence)
    ]
    normalized_sentences = [
        re.sub(r"[^\w\u4e00-\u9fff]+", "", sentence).casefold()
        for sentence in sentences
    ]
    duplicate_sentences = sorted(
        sentence
        for sentence, count in Counter(normalized_sentences).items()
        if sentence and count > 1
    )

    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    reference_count = len(_REFERENCE_DEF_RE.findall(text))
    required_references = 6 if chinese_chars else 5
    additionally_count = prose.count("此外")
    english_linkers = len(re.findall(r"\b(?:moreover|furthermore)\b", lowered))
    em_dash_count = prose.count("—")
    forced_parallel_count = len(
        re.findall(r"不仅.{0,100}?而且|not only.{0,160}?but also", prose, re.I | re.S)
    )

    issues: list[str] = []
    if phrase_hits:
        issues.append(
            "发现空泛或宣传式模板措辞："
            + "、".join(f"{phrase}×{count}" for phrase, count in phrase_hits.items())
        )
    if unsupported_attributions:
        issues.append(f"发现 {len(unsupported_attributions)} 处无可核验引用的模糊归因")
    if duplicate_sentences:
        issues.append(f"发现 {len(duplicate_sentences)} 个重复长句")
    if additionally_count > 2:
        issues.append(f"“此外”出现 {additionally_count} 次，超过全文 2 次上限")
    if english_linkers > 2:
        issues.append(
            f"moreover/furthermore 合计出现 {english_linkers} 次，超过 2 次上限"
        )
    if em_dash_count > 2:
        issues.append(f"破折号出现 {em_dash_count} 次，超过全文 2 次上限")
    if forced_parallel_count:
        issues.append(f"发现 {forced_parallel_count} 处“不仅…而且…”式机械并列")
    if reference_count < required_references:
        issues.append(
            f"可核验参考文献仅 {reference_count} 条，少于要求的 {required_references} 条"
        )

    penalty = (
        sum(phrase_hits.values()) * 8
        + len(unsupported_attributions) * 10
        + len(duplicate_sentences) * 8
        + max(0, additionally_count - 2) * 3
        + max(0, english_linkers - 2) * 3
        + max(0, em_dash_count - 2) * 2
        + forced_parallel_count * 6
        + max(0, required_references - reference_count) * 5
    )
    score = max(0, 100 - penalty)
    return PaperStyleAudit(
        status="pass" if not issues else "fail",
        score=score,
        issues=tuple(issues),
        metrics={
            "template_phrase_hits": phrase_hits,
            "unsupported_attributions": unsupported_attributions,
            "duplicate_sentence_count": len(duplicate_sentences),
            "additionally_count": additionally_count,
            "english_linker_count": english_linkers,
            "em_dash_count": em_dash_count,
            "forced_parallel_count": forced_parallel_count,
            "reference_count": reference_count,
            "required_references": required_references,
        },
    )
