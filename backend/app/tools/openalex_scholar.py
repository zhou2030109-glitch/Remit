"""学术文献检索：OpenAlex 优先，Crossref 兜底。

OpenAlex 需要 API Key；未配置时改走公开的 Crossref REST，
保证文献调研仍能产出可核验的元数据。
"""

import asyncio
import re
from html import unescape
from typing import Any

import requests

from app.schemas.response import ScholarMessage
from app.services.redis_manager import redis_manager

_HTTP_TIMEOUT = (5, 30)

_QUERY_STOPWORDS = {
    "and", "based", "for", "from", "model", "models",
    "of", "the", "using", "with",
}
_DOMAIN_ANCHORS = {
    "congestion", "density", "eda", "flute", "grid", "hpwl",
    "net", "placement", "routing", "rsmt", "steiner", "vlsi", "wirelength",
}
_STRONG_ACRONYMS = {"flute", "hpwl", "rsmt", "vlsi"}

_OPENALEX_SELECT = (
    "id,title,display_name,authorships,cited_by_count,doi,"
    "publication_year,biblio,abstract_inverted_index,"
    "open_access,best_oa_location,primary_location,locations"
)
_CROSSREF_SELECT = (
    "DOI,title,author,published,is-referenced-by-count,abstract,"
    "URL,container-title"
)


def _title_matches_query(query: str, title: str) -> bool:
    """Crossref 词面误报的过滤：摘要送给 LLM 前先拦一道。"""
    wanted = {
        token
        for token in re.findall(r"[a-z0-9]+", query.casefold())
        if len(token) > 2 and token not in _QUERY_STOPWORDS
    }
    found = set(re.findall(r"[a-z0-9]+", title.casefold()))
    overlap = wanted & found
    if overlap & _STRONG_ACRONYMS:
        return True
    return len(overlap) >= 2 and bool(overlap & _DOMAIN_ANCHORS)


def _strip_markup(raw: str) -> str:
    """去 HTML 标签、反转义并压平空白。"""
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", raw)).split())


def _author_short(names: list[str]) -> str:
    """超过三位作者缩成 et al.。"""
    return f"{names[0]} et al." if len(names) > 3 else ", ".join(names)


class OpenAlexScholar:
    """按任务隔离的文献检索客户端。"""

    def __init__(
        self, task_id: str, email: str | None = None, api_key: str | None = None
    ) -> None:
        self.task_id = task_id
        self.email = email
        self.api_key = api_key
        self.base_url = "https://api.openalex.org"

    # ---- 对外入口 ----

    async def search_papers(
        self,
        query: str,
        limit: int = 8,
        open_access_only: bool = False,
    ) -> list[dict[str, Any]]:
        """检索真实文献元数据。

        Args:
            query: 英文检索式。
            limit: 返回上限。
            open_access_only: 只要开放获取文献（方法卡需要读全文；
                Crossref 不支持该过滤，会退化为普通检索）。
        """
        if self.api_key:
            return await self._search_openalex(query, limit, open_access_only)
        return await self._search_crossref(query, limit)

    # ---- Crossref 通道 ----

    async def _search_crossref(self, query: str, limit: int) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "query.bibliographic": query,
            "rows": limit,
            "select": _CROSSREF_SELECT,
        }
        if self.email:
            params["mailto"] = self.email
        headers = {
            "User-Agent": (
                f"Remit/1.0 (mailto:{self.email})"
                if self.email
                else "Remit/1.0 scholarly-search"
            )
        }
        response = await asyncio.to_thread(
            requests.get,
            "https://api.crossref.org/works",
            params=params,
            headers=headers,
            timeout=_HTTP_TIMEOUT,
        )
        response.raise_for_status()
        items = response.json().get("message", {}).get("items", [])

        papers = [p for p in (self._crossref_to_paper(query, it) for it in items) if p]
        await self._report(query, papers, provider="Crossref")
        return papers

    @staticmethod
    def _crossref_to_paper(query: str, item: dict) -> dict[str, Any] | None:
        titles = item.get("title") or []
        title = str(titles[0]).strip() if titles else ""
        if not title or not _title_matches_query(query, title):
            return None

        names = [
            " ".join(
                part
                for part in (
                    str(a.get("given") or "").strip(),
                    str(a.get("family") or "").strip(),
                )
                if part
            )
            for a in item.get("author") or []
        ]
        names = [n for n in names if n]

        date_parts = item.get("published", {}).get("date-parts", [])
        year = (
            date_parts[0][0]
            if date_parts and isinstance(date_parts[0], list) and date_parts[0]
            else None
        )
        doi = str(item.get("DOI") or "").strip()
        venues = item.get("container-title") or []
        venue = str(venues[0]).strip() if venues else ""

        citation = f"{_author_short(names)} ({year or ''}). {title}."
        if venue:
            citation += f" {venue}."
        if doi:
            citation += f" DOI: {doi}"

        return {
            "title": title,
            "abstract": _strip_markup(str(item.get("abstract") or "")),
            "authors": [{"name": n} for n in names],
            "citations_count": int(item.get("is-referenced-by-count") or 0),
            "doi": doi or None,
            "publication_year": year,
            "citation_info": {"venue": venue},
            "citation_format": citation,
            "source": "Crossref",
            "url": item.get("URL") or (f"https://doi.org/{doi}" if doi else ""),
            # Crossref 不提供 OA 位置，全文抓取只能靠 DOI 反查
            "is_oa": False,
            "oa_status": "",
            "oa_url": "",
            "oa_pdf_url": "",
            "oa_landing_url": "",
        }

    # ---- OpenAlex 通道 ----

    async def _search_openalex(
        self, query: str, limit: int, open_access_only: bool
    ) -> list[dict[str, Any]]:
        if not self.email and not self.api_key:
            raise ValueError("配置OpenAlex邮箱获取访问文献权利")

        params: dict[str, Any] = {
            "search": query,
            "per_page": limit,
            "select": _OPENALEX_SELECT,
        }
        if open_access_only:
            params["filter"] = "open_access.is_oa:true"
        if self.email:
            params["mailto"] = self.email
        if self.api_key:
            params["api_key"] = self.api_key
        headers = {
            "User-Agent": f"OpenAlexScholar/1.0 (mailto:{self.email})"
            if self.email
            else "OpenAlexScholar/1.0"
        }

        # 线程池执行并加超时：同步阻塞或对端挂起都不能拖垮事件循环
        response = await asyncio.to_thread(
            requests.get,
            f"{self.base_url}/works",
            params=params,
            headers=headers,
            timeout=_HTTP_TIMEOUT,
        )
        response.raise_for_status()
        works = response.json().get("results", [])

        papers = [self._openalex_to_paper(w) for w in works]
        await self._report(query, papers)
        return papers

    def _openalex_to_paper(self, work: dict) -> dict[str, Any]:
        biblio = work.get("biblio", {})
        open_access = work.get("open_access") or {}
        best_location = work.get("best_oa_location") or {}
        primary_location = work.get("primary_location") or {}
        venue = str((primary_location.get("source") or {}).get("display_name") or "")

        return {
            "title": work.get("display_name") or work.get("title", ""),
            "abstract": self._abstract_from_index(
                work.get("abstract_inverted_index", {})
            ),
            "authors": self._openalex_authors(work),
            "citations_count": work.get("cited_by_count"),
            "doi": work.get("doi"),
            "publication_year": work.get("publication_year"),
            "citation_info": {
                "volume": biblio.get("volume"),
                "issue": biblio.get("issue"),
                "first_page": biblio.get("first_page"),
                "last_page": biblio.get("last_page"),
                "venue": venue,
            },
            "citation_format": self._format_citation(work),
            "source": "OpenAlex",
            "url": work.get("id", ""),
            # 全文抓取只走 OA 通道：这些字段决定方法卡能否读到原文
            "is_oa": bool(open_access.get("is_oa")),
            "oa_status": str(open_access.get("oa_status") or ""),
            "oa_url": str(open_access.get("oa_url") or ""),
            "oa_pdf_url": str(best_location.get("pdf_url") or ""),
            "oa_landing_url": str(best_location.get("landing_page_url") or ""),
            # 出版商站点拦截时可改用其它仓储副本
            "oa_location_pdf_urls": [
                str(loc["pdf_url"])
                for loc in (work.get("locations") or [])
                if isinstance(loc, dict) and loc.get("pdf_url")
            ],
        }

    @staticmethod
    def _openalex_authors(work: dict) -> list[dict]:
        authors = []
        for authorship in work.get("authorships", []):
            author = authorship.get("author")
            if not author:
                continue
            institutions = authorship.get("institutions") or []
            authors.append(
                {
                    "name": author.get("display_name"),
                    "position": authorship.get("author_position"),
                    "institution": institutions[0].get("display_name")
                    if institutions
                    else None,
                }
            )
        return authors

    @staticmethod
    def _abstract_from_index(inverted_index: dict) -> str:
        """把 OpenAlex 的倒排索引还原成摘要文本。"""
        if not inverted_index:
            return ""
        positions = [p for ps in inverted_index.values() for p in ps]
        if not positions:
            return ""
        words = [""] * (max(positions) + 1)
        for word, ps in inverted_index.items():
            for p in ps:
                words[p] = word
        return " ".join(words).strip()

    def _format_citation(self, work: dict) -> str:
        names = [
            a.get("author", {}).get("display_name")
            for a in work.get("authorships", [])
            if a.get("author")
        ]
        title = work.get("display_name") or work.get("title", "")
        year = work.get("publication_year", "")
        doi = work.get("doi", "")
        citation = f"{_author_short(names)} ({year}). {title}."
        if doi:
            citation += f" DOI: {doi}"
        return citation

    # ---- 播报与格式化 ----

    async def _report(
        self, query: str, papers: list[dict[str, Any]], provider: str = "OpenAlex"
    ) -> None:
        payload: dict[str, Any] = {"query": query}
        if provider != "OpenAlex":
            payload["provider"] = provider
        await redis_manager.publish_message(
            self.task_id,
            ScholarMessage(input=payload, output=[p["title"] for p in papers]),
        )

    def papers_to_str(self, papers: list[dict[str, Any]]) -> str:
        """把检索结果渲染成给模型阅读的文本块。"""
        chunks = []
        for paper in papers:
            author_lines = "\n".join(f"- {a['name']}" for a in paper["authors"])
            chunks.append(
                f"标题: {paper['title']}\n"
                f"摘要: {paper['abstract']}\n"
                f"作者:\n{author_lines}\n"
                f"引用次数: {paper['citations_count']}\n"
                f"发表年份: {paper['publication_year']}\n"
                f"引用格式:\n{paper['citation_format']}"
            )
        divider = "=" * 80
        return "".join(f"\n{divider}\n{chunk}\n{divider}" for chunk in chunks)
