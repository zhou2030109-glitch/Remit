"""开放获取论文全文抓取：只走 OA 通道，抓不到就如实返回失败。

方法卡要求写明"原文位置"，因此必须拿到真正的正文而不是摘要。这里按
OpenAlex OA 位置 → Unpaywall → arXiv 的顺序尝试，每一步都可能失败；失败不是异常，
而是让上层把方法卡标成 ``abstract_only``，避免凭摘要编造章节号。
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlparse

import pymupdf
import requests

from app.utils.log_util import logger

# 单篇 PDF 上限：期刊正文极少超过 30MB，超过多半是扫描合订本
_MAX_PDF_BYTES = 30 * 1024 * 1024
_DOWNLOAD_TIMEOUT = (8, 45)
_METADATA_TIMEOUT = (5, 20)
# 送进 LLM 的正文上限：方法卡只需要模型/方法/实验部分
_MAX_FULLTEXT_CHARS = 60000
# 元数据 API（Unpaywall/arXiv）要求可识别的 UA，礼貌池也据此放行
_API_USER_AGENT = "Remit/1.0 (mathematical-modeling-research)"
# 部分出版商对缺失 Accept/Accept-Language 的请求直接 403，补齐常规请求头即可放行。
# 注意：这挡不住 Cloudflare 级别的机器人识别（MDPI、AAQR 等仍会 403），
# 那种情况按设计降级为 abstract_only，不做任何绕过。
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/pdf,text/html;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# 常见英文论文章节标题；用于给正文切段并记录"原文位置"
_SECTION_PATTERN = re.compile(
    r"^\s*((?:\d+(?:\.\d+)*\.?\s+)?"
    r"(?:abstract|introduction|related\s+work|background|preliminaries|"
    r"problem\s+(?:statement|formulation|definition)|methodology|methods?|"
    r"model(?:ing|ling)?|proposed\s+\w+|approach|algorithm[s]?|framework|"
    r"theoretical\s+analysis|experiments?|experimental\s+(?:setup|results)|"
    r"results?(?:\s+and\s+discussion)?|evaluation|case\s+stud(?:y|ies)|"
    r"discussion|limitations?|conclusions?(?:\s+and\s+future\s+work)?|"
    r"references))\s*$",
    re.IGNORECASE,
)


@dataclass
class FullTextSection:
    """正文中的一个章节，带页码以便回溯原文位置。"""

    heading: str
    start_page: int
    text: str


@dataclass
class FullText:
    """一篇论文的全文抓取结果。"""

    status: str
    source_url: str = ""
    provider: str = ""
    page_count: int = 0
    char_count: int = 0
    sections: list[FullTextSection] = field(default_factory=list)
    error: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status == "ok" and bool(self.sections)

    def digest(self, max_chars: int = _MAX_FULLTEXT_CHARS) -> str:
        """拼成带章节名和页码的正文，供模型引用原文位置。"""
        parts: list[str] = []
        used = 0
        for section in self.sections:
            block = (
                f"\n===== [章节] {section.heading} "
                f"（起始页 {section.start_page}） =====\n{section.text}"
            )
            if used + len(block) > max_chars:
                parts.append(block[: max(0, max_chars - used)])
                break
            parts.append(block)
            used += len(block)
        return "".join(parts).strip()


def _normalize_doi(doi: str | None) -> str:
    """把各种写法的 DOI 归一成 10.xxxx/yyyy。"""
    value = str(doi or "").strip()
    if not value:
        return ""
    value = re.sub(r"^https?://(dx\.)?doi\.org/", "", value, flags=re.IGNORECASE)
    return value.removeprefix("doi:").strip()


def _repository_first(urls: list[str]) -> list[str]:
    """仓储副本优先。

    出版商站点（MDPI、AAQR 等）常有面向机器人的访问拦截，而 PMC、arXiv、机构仓储
    本来就是给程序化获取用的，同一篇 OA 论文换个来源往往就能正常下载。
    """
    repositories = ("pmc", "arxiv", "zenodo", "osf.io", ".edu", "repositor", "hal.")
    ranked = sorted(
        dict.fromkeys(url for url in urls if url),
        key=lambda url: 0 if any(tag in url.lower() for tag in repositories) else 1,
    )
    return ranked


def _resolve_unpaywall_pdfs(doi: str, email: str) -> list[str]:
    """用 Unpaywall 反查全部 OA PDF 直链；无 OA 版本时返回空列表。"""
    if not doi or not email:
        return []
    try:
        response = requests.get(
            f"https://api.unpaywall.org/v2/{quote(doi, safe='')}",
            params={"email": email},
            headers={"User-Agent": _API_USER_AGENT},
            timeout=_METADATA_TIMEOUT,
        )
        if response.status_code != 200:
            return []
        payload = response.json()
    except (requests.RequestException, ValueError):
        return []

    best = payload.get("best_oa_location") or {}
    locations = [best, *(payload.get("oa_locations") or [])]
    return _repository_first(
        [
            str(location["url_for_pdf"])
            for location in locations
            if isinstance(location, dict) and location.get("url_for_pdf")
        ]
    )


def _resolve_europe_pmc_pdf(doi: str) -> str:
    """在 Europe PMC 的开放获取子集里按 DOI 找全文 PDF。"""
    if not doi:
        return ""
    try:
        response = requests.get(
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
            params={
                "query": f'DOI:"{doi}" AND OPEN_ACCESS:Y',
                "format": "json",
                "pageSize": 1,
            },
            headers={"User-Agent": _API_USER_AGENT},
            timeout=_METADATA_TIMEOUT,
        )
        if response.status_code != 200:
            return ""
        results = response.json().get("resultList", {}).get("result") or []
    except (requests.RequestException, ValueError):
        return ""

    for item in results:
        pmcid = str(item.get("pmcid") or "")
        if pmcid:
            return (
                f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextPDF"
            )
    return ""


def _resolve_arxiv_pdf(title: str) -> str:
    """按标题在 arXiv 上找同名预印本；标题不完全一致时放弃。"""
    cleaned = re.sub(r"[^\w\s]", " ", title).strip()
    if len(cleaned) < 12:
        return ""
    try:
        response = requests.get(
            "https://export.arxiv.org/api/query",
            params={
                "search_query": f'ti:"{cleaned}"',
                "max_results": 1,
            },
            headers={"User-Agent": _API_USER_AGENT},
            timeout=_METADATA_TIMEOUT,
        )
        if response.status_code != 200:
            return ""
    except requests.RequestException:
        return ""

    found_title = re.search(r"<entry>.*?<title>(.*?)</title>", response.text, re.DOTALL)
    entry_id = re.search(r"<entry>.*?<id>(.*?)</id>", response.text, re.DOTALL)
    if not found_title or not entry_id:
        return ""

    def _key(value: str) -> str:
        return re.sub(r"[^a-z0-9]", "", value.lower())

    # 标题必须高度一致，否则拿到的是别人的论文，方法卡会张冠李戴
    if _key(found_title.group(1))[:80] != _key(title)[:80]:
        return ""
    return entry_id.group(1).strip().replace("/abs/", "/pdf/")


def _download_pdf(url: str) -> bytes:
    """下载 PDF；非 PDF 内容或超限直接判失败。"""
    parsed = urlparse(url)
    response = requests.get(
        url,
        # 部分出版商还会校验 Referer 是否来自本站落地页
        headers={
            **_BROWSER_HEADERS,
            "Referer": f"{parsed.scheme}://{parsed.netloc}/",
        },
        timeout=_DOWNLOAD_TIMEOUT,
        stream=True,
        allow_redirects=True,
    )
    response.raise_for_status()
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        total += len(chunk)
        if total > _MAX_PDF_BYTES:
            raise ValueError("PDF 超过 30MB 上限")
        chunks.append(chunk)
    content = b"".join(chunks)
    if not content.startswith(b"%PDF-"):
        raise ValueError("下载内容不是 PDF（多半是出版商拦截页）")
    return content


def _split_sections(pages: list[str]) -> list[FullTextSection]:
    """按识别到的章节标题切分正文，并记录每段起始页。"""
    sections: list[FullTextSection] = []
    current = FullTextSection(heading="正文开头", start_page=1, text="")
    buffer: list[str] = []

    for page_number, page_text in enumerate(pages, start=1):
        for line in page_text.splitlines():
            stripped = line.strip()
            if stripped and _SECTION_PATTERN.match(stripped) and len(stripped) < 90:
                current.text = "\n".join(buffer).strip()
                if current.text:
                    sections.append(current)
                buffer = []
                current = FullTextSection(
                    heading=stripped, start_page=page_number, text=""
                )
                continue
            buffer.append(line)

    current.text = "\n".join(buffer).strip()
    if current.text:
        sections.append(current)

    # 参考文献列表对方法卡没有价值，还会挤占正文预算
    return [
        section
        for section in sections
        if not re.match(r"^\s*(\d+\.?\s*)?references?\s*$", section.heading, re.I)
    ]


def _parse_pdf(content: bytes) -> tuple[list[FullTextSection], int]:
    """把 PDF 正文转成带页码的章节列表。"""
    with pymupdf.open(stream=content, filetype="pdf") as document:
        if document.needs_pass:
            raise ValueError("PDF 已加密")
        pages = [page.get_text("text") for page in document]
        page_count = document.page_count
    return _split_sections(pages), page_count


def _fetch_sync(paper: dict[str, Any], email: str) -> FullText:
    """在工作线程里完成解析：全部是同步网络与 CPU 操作。"""
    doi = _normalize_doi(paper.get("doi"))
    title = str(paper.get("title", ""))
    attempts: list[tuple[str, str]] = []

    openalex_urls = _repository_first(
        [
            str(paper.get("oa_pdf_url") or ""),
            *(
                [str(paper.get("oa_url"))]
                if str(paper.get("oa_url") or "").lower().endswith(".pdf")
                else []
            ),
            *[str(url) for url in (paper.get("oa_location_pdf_urls") or [])],
        ]
    )
    attempts.extend(("openalex_oa", url) for url in openalex_urls)

    if doi:
        attempts.extend(
            ("unpaywall", url) for url in _resolve_unpaywall_pdfs(doi, email)
        )
        # PMC 的开放获取子集本就面向程序化获取，比出版商站点稳定得多
        pmc_url = _resolve_europe_pmc_pdf(doi)
        if pmc_url:
            attempts.append(("europe_pmc", pmc_url))
    arxiv = _resolve_arxiv_pdf(title)
    if arxiv:
        attempts.append(("arxiv", arxiv))

    # 同一直链可能被多个来源同时给出，去重避免重复 403 拖慢整轮调研
    seen_urls: set[str] = set()
    deduped: list[tuple[str, str]] = []
    for provider, url in attempts:
        if url and url not in seen_urls:
            seen_urls.add(url)
            deduped.append((provider, url))
    attempts = deduped

    errors: list[str] = []
    if not attempts:
        return FullText(status="no_open_access", error="未找到任何开放获取 PDF 链接")

    for provider, url in attempts:
        try:
            content = _download_pdf(url)
            sections, page_count = _parse_pdf(content)
        except Exception as exc:
            errors.append(f"{provider}: {exc}")
            continue
        if not sections:
            errors.append(f"{provider}: PDF 无可提取正文（可能是扫描件）")
            continue
        return FullText(
            status="ok",
            source_url=url,
            provider=provider,
            page_count=page_count,
            char_count=sum(len(section.text) for section in sections),
            sections=sections,
        )

    return FullText(status="fetch_failed", error="；".join(errors)[:300])


async def fetch_open_access_fulltext(
    paper: dict[str, Any],
    *,
    email: str = "",
) -> FullText:
    """抓取并解析一篇论文的开放获取全文。

    Args:
        paper: 检索结果条目，需含 doi / title / oa_pdf_url 等字段。
        email: Unpaywall 要求的联系邮箱；为空时跳过 Unpaywall 通道。

    Returns:
        全文结果；status 为 ok / no_open_access / fetch_failed / error。
    """
    try:
        return await asyncio.to_thread(_fetch_sync, paper, email)
    except Exception as exc:
        logger.warning(f"全文抓取异常 {paper.get('title', '')[:60]}: {exc}")
        return FullText(status="error", error=str(exc)[:300])
