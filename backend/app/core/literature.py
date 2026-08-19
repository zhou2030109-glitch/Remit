"""文献调研：检索 → 筛选 → 读全文 → 方法卡，为建模提供可核对的方法依据。

完整链路：赛题拆分 → 生成英文检索词 → OpenAlex/Crossref 检索 → 按标题摘要筛选
每问 2~3 篇 → 尽量抓开放获取全文 → 提取"问题/模型/适用条件/优缺点/原文位置"方法卡。

任何一步失败都降级返回可审计的失败结果，绝不阻塞主工作流。
"""

import asyncio
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.core.activity import publish_activity
from app.core.agents.modeler_agent import repair_json
from app.core.llm.llm import LLM
from app.schemas.A2A import MethodCard
from app.tools.fulltext_fetcher import FullText, fetch_open_access_fulltext
from app.tools.openalex_scholar import OpenAlexScholar
from app.utils.log_util import logger

_MAX_QUERIES = 6
_PAPERS_PER_QUERY = 6
_ABSTRACT_CHARS = 400
# 每问只精读 2~3 篇：读得少而深，好过读得多而浅
_MAX_SELECTED_PER_QUESTION = 3
_MIN_SELECTED_PER_QUESTION = 2
# 全文抓取和方法卡提取都很慢，限制并发避免拖垮整个调研节点
_MAX_CONCURRENT_FULLTEXT = 4
_MAX_CONCURRENT_CARDS = 3
METHOD_CARDS_FILENAME = "method_cards.json"

_QUERY_PROMPT = """你是数学建模竞赛的文献调研助手。根据题目各小问，生成用于学术检索的英文检索式。
要求：
- 每个正式小问 1-2 条检索式，总数不超过 {max_queries} 条
- 检索式是英文短语（方法领域 + 问题类型），例如 "short-term PM2.5 concentration forecasting"、"multi-criteria decision making TOPSIS ranking"
- 面向可落地的经典与近年方法，不要过于冷门
- 检索式必须包含赛题领域限定词（如 VLSI 电路布局、城市交通、水质监测等），
  避免命中交通网络、能源系统、网络安全、医疗、金融等同名跨领域主题
只输出 JSON：{{"queries": [{{"question_key": "ques1", "query": "..."}}]}}"""

_SCREEN_PROMPT = """你是数学建模竞赛的文献筛选专家。下面是按小问检索到的真实文献（编号/标题/年份/被引/是否开放获取/摘要）。
为每个正式小问挑出最相关的 {min_selected}-{max_selected} 篇，后续会精读它们的全文。

筛选标准（按重要性排序）：
1. 摘要中的方法能直接迁移到该小问的问题类型，而不是只有领域词重合
2. 方法在竞赛环境可落地：无 GPU、单问约 30 分钟、MATLAB/Python 可实现
3. 同等相关时优先 is_oa=true（能读到全文，方法卡才有原文位置）
4. 同等条件下优先高被引或近年文献；不要选综述以外全是理论证明的文章

硬约束：
- paper_index 必须来自下面列表中真实存在的编号，禁止编造
- 一篇文献可以同时服务多个小问
- 宁可少选也不要凑数：明显不相关的不要选
- 与赛题领域明显无关的检索命中（例如赛题为电路布局时出现的交通网络、能源
  系统、网络安全、医疗、金融等领域论文）一律不得选择，即使检索式字面命中
- 每问选不到 2 篇就如实少选，允许某问 0 篇；不得为了凑足数量硬选无关文献

只输出 JSON：
{{"questions": {{"ques1": {{"selections": [
  {{"paper_index": 3, "relevance_reason": "为什么这篇能解决本小问（一句话，指名方法）"}}
]}}}}}}"""

_CARD_PROMPT = """你是数学建模竞赛的方法提取专家。下面给你一篇论文的内容，以及它要服务的赛题小问。
请提取一张"方法卡"，让建模 Agent 不看原文也能直接照着实现。

硬约束：
- 只能写论文里真实存在的内容，读不到就留空或写"原文未说明"，禁止编造
- evidence_level=full_text 时，source_locations 必须给出真实章节名，page 只能填正文里
  「（起始页 N）」标注的那个 N（PDF 内页序），不要填期刊排版页码；
  quote 必须是原文中的英文原句片段；evidence_level=abstract_only 时 source_locations 必须为空数组
- key_steps 要写成可执行步骤（输入 → 处理 → 输出），不要写成名词罗列
- competition_adaptation 写明搬到竞赛环境要做哪些简化或替换（数据规模、算力、实现语言）

只输出 JSON：
{{
 "problem_solved": "论文要解决的问题（含数据形态与目标）",
 "method": "采用的模型或算法（写清名称与核心结构）",
 "key_steps": ["可执行步骤，3-6 条"],
 "key_parameters": ["关键超参数或设定及论文取值"],
 "applicable_conditions": ["方法成立需要满足的数据/假设条件，2-5 条"],
 "strengths": ["相对基线的优点，含论文报告的量化提升"],
 "limitations": ["缺点、失败场景与论文自述局限"],
 "source_locations": [{{"section": "章节名", "page": 页码整数或null, "quote": "原文英文片段"}}],
 "competition_adaptation": "迁移到本赛题小问需要做的修改"
}}"""

_REVIEW_PROMPT = """你是数学建模竞赛的方法调研专家。基于下面已精读的文献方法卡，为每个小问产出建模选型结论。
硬约束：
- 只能引用方法卡中真实存在的文献，禁止编造
- 推荐必须可在竞赛环境落地：无 GPU、单问计算预算约 30 分钟、MATLAB/Python 实现
- SOTA 思路只取可落地部分；创新点是"在经典方法上加改进"量级，不是复现深度模型
只输出 JSON：
{{"questions": {{"ques1": {{
  "mainstream_methods": ["该领域常用方法 2-4 个"],
  "sota_summary": "近年最有代表性的思路一句话",
  "recommended_baseline": "必跑的简单基线",
  "innovation_idea": "一个可落地的创新点（在 X 基础上做 Y）",
  "key_citations": ["作者 (年份). 标题" 形式，1-3 条，必须来自方法卡列表]
}}}}, "overall_notes": "跨问的共性建议"}}"""


def _digest_papers(papers: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for index, paper in enumerate(papers, 1):
        abstract = str(paper.get("abstract") or "")[:_ABSTRACT_CHARS]
        lines.append(
            f"[{index}] {paper.get('title', '')}（{paper.get('publication_year', '?')}，"
            f"被引 {paper.get('citations_count', 0)}，"
            f"is_oa={str(bool(paper.get('is_oa'))).lower()}，"
            f"检索式「{paper.get('matched_query', '')}」）\n摘要：{abstract}"
        )
    return "\n\n".join(lines)


def _digest_cards(cards: list[MethodCard]) -> str:
    """把方法卡压成综述阶段的输入。"""
    lines: list[str] = []
    for card in cards:
        lines.append(
            f"[{card.card_id}] {card.question_key} | {card.citation or card.title}\n"
            f"  证据级别：{card.evidence_level}\n"
            f"  解决问题：{card.problem_solved}\n"
            f"  方法：{card.method}\n"
            f"  适用条件：{'；'.join(card.applicable_conditions) or '未说明'}\n"
            f"  优点：{'；'.join(card.strengths) or '未说明'}\n"
            f"  缺点：{'；'.join(card.limitations) or '未说明'}"
        )
    return "\n\n".join(lines)


async def _json_chat(llm: LLM, system: str, user: str) -> dict[str, Any] | None:
    response = await llm.chat(
        history=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        agent_name="ModelerAgent",
        publish=False,
    )
    return repair_json(response.content or "")


def build_literature_brief(review: dict[str, Any]) -> str:
    """把文献调研结果压缩成注入 prompt 的短摘要。"""
    questions = review.get("questions")
    if not isinstance(questions, dict) or not questions:
        return ""
    lines: list[str] = ["【文献调研结论（基于真实检索）】"]
    for key, card in questions.items():
        if not isinstance(card, dict):
            continue
        methods = "、".join(
            str(item) for item in (card.get("mainstream_methods") or [])[:4]
        )
        lines.append(
            f"{key}：主流方法：{methods or '未知'}；"
            f"SOTA 思路：{card.get('sota_summary', '')}；"
            f"必跑基线：{card.get('recommended_baseline', '')}；"
            f"创新点：{card.get('innovation_idea', '')}"
        )
        citations = card.get("key_citations") or []
        if citations:
            lines.append(
                "  可引用文献：" + "；".join(str(item) for item in citations[:3])
            )
    notes = str(review.get("overall_notes", "")).strip()
    if notes:
        lines.append(f"共性建议：{notes}")
    return "\n".join(lines)[:3000]


def build_method_cards(review: dict[str, Any]) -> dict[str, list[MethodCard]]:
    """从调研结果中恢复逐题方法卡；结构损坏时安全跳过。"""
    grouped: dict[str, list[MethodCard]] = {}
    for payload in review.get("method_cards") or []:
        try:
            card = MethodCard.model_validate(payload)
        except ValidationError:
            continue
        grouped.setdefault(card.question_key, []).append(card)
    return grouped


def build_method_card_brief(cards_by_question: dict[str, list[MethodCard]]) -> str:
    """把方法卡压成注入建模手 prompt 的可执行摘要。"""
    if not cards_by_question:
        return ""
    lines = [
        "【文献方法卡（已精读入选文献，候选方案必须标注引用了哪张卡）】",
    ]
    for question_key in sorted(cards_by_question):
        for card in cards_by_question[question_key]:
            evidence = "已读全文" if card.evidence_level == "full_text" else "仅摘要"
            lines.append(
                f"[{card.card_id}] {question_key} · {evidence} · "
                f"{card.citation or card.title}"
            )
            lines.append(f"  解决问题：{card.problem_solved}")
            lines.append(f"  方法：{card.method}")
            if card.key_steps:
                lines.append("  关键步骤：" + " → ".join(card.key_steps[:6]))
            if card.applicable_conditions:
                lines.append("  适用条件：" + "；".join(card.applicable_conditions[:4]))
            if card.strengths:
                lines.append("  优点：" + "；".join(card.strengths[:3]))
            if card.limitations:
                lines.append("  缺点：" + "；".join(card.limitations[:3]))
            if card.competition_adaptation:
                lines.append(f"  竞赛适配：{card.competition_adaptation}")
            if card.source_locations:
                spots = "；".join(
                    f"{item.section}" + (f" p{item.page}" if item.page else "")
                    for item in card.source_locations[:3]
                    if item.section
                )
                if spots:
                    lines.append(f"  原文位置：{spots}")
    return "\n".join(lines)[:8000]


def _persist_review(work_dir: str | Path, review: dict[str, Any]) -> None:
    path = Path(work_dir) / "literature_review.json"
    path.write_text(
        json.dumps(review, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    cards_path = Path(work_dir) / METHOD_CARDS_FILENAME
    cards = review.get("method_cards") or []
    cards_path.write_text(
        json.dumps({"method_cards": cards}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _failed_review(
    *,
    reason: str,
    queries: list[dict[str, str]] | None = None,
    errors: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "status": "failed",
        "paper_count": 0,
        "questions": {},
        "searched_queries": [item["query"] for item in (queries or [])],
        "papers": [],
        "kept_paper_count": 0,
        "filtered_out": {"count": 0, "items": []},
        "selected_papers": {},
        "method_cards": [],
        "fulltext_stats": {"attempted": 0, "succeeded": 0},
        "errors": errors or [reason],
        "summary": reason,
    }


def _converge_selected_papers(
    papers: list[dict[str, Any]],
    selected: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """把检索命中收敛为“精选文献 + 可审计的过滤清单”。

    ``selected`` 是逐问筛选结果；这里只保留被任意小问选中的论文（按标题去重），
    其余命中全部进入 ``filtered_out`` 供前端展示和人工核对，避免无关检索结果
    混入文献列表。被选中的论文会保留 ``relevance_reason`` 说明为什么入选。

    Returns:
        (精选论文列表, 过滤清单)。
    """
    kept: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    for question_key in sorted(selected):
        for paper in selected[question_key]:
            title = str(paper.get("title", "")).strip().casefold()
            if not title or title in seen_titles:
                continue
            seen_titles.add(title)
            kept.append(paper)

    kept_titles = {
        str(paper.get("title", "")).strip().casefold() for paper in kept
    }
    filtered_items = [
        {
            "title": str(paper.get("title", "")),
            "matched_query": str(paper.get("matched_query", "")),
            "reason": "未被任何小问精选（相关性不足）",
        }
        for paper in papers
        if str(paper.get("title", "")).strip().casefold() not in kept_titles
    ]
    return kept, {
        "count": len(filtered_items),
        "items": filtered_items[:50],
    }


async def _screen_papers(
    *,
    llm: LLM,
    question_items: dict,
    papers: list[dict[str, Any]],
    guidance_block: str,
) -> dict[str, list[dict[str, Any]]]:
    """按标题和摘要为每个小问挑出最相关的 2~3 篇。

    Returns:
        小问 → 入选文献列表（附 relevance_reason）；筛选失败时按检索式归属回退。
    """
    payload = await _json_chat(
        llm,
        _SCREEN_PROMPT.format(
            min_selected=_MIN_SELECTED_PER_QUESTION,
            max_selected=_MAX_SELECTED_PER_QUESTION,
        ),
        json.dumps({"questions": question_items}, ensure_ascii=False)
        + guidance_block
        + "\n\n【候选文献】\n"
        + _digest_papers(papers),
    )
    raw_questions = (payload or {}).get("questions")
    selected: dict[str, list[dict[str, Any]]] = {}
    if isinstance(raw_questions, dict):
        for question_key, entry in raw_questions.items():
            if str(question_key) not in question_items or not isinstance(entry, dict):
                continue
            picks: list[dict[str, Any]] = []
            seen: set[int] = set()
            for item in entry.get("selections") or []:
                if not isinstance(item, dict):
                    continue
                try:
                    index = int(item.get("paper_index"))
                except (TypeError, ValueError):
                    continue
                if not 1 <= index <= len(papers) or index in seen:
                    continue
                seen.add(index)
                picks.append(
                    {
                        **papers[index - 1],
                        "relevance_reason": str(item.get("relevance_reason", ""))[:300],
                    }
                )
                if len(picks) >= _MAX_SELECTED_PER_QUESTION:
                    break
            if picks:
                selected[str(question_key)] = picks

    # 模型漏掉的小问用检索式归属兜底，保证每问都有可读文献
    for question_key in question_items:
        if selected.get(str(question_key)):
            continue
        fallback = [
            {**paper, "relevance_reason": "按该小问的检索式命中，未经模型复核"}
            for paper in papers
            if paper.get("question_key") == str(question_key)
        ][:_MIN_SELECTED_PER_QUESTION]
        if fallback:
            selected[str(question_key)] = fallback
    return selected


async def _extract_method_card(
    *,
    llm: LLM,
    card_id: str,
    question_key: str,
    question_text: str,
    paper: dict[str, Any],
    fulltext: FullText,
) -> MethodCard | None:
    """从一篇论文（全文优先，其次摘要）提取方法卡。"""
    read_full = fulltext.succeeded
    body = (
        fulltext.digest()
        if read_full
        else str(paper.get("abstract") or "")[: _ABSTRACT_CHARS * 4]
    )
    if not body.strip():
        return None

    evidence_level = "full_text" if read_full else "abstract_only"
    payload = await _json_chat(
        llm,
        _CARD_PROMPT,
        json.dumps(
            {
                "question_key": question_key,
                "question_text": question_text,
                "paper_title": paper.get("title", ""),
                "publication_year": paper.get("publication_year"),
                "evidence_level": evidence_level,
                "evidence_note": (
                    "下面是论文正文（含章节名与起始页），可以给出原文位置"
                    if read_full
                    else "只拿到摘要，禁止编造章节位置，source_locations 必须为空数组"
                ),
            },
            ensure_ascii=False,
        )
        + "\n\n【论文内容】\n"
        + body,
    )
    if not isinstance(payload, dict):
        return None

    locations = payload.get("source_locations") if read_full else []
    try:
        return MethodCard.model_validate(
            {
                "card_id": card_id,
                "question_key": question_key,
                "title": str(paper.get("title", "")) or card_id,
                "citation": str(paper.get("citation_format", "")),
                "publication_year": paper.get("publication_year"),
                "doi": paper.get("doi"),
                "url": str(paper.get("url", "")),
                "evidence_level": evidence_level,
                "fulltext_source": fulltext.source_url if read_full else "",
                "relevance_reason": str(paper.get("relevance_reason", "")),
                "problem_solved": str(payload.get("problem_solved", "")),
                "method": str(payload.get("method", "")),
                "key_steps": [str(item) for item in payload.get("key_steps") or []][:8],
                "key_parameters": [
                    str(item) for item in payload.get("key_parameters") or []
                ][:8],
                "applicable_conditions": [
                    str(item) for item in payload.get("applicable_conditions") or []
                ][:6],
                "strengths": [str(item) for item in payload.get("strengths") or []][:5],
                "limitations": [str(item) for item in payload.get("limitations") or []][
                    :5
                ],
                "source_locations": locations if isinstance(locations, list) else [],
                "competition_adaptation": str(
                    payload.get("competition_adaptation", "")
                ),
            }
        )
    except ValidationError as exc:
        logger.warning(f"方法卡 {card_id} 结构无效: {exc}")
        return None


async def _build_method_cards(
    *,
    task_id: str,
    llm: LLM,
    question_items: dict,
    selected: dict[str, list[dict[str, Any]]],
    openalex_email: str,
) -> tuple[list[MethodCard], dict[str, Any]]:
    """对入选文献抓全文并提取方法卡。

    Returns:
        (方法卡列表, 全文抓取统计)。
    """
    jobs: list[tuple[str, str, dict[str, Any]]] = []
    for question_key in sorted(selected):
        for order, paper in enumerate(selected[question_key], start=1):
            jobs.append((f"{question_key}-C{order}", question_key, paper))
    if not jobs:
        return [], {"attempted": 0, "succeeded": 0, "failures": []}

    await publish_activity(
        task_id,
        f"正在读取 {len(jobs)} 篇入选文献的开放获取全文…",
        category="info",
    )
    fulltext_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_FULLTEXT)

    async def _fetch(paper: dict[str, Any]) -> FullText:
        async with fulltext_semaphore:
            return await fetch_open_access_fulltext(paper, email=openalex_email)

    fulltexts = await asyncio.gather(
        *(_fetch(paper) for _, _, paper in jobs), return_exceptions=True
    )
    normalized: list[FullText] = [
        item
        if isinstance(item, FullText)
        else FullText(status="error", error=str(item)[:200])
        for item in fulltexts
    ]
    succeeded = sum(1 for item in normalized if item.succeeded)
    # 记录抓不到全文的真实原因（多为出版商拦截或无 OA 版本），
    # 让"只有摘要"这件事可审计，而不是看起来像调研偷懒
    failures = [
        f"{job[0]} {job[2].get('title', '')[:60]}: {item.status} {item.error[:120]}"
        for job, item in zip(jobs, normalized)
        if not item.succeeded
    ]
    await publish_activity(
        task_id,
        f"全文读取完成：{succeeded}/{len(jobs)} 篇拿到正文，正在提取方法卡…",
        category="llm",
    )

    card_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_CARDS)

    async def _card(
        job: tuple[str, str, dict[str, Any]], fulltext: FullText
    ) -> MethodCard | None:
        card_id, question_key, paper = job
        async with card_semaphore:
            return await _extract_method_card(
                llm=llm,
                card_id=card_id,
                question_key=question_key,
                question_text=str(question_items.get(question_key, "")),
                paper=paper,
                fulltext=fulltext,
            )

    results = await asyncio.gather(
        *(_card(job, fulltext) for job, fulltext in zip(jobs, normalized)),
        return_exceptions=True,
    )
    cards: list[MethodCard] = []
    for result in results:
        if isinstance(result, BaseException):
            logger.warning(f"方法卡提取失败: {result}")
            continue
        if result is not None:
            cards.append(result)
    return cards, {
        "attempted": len(jobs),
        "succeeded": succeeded,
        "failures": failures[:8],
    }


async def run_literature_review(
    *,
    task_id: str,
    llm: LLM,
    scholar: OpenAlexScholar,
    questions: dict,
    work_dir: str | Path,
    extra_guidance: str = "",
    openalex_email: str = "",
) -> dict[str, Any]:
    """执行文献调研全流程；失败降级但保留可审计原因。

    Args:
        task_id: 任务 ID，用于活动播报。
        llm: 生成检索式、筛选、提取方法卡与综述的模型（复用建模手模型）。
        scholar: OpenAlex 检索客户端。
        questions: 协调者拆解的小问字典。
        work_dir: 产物落盘目录。
        extra_guidance: 人工退回意见，检索与总结都必须遵循。
        openalex_email: 联系邮箱，Unpaywall 反查 OA 全文时必填。

    Returns:
        文献调研结果字典；status 明确标记 completed/partial/failed。
    """
    question_items = {
        key: value
        for key, value in questions.items()
        if str(key).startswith("ques") and str(key) != "ques_count"
    }
    if not question_items:
        review = _failed_review(reason="没有正式小问，无法生成文献检索式")
        _persist_review(work_dir, review)
        return review

    guidance_block = (
        f"\n\n【人工调整意见，检索方向与总结必须遵循】\n{extra_guidance}"
        if extra_guidance.strip()
        else ""
    )
    try:
        await publish_activity(task_id, "正在生成文献检索式…", category="llm")
        query_payload = await _json_chat(
            llm,
            _QUERY_PROMPT.format(max_queries=_MAX_QUERIES),
            json.dumps({"questions": question_items}, ensure_ascii=False)
            + guidance_block,
        )
        raw_queries = (query_payload or {}).get("queries") or []
        queries = [
            {
                "question_key": str(item.get("question_key", "")),
                "query": str(item.get("query", "")).strip(),
            }
            for item in raw_queries
            if isinstance(item, dict) and str(item.get("query", "")).strip()
        ][:_MAX_QUERIES]
        if not queries:
            logger.warning("文献调研：未生成有效检索式，降级跳过")
            review = _failed_review(reason="模型未生成有效文献检索式")
            _persist_review(work_dir, review)
            return review

        papers: list[dict[str, Any]] = []
        search_errors: list[str] = []
        seen_titles: set[str] = set()
        for item in queries:
            await publish_activity(
                task_id,
                f"正在检索文献：{item['query']}",
                category="info",
            )
            # 第二轮只查开放获取：方法卡要读全文，OA 命中率决定证据质量
            for open_access_only in (False, True):
                try:
                    found = await scholar.search_papers(
                        item["query"],
                        limit=_PAPERS_PER_QUERY,
                        open_access_only=open_access_only,
                    )
                except Exception as exc:
                    logger.warning(f"文献检索失败 {item['query']}: {exc}")
                    search_errors.append(f"{item['query']}: {exc}")
                    break
                for paper in found:
                    title = str(paper.get("title", "")).strip().casefold()
                    if not title or title in seen_titles:
                        continue
                    seen_titles.add(title)
                    papers.append(
                        {
                            **paper,
                            "matched_query": item["query"],
                            "question_key": item["question_key"],
                        }
                    )
        if not papers:
            logger.warning("文献调研：检索无结果，降级跳过")
            review = _failed_review(
                reason="所有检索式均未取得可用文献",
                queries=queries,
                errors=search_errors or ["检索返回 0 篇文献"],
            )
            _persist_review(work_dir, review)
            return review

        await publish_activity(
            task_id,
            f"检索到 {len(papers)} 篇文献，正在按标题和摘要筛选最相关的…",
            category="llm",
        )
        selected = await _screen_papers(
            llm=llm,
            question_items=question_items,
            papers=papers,
            guidance_block=guidance_block,
        )
        cards, fulltext_stats = await _build_method_cards(
            task_id=task_id,
            llm=llm,
            question_items=question_items,
            selected=selected,
            openalex_email=openalex_email,
        )

        await publish_activity(
            task_id,
            f"已产出 {len(cards)} 张方法卡，正在形成选型结论…",
            category="llm",
        )
        review = await _json_chat(
            llm,
            _REVIEW_PROMPT,
            json.dumps({"questions": question_items}, ensure_ascii=False)
            + guidance_block
            + "\n\n【已精读文献的方法卡】\n"
            + (_digest_cards(cards) or "无（全文与摘要均未能提取出方法卡）")
            + "\n\n【检索到的全部文献摘要】\n"
            + _digest_papers(papers),
        )
        if not isinstance(review, dict) or not isinstance(
            review.get("questions"), dict
        ):
            logger.warning("文献调研：综述结构无效，降级跳过")
            failed = _failed_review(
                reason="已检索到文献，但模型未生成有效方法卡",
                queries=queries,
                errors=search_errors,
            )
            failed["method_cards"] = [card.model_dump(mode="json") for card in cards]
            failed["fulltext_stats"] = fulltext_stats
            _persist_review(work_dir, failed)
            return failed

        # 相关性收敛：展示层只保留被任意小问精选的文献，其余进入过滤清单
        kept_papers, filtered_out = _converge_selected_papers(papers, selected)
        # 方法卡是本节点的核心产物：一张都没有时不能标记为完成
        review["status"] = "partial" if search_errors or not cards else "completed"
        review["searched_queries"] = [item["query"] for item in queries]
        review["paper_count"] = len(papers)
        review["kept_paper_count"] = len(kept_papers)
        review["filtered_out"] = filtered_out
        review["errors"] = search_errors
        review["papers"] = [
            {
                "title": paper.get("title", ""),
                "publication_year": paper.get("publication_year"),
                "citations_count": paper.get("citations_count", 0),
                "citation_format": paper.get("citation_format", ""),
                "matched_query": paper.get("matched_query", ""),
                "source": paper.get("source", ""),
                "doi": paper.get("doi"),
                "url": paper.get("url", ""),
                "is_oa": bool(paper.get("is_oa")),
                "relevance_reason": str(paper.get("relevance_reason", "")),
            }
            for paper in kept_papers
        ]
        review["selected_papers"] = {
            question_key: [
                {
                    "title": paper.get("title", ""),
                    "publication_year": paper.get("publication_year"),
                    "citation_format": paper.get("citation_format", ""),
                    "relevance_reason": paper.get("relevance_reason", ""),
                    "doi": paper.get("doi"),
                    "url": paper.get("url", ""),
                    "is_oa": bool(paper.get("is_oa")),
                }
                for paper in items
            ]
            for question_key, items in selected.items()
        }
        review["method_cards"] = [card.model_dump(mode="json") for card in cards]
        review["fulltext_stats"] = fulltext_stats
        _persist_review(work_dir, review)
        return review
    except Exception as exc:
        logger.warning(f"文献调研失败，降级跳过: {exc}")
        review = _failed_review(reason=f"文献调研异常：{exc}")
        _persist_review(work_dir, review)
        return review
