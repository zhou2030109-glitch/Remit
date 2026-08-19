"""最终引用台账：把"读过的文献"收敛成"真正影响了建模的文献"。

参考文献不是读过就能写。一篇文献必须先被某个候选方案引用（有 ``source_card_id``），
再经过小样本代码验证，最后被建模手裁决为 ``adopted`` 或 ``modified``，才允许进入
论文参考文献。被判 ``rejected`` 的文献保留在台账里作为审计痕迹，但不进正文。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.schemas.A2A import MethodCard, PilotDecision, PilotPlan

FINAL_CITATIONS_FILENAME = "final_citations.json"

_USED_DECISIONS = {"adopted", "modified"}
_DECISION_LABELS = {
    "adopted": "采用",
    "modified": "修改后采用",
    "rejected": "放弃",
}


def _flatten_cards(
    cards_by_question: dict[str, list[MethodCard]],
) -> dict[str, MethodCard]:
    return {
        card.card_id: card for cards in cards_by_question.values() for card in cards
    }


def build_citation_ledger(
    *,
    method_cards: dict[str, list[MethodCard]],
    plan: PilotPlan | None,
    decision: PilotDecision | None,
) -> dict[str, Any]:
    """汇总每张方法卡从"被引用"到"被采纳"的完整链路。

    Args:
        method_cards: 逐题方法卡。
        plan: 探索实验协议，提供候选与方法卡的引用关系。
        decision: 定案结果，提供每张卡的去留裁决。

    Returns:
        引用台账；``entries`` 是全部有裁决的卡，``used`` 是可进参考文献的子集。
    """
    cards = _flatten_cards(method_cards)
    citing_candidates: dict[str, list[dict[str, str]]] = {}
    for question_key, question_plan in (plan.questions if plan else {}).items():
        for candidate in question_plan.candidates:
            card_id = candidate.source_card_id.strip()
            if card_id:
                citing_candidates.setdefault(card_id, []).append(
                    {
                        "question_key": question_key,
                        "candidate_name": candidate.name,
                        "adaptation": candidate.adaptation,
                    }
                )

    entries: list[dict[str, Any]] = []
    for question_key, item in (decision.questions if decision else {}).items():
        selected = item.selected_model.strip().casefold()
        for judgement in item.citation_decisions:
            card = cards.get(judgement.card_id.strip())
            links = [
                link
                for link in citing_candidates.get(judgement.card_id.strip(), [])
                if link["question_key"] == question_key
            ]
            entries.append(
                {
                    "card_id": judgement.card_id.strip(),
                    "question_key": question_key,
                    "decision": judgement.decision,
                    "decision_label": _DECISION_LABELS.get(
                        judgement.decision, judgement.decision
                    ),
                    "evidence": judgement.evidence,
                    "influence": judgement.influence,
                    "candidate_name": links[0]["candidate_name"] if links else "",
                    "adaptation": links[0]["adaptation"] if links else "",
                    # 被引候选恰好是入选模型时，该文献直接决定了最终方案
                    "is_selected_model": bool(
                        links
                        and links[0]["candidate_name"].strip().casefold() == selected
                    ),
                    "title": card.title if card else "",
                    "citation": (card.citation or card.title) if card else "",
                    "publication_year": card.publication_year if card else None,
                    "doi": card.doi if card else None,
                    "url": card.url if card else "",
                    "evidence_level": card.evidence_level if card else "",
                    "method": card.method if card else "",
                }
            )

    entries.sort(key=lambda item: (item["question_key"], item["card_id"]))
    used = [item for item in entries if item["decision"] in _USED_DECISIONS]
    # 同一篇文献可能服务多个小问，参考文献里只能出现一次
    unique_used: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in used:
        key = str(item["doi"] or item["citation"] or item["card_id"]).casefold()
        if key in seen:
            continue
        seen.add(key)
        unique_used.append(item)

    return {
        "entries": entries,
        "used": unique_used,
        "rejected": [item for item in entries if item["decision"] == "rejected"],
        "used_count": len(unique_used),
        "judged_count": len(entries),
    }


def persist_citation_ledger(work_dir: str | Path, ledger: dict[str, Any]) -> str:
    """把引用台账落盘，供论文手、审批卡和人工核对共用。"""
    path = Path(work_dir) / FINAL_CITATIONS_FILENAME
    path.write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return FINAL_CITATIONS_FILENAME


def load_citation_ledger(work_dir: str | Path) -> dict[str, Any]:
    """读取引用台账；缺失或损坏时返回空台账。"""
    path = Path(work_dir) / FINAL_CITATIONS_FILENAME
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def build_citation_brief(ledger: dict[str, Any]) -> str:
    """把"最终可引用文献"拼成注入论文手的硬约束段落。"""
    used = ledger.get("used") or []
    if not used:
        return ""
    lines = [
        "【最终参考文献清单（只有这些文献真正影响了建模并被采用）】",
        "写作硬约束：正文引用只能来自本清单；清单外的文献一律不得引用，"
        "也不要为了凑数补充泛泛的综述。每条引用都要出现在真正用到该方法的章节。",
    ]
    for index, item in enumerate(used, start=1):
        lines.append(
            f"[{index}] {item.get('citation') or item.get('title')}"
            f"（{item.get('question_key')}，{item.get('decision_label')}）"
        )
        influence = str(item.get("influence") or "").strip()
        if influence:
            lines.append(f"    对建模的影响：{influence}")
        adaptation = str(item.get("adaptation") or "").strip()
        if adaptation:
            lines.append(f"    我们做的修改：{adaptation}")
        evidence = str(item.get("evidence") or "").strip()
        if evidence:
            lines.append(f"    实验依据：{evidence}")

    rejected = ledger.get("rejected") or []
    if rejected:
        names = "；".join(
            str(item.get("citation") or item.get("title") or item.get("card_id"))
            for item in rejected[:5]
        )
        lines.append(
            f"以下文献经代码验证后已放弃，禁止引用，可在模型评价中如实说明为何不采用：{names}"
        )
    return "\n".join(lines)[:4000]


def build_citation_table(ledger: dict[str, Any]) -> dict[str, Any]:
    """把台账转成审批卡可直接渲染的表格。"""
    rows = [
        {
            "小问": str(item.get("question_key", "")),
            "候选方案": str(item.get("candidate_name", "")) or "—",
            "参考文献": str(item.get("citation") or item.get("title") or "")[:90],
            "证据": "全文" if item.get("evidence_level") == "full_text" else "摘要",
            "裁决": str(item.get("decision_label", "")),
            "依据": str(item.get("evidence", ""))[:80],
        }
        for item in ledger.get("entries") or []
    ]
    return {
        "filename": FINAL_CITATIONS_FILENAME,
        "columns": ["小问", "候选方案", "参考文献", "证据", "裁决", "依据"],
        "rows": rows,
        "preview_limited_to_rows": len(rows),
    }
