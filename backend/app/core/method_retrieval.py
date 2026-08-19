"""面向数学建模问题的三级方法检索。"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


_LATIN_WORD_RE = re.compile(r"[a-z0-9][a-z0-9_+.-]*", re.IGNORECASE)
_CJK_RE = re.compile(r"[\u3400-\u9fff]+")
DEFAULT_LIBRARY_PATH = (
    Path(__file__).resolve().parent / "knowledge" / "modeling_methods.json"
)


@dataclass(frozen=True, slots=True)
class MethodRecommendation:
    """一个带层级来源和可解释得分的候选建模方法。"""

    method_id: str
    method_name: str
    domain_id: str
    domain_name: str
    subdomain_id: str
    subdomain_name: str
    summary: str
    assumptions: tuple[str, ...]
    failure_modes: tuple[str, ...]
    validation: tuple[str, ...]
    matched_keywords: tuple[str, ...]
    score: float
    domain_score: float
    subdomain_score: float
    method_score: float


def _text_tokens(text: str) -> set[str]:
    """将中英文文本转换成适合短查询匹配的词元集合。"""
    normalized = str(text or "").casefold()
    tokens = set(_LATIN_WORD_RE.findall(normalized))
    for chunk in _CJK_RE.findall(normalized):
        tokens.update(chunk)
        tokens.update(chunk[index : index + 2] for index in range(len(chunk) - 1))
        tokens.update(chunk[index : index + 3] for index in range(len(chunk) - 2))
    return {token for token in tokens if token}


def _join_search_text(node: dict[str, Any], fields: Iterable[str]) -> str:
    values: list[str] = []
    for field in fields:
        value = node.get(field, "")
        if isinstance(value, list):
            values.extend(str(item) for item in value)
        else:
            values.append(str(value))
    return " ".join(values)


def _similarity(query: str, candidate: str, keywords: Iterable[str]) -> float:
    query_tokens = _text_tokens(query)
    candidate_tokens = _text_tokens(candidate)
    if not query_tokens or not candidate_tokens:
        return 0.0
    overlap = len(query_tokens & candidate_tokens) / math.sqrt(
        len(query_tokens) * len(candidate_tokens)
    )
    normalized_query = str(query).casefold().replace(" ", "")
    keyword_values = [str(item).strip() for item in keywords if str(item).strip()]
    keyword_hits = sum(
        1
        for keyword in keyword_values
        if keyword.casefold().replace(" ", "") in normalized_query
    )
    keyword_score = keyword_hits / len(keyword_values) if keyword_values else 0.0
    return min(1.0, overlap * 0.65 + keyword_score * 0.35)


class HierarchicalMethodRetriever:
    """按领域、子领域、具体方法逐层计算相关性并返回候选。"""

    def __init__(self, library: list[dict[str, Any]]) -> None:
        """创建检索器。

        Args:
            library: 三级方法库，结构为领域→子领域→方法。
        """
        self._validate_library(library)
        self.library = library

    @classmethod
    def from_default_library(
        cls, path: str | Path | None = None
    ) -> "HierarchicalMethodRetriever":
        """从 Remit 自有方法库创建检索器。

        Args:
            path: 可选的自定义 JSON 方法库路径。

        Returns:
            已加载并完成结构校验的检索器。
        """
        library_path = Path(path).resolve() if path else DEFAULT_LIBRARY_PATH
        with library_path.open("r", encoding="utf-8") as file:
            library = json.load(file)
        if not isinstance(library, list):
            raise ValueError("方法库根节点必须是领域数组")
        return cls(library)

    @property
    def domain_count(self) -> int:
        """返回领域数量。"""
        return len(self.library)

    @property
    def method_count(self) -> int:
        """返回库内唯一方法数量。"""
        return len(
            {
                str(method.get("id", ""))
                for domain in self.library
                for subdomain in domain.get("subdomains", [])
                for method in subdomain.get("methods", [])
            }
        )

    @staticmethod
    def _validate_library(library: list[dict[str, Any]]) -> None:
        """在启动时阻止结构不完整的方法进入检索结果。"""
        if not library:
            raise ValueError("方法库不能为空")
        for domain in library:
            if not str(domain.get("id", "")).strip():
                raise ValueError("领域缺少 id")
            if not domain.get("subdomains"):
                raise ValueError(f"领域 {domain['id']} 缺少子领域")
            for subdomain in domain["subdomains"]:
                if not str(subdomain.get("id", "")).strip():
                    raise ValueError("子领域缺少 id")
                if not subdomain.get("methods"):
                    raise ValueError(f"子领域 {subdomain['id']} 缺少方法")
                for method in subdomain["methods"]:
                    method_id = str(method.get("id", "")).strip()
                    if not method_id:
                        raise ValueError("方法缺少 id")

    def retrieve(self, query: str, top_k: int = 6) -> list[MethodRecommendation]:
        """检索与问题最相关的建模方法。

        Args:
            query: 小问题、数据特征和用户约束组成的检索文本。
            top_k: 返回的最大候选数量。

        Returns:
            按综合得分降序排列的方法候选。

        Raises:
            ValueError: 查询为空或 ``top_k`` 小于一。
        """
        if not str(query).strip():
            raise ValueError("方法检索查询不能为空")
        if top_k < 1:
            raise ValueError("top_k 必须大于等于 1")

        recommendations: list[MethodRecommendation] = []
        for domain in self.library:
            domain_score = _similarity(
                query,
                _join_search_text(domain, ("name", "description", "keywords")),
                domain.get("keywords", []),
            )
            for subdomain in domain.get("subdomains", []):
                subdomain_score = _similarity(
                    query,
                    _join_search_text(subdomain, ("name", "description", "keywords")),
                    subdomain.get("keywords", []),
                )
                for method in subdomain.get("methods", []):
                    method_score = _similarity(
                        query,
                        _join_search_text(
                            method,
                            (
                                "name",
                                "summary",
                                "keywords",
                                "assumptions",
                                "failure_modes",
                                "validation",
                            ),
                        ),
                        method.get("keywords", []),
                    )
                    score = (
                        domain_score * 0.2 + subdomain_score * 0.3 + method_score * 0.5
                    )
                    normalized_query = query.casefold().replace(" ", "")
                    matched_keywords = tuple(
                        str(keyword)
                        for keyword in method.get("keywords", [])
                        if str(keyword).casefold().replace(" ", "") in normalized_query
                    )
                    recommendations.append(
                        MethodRecommendation(
                            method_id=str(method.get("id", "")),
                            method_name=str(method.get("name", "")),
                            domain_id=str(domain.get("id", "")),
                            domain_name=str(domain.get("name", "")),
                            subdomain_id=str(subdomain.get("id", "")),
                            subdomain_name=str(subdomain.get("name", "")),
                            summary=str(method.get("summary", "")),
                            assumptions=tuple(
                                str(item) for item in method.get("assumptions", [])
                            ),
                            failure_modes=tuple(
                                str(item) for item in method.get("failure_modes", [])
                            ),
                            validation=tuple(
                                str(item) for item in method.get("validation", [])
                            ),
                            matched_keywords=matched_keywords,
                            score=round(score, 6),
                            domain_score=round(domain_score, 6),
                            subdomain_score=round(subdomain_score, 6),
                            method_score=round(method_score, 6),
                        )
                    )

        recommendations.sort(
            key=lambda item: (-item.score, item.method_name, item.method_id)
        )
        unique: list[MethodRecommendation] = []
        seen: set[str] = set()
        for recommendation in recommendations:
            identity = recommendation.method_id or recommendation.method_name.casefold()
            if identity in seen:
                continue
            seen.add(identity)
            unique.append(recommendation)
            if len(unique) >= top_k:
                break
        return unique

    def retrieve_for_questions(
        self,
        questions: dict[str, Any],
        *,
        shared_context: dict[str, Any] | None = None,
        top_k: int = 6,
    ) -> dict[str, list[MethodRecommendation]]:
        """为每个正式小问独立检索候选方法。

        Args:
            questions: 协调者拆解后的小问字典。
            shared_context: 数据画像、文献摘要和用户要求等公共上下文。
            top_k: 每个小问返回的最大候选数。

        Returns:
            以小问键为索引的候选方法集合；元数据键不会进入结果。
        """
        context_text = json.dumps(
            shared_context or {}, ensure_ascii=False, sort_keys=True
        )
        results: dict[str, list[MethodRecommendation]] = {}
        for key, value in questions.items():
            normalized_key = str(key)
            if not normalized_key.startswith("ques") or normalized_key == "ques_count":
                continue
            question_text = str(value).strip()
            if not question_text:
                continue
            query = (
                f"{question_text}\n{context_text}" if context_text else question_text
            )
            results[normalized_key] = self.retrieve(query, top_k=top_k)
        return results

    @staticmethod
    def to_payload(
        by_question: dict[str, list[MethodRecommendation]],
    ) -> dict[str, list[dict[str, Any]]]:
        """转换成可落盘、可注入提示词的 JSON 数据。

        Args:
            by_question: 每个小问的检索结果。

        Returns:
            保留层级、解释字段和分项得分的普通字典。
        """
        payload: dict[str, list[dict[str, Any]]] = {}
        for question_key, recommendations in by_question.items():
            payload[question_key] = [
                {
                    "rank": rank,
                    "method_id": item.method_id,
                    "method_name": item.method_name,
                    "hierarchy": [
                        item.domain_name,
                        item.subdomain_name,
                        item.method_name,
                    ],
                    "summary": item.summary,
                    "assumptions": list(item.assumptions),
                    "failure_modes": list(item.failure_modes),
                    "validation": list(item.validation),
                    "matched_keywords": list(item.matched_keywords),
                    "score": item.score,
                    "score_breakdown": {
                        "domain": item.domain_score,
                        "subdomain": item.subdomain_score,
                        "method": item.method_score,
                    },
                }
                for rank, item in enumerate(recommendations, start=1)
            ]
        return payload


class MethodSelectionEngine:
    """把层级检索组织成可注入、可恢复的任务级方法选择证据。"""

    artifact_name = "method_recommendations.json"

    def __init__(
        self,
        retriever: HierarchicalMethodRetriever,
        *,
        top_k: int = 6,
    ) -> None:
        """创建方法选择引擎。

        Args:
            retriever: 负责分层评分的检索器。
            top_k: 每个正式小问保留的方法数量。
        """
        if top_k < 1:
            raise ValueError("top_k 必须大于等于 1")
        self.retriever = retriever
        self.top_k = top_k

    def select(
        self,
        questions: dict[str, Any],
        *,
        shared_context: dict[str, Any] | None = None,
        work_dir: str | Path | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """检索每个正式小问并可选地原子落盘。

        Args:
            questions: 协调者拆解后的问题集合。
            shared_context: 数据画像、文献和用户约束。
            work_dir: 任务工作目录；提供时生成检索证据文件。

        Returns:
            可直接注入 Agent 提示词的 Top-K 字典。
        """
        recommendations = self.retriever.retrieve_for_questions(
            questions,
            shared_context=shared_context,
            top_k=self.top_k,
        )
        payload = self.retriever.to_payload(recommendations)
        if work_dir is not None:
            self.persist_payload(payload, work_dir)
        return payload

    @classmethod
    def persist_payload(
        cls,
        payload: dict[str, list[dict[str, Any]]],
        work_dir: str | Path,
    ) -> Path:
        """原子保存已生成的方法候选，用于断点恢复时修复缺失产物。"""
        output_dir = Path(work_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / cls.artifact_name
        temp_path = output_path.with_suffix(".json.tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(output_path)
        return output_path
