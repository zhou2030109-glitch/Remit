"""多模型评审组与 Gemini Provider 的回归测试。"""

import asyncio
import json
import unittest
from unittest.mock import patch

import httpx
from pydantic import ValidationError

from app.core.llm.errors import ProviderRefusalError
from app.core.llm.providers.gemini import GeminiProvider
from app.core.llm.types import StandardResponse
from app.core.model_council import ModelCouncil
from app.schemas.A2A import (
    CoordinatorToModeler,
    ModelScoutProposal,
    ModelerToCoder,
)
from app.services.redis_manager import redis_manager


class _FakeAsyncClient:
    last_request: dict | None = None

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def post(self, url, *, headers, json):
        self.__class__.last_request = {
            "url": url,
            "headers": headers,
            "json": json,
        }
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "done"},
                                {
                                    "functionCall": {
                                        "name": "execute_code",
                                        "args": {"code": "disp(1)"},
                                    }
                                },
                            ]
                        }
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 11,
                    "candidatesTokenCount": 7,
                    "thoughtsTokenCount": 3,
                },
            },
        )


class GeminiProviderTests(unittest.TestCase):
    def test_native_response_is_standardized(self) -> None:
        provider = GeminiProvider()
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "execute_code",
                    "description": "run MATLAB",
                    "parameters": {
                        "type": "object",
                        "properties": {"code": {"type": "string"}},
                    },
                },
            }
        ]

        with patch("app.core.llm.providers.gemini.httpx.AsyncClient", _FakeAsyncClient):
            response = asyncio.run(
                provider.call(
                    messages=[
                        {"role": "system", "content": "system"},
                        {"role": "user", "content": "solve"},
                    ],
                    model="gemini-test",
                    api_key="secret",
                    base_url="https://relay.example/v1beta",
                    tools=tools,
                    tool_choice="auto",
                    max_tokens=256,
                )
            )

        self.assertEqual(response.content, "done")
        self.assertEqual(response.usage.prompt_tokens, 11)
        self.assertEqual(response.usage.completion_tokens, 10)
        self.assertEqual(response.tool_calls[0].name, "execute_code")
        self.assertEqual(
            json.loads(response.tool_calls[0].arguments)["code"], "disp(1)"
        )
        assert _FakeAsyncClient.last_request is not None
        self.assertTrue(
            _FakeAsyncClient.last_request["url"].endswith(
                "/models/gemini-test:generateContent"
            )
        )
        self.assertIn(
            "systemInstruction", _FakeAsyncClient.last_request["json"]
        )


class ModelCouncilSchemaTests(unittest.TestCase):
    @staticmethod
    def _proposal(recommended_model: str = "GAM") -> dict:
        return {
            "questions": {
                "ques1": {
                    "candidate_models": [
                        {
                            "name": "linear",
                            "role": "baseline",
                            "reason": "simple baseline",
                        },
                        {
                            "name": "GAM",
                            "role": "candidate",
                            "reason": "captures smooth nonlinearity",
                        },
                    ],
                    "recommended_model": recommended_model,
                    "strategy": "Use grouped validation and compare the same features on every fold.",
                    "validation_plan": "Group by subject and report OOF error with bootstrap intervals.",
                    "failure_risks": ["small effective sample size"],
                }
            },
            "global_data_risks": ["repeated measurements"],
            "cross_question_strategy": "Reuse only preprocessing learned inside each training fold.",
        }

    def test_scout_proposal_requires_recommendation_from_candidates(self) -> None:
        parsed = ModelScoutProposal.model_validate(self._proposal())
        self.assertEqual(parsed.questions["ques1"].recommended_model, "GAM")

        with self.assertRaises(ValidationError):
            ModelScoutProposal.model_validate(self._proposal("random forest"))


class _PortfolioCritic:
    """模拟一次完成整题战略审查的模型。"""

    model = "size-sensitive-critic"

    def __init__(self) -> None:
        self.payloads: list[dict] = []

    async def chat(self, *, history, **kwargs):
        payload = json.loads(history[1]["content"])
        self.payloads.append(payload)
        question_keys = list(payload["questions"].keys())
        return StandardResponse(
            content=json.dumps(
                {
                    "question_reviews": {
                        key: {
                            "selected_source": "hybrid",
                            "recommended_models": ["baseline", "candidate"],
                            "rationale": "两套方案需要在相同分组样本外验证上公平比较。",
                            "rejected_options": [],
                            "required_experiments": ["固定分组折并比较OOF指标"],
                            "final_plan": "保留简单基线和候选模型，在完全相同且固定的分组折上比较样本外误差、校准、区间稳定性与失败回退路线，再交由人工确认最终模型。",
                        }
                        for key in question_keys
                    },
                    "global_risks": ["重复测量泄漏"],
                    "minimum_experiment_matrix": ["统一分组交叉验证"],
                    "human_review_focus": ["确认独立单位没有跨折"],
                },
                ensure_ascii=False,
            )
        )


class _RefusingCritic:
    model = "refusing-critic"

    def __init__(self, delay: float = 0) -> None:
        self.delay = delay
        self.call_count = 0

    async def chat(self, *, history, **kwargs):
        self.call_count += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        raise ProviderRefusalError("Anthropic", self.model)


class ModelCouncilReviewRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_multi_question_review_uses_one_strategic_critic_call(self):
        critic = _PortfolioCritic()
        council = ModelCouncil(
            task_id="split-review",
            scout_llm=critic,  # type: ignore[arg-type]
            critic_llm=critic,  # type: ignore[arg-type]
        )
        coordinator = CoordinatorToModeler(
            questions={
                "ques1": "分析变量关系",
                "ques2": "建立分类模型",
                "ques_count": 2,
            },
            ques_count=2,
        )
        primary = ModelerToCoder(
            questions_solution={
                "ques1": "使用线性基线与非线性候选进行分组验证。",
                "ques2": "使用逻辑回归基线与树模型进行分组验证。",
            }
        )
        scout = ModelScoutProposal.model_validate(
            {
                "questions": {
                    key: {
                        "candidate_models": [
                            {
                                "name": "baseline",
                                "role": "baseline",
                                "reason": "simple baseline",
                            },
                            {
                                "name": "candidate",
                                "role": "candidate",
                                "reason": "nonlinear candidate",
                            },
                        ],
                        "recommended_model": "candidate",
                        "strategy": "Compare both models using identical grouped out-of-fold validation.",
                        "validation_plan": "Group by subject and compare OOF metrics with intervals.",
                        "failure_risks": ["small effective sample"],
                    }
                    for key in ("ques1", "ques2")
                },
                "global_data_risks": ["group leakage"],
                "cross_question_strategy": "Reuse only fold-local preprocessing and fixed group splits.",
            }
        )

        original_publish = redis_manager.publish_message

        async def noop(*args, **kwargs):
            return None

        redis_manager.publish_message = noop
        try:
            review, _ = await council.review(
                coordinator=coordinator,
                primary=primary,
                scout=scout,
            )
        finally:
            redis_manager.publish_message = original_publish

        self.assertEqual(set(review.question_reviews), {"ques1", "ques2"})
        self.assertEqual(len(critic.payloads), 1)
        self.assertEqual(
            set(critic.payloads[0]["questions"]), {"ques1", "ques2"}
        )
        self.assertEqual(council.critic_calls_used, 1)

    async def test_refusal_falls_back_and_sensitive_terms_are_redacted(self):
        fallback = _PortfolioCritic()
        refusing = _RefusingCritic()
        council = ModelCouncil(
            task_id="refusal-fallback",
            scout_llm=fallback,  # type: ignore[arg-type]
            critic_llm=refusing,  # type: ignore[arg-type]
        )
        coordinator = CoordinatorToModeler(
            questions={
                "ques1": "分析 NIPT 中孕妇、胎儿和染色体变量的统计关系",
                "ques_count": 1,
            },
            ques_count=1,
        )
        primary = ModelerToCoder(
            questions_solution={
                "ques1": "使用孕妇分组交叉验证分析胎儿染色体指标。"
            }
        )
        scout = ModelScoutProposal.model_validate(
            {
                "questions": {
                    "ques1": {
                        "candidate_models": [
                            {
                                "name": "baseline",
                                "role": "baseline",
                                "reason": "simple baseline",
                            },
                            {
                                "name": "candidate",
                                "role": "candidate",
                                "reason": "nonlinear candidate",
                            },
                        ],
                        "recommended_model": "candidate",
                        "strategy": "Compare every baseline and candidate on the same fixed grouped folds using fold-local preprocessing only.",
                        "validation_plan": "Report grouped out-of-fold metrics, calibration, uncertainty intervals, and fold-wise stability checks.",
                        "failure_risks": ["group leakage"],
                    }
                },
                "global_data_risks": ["group leakage"],
                "cross_question_strategy": "Use fold-local preprocessing.",
            }
        )

        original_publish = redis_manager.publish_message

        async def noop(*args, **kwargs):
            return None

        redis_manager.publish_message = noop
        try:
            review, _ = await council.review(
                coordinator=coordinator,
                primary=primary,
                scout=scout,
            )
        finally:
            redis_manager.publish_message = original_publish

        self.assertIn("ques1", review.question_reviews)
        self.assertEqual(council.fallback_reasons["ques1"], "安全策略拒绝")
        self.assertIn("备用审稿", council.reviewer_by_question["ques1"])
        self.assertEqual(refusing.call_count, 1)
        self.assertEqual(len(fallback.payloads), 1)
        self.assertEqual(council.critic_calls_used, 1)

        critic_payload = council._build_portfolio_review_payload(
            question_keys=["ques1"],
            coordinator=coordinator,
            primary=primary,
            scout=scout,
            label_map={"A": "primary", "B": "scout"},
            redact_for_methodology=True,
        )
        serialized = json.dumps(critic_payload, ensure_ascii=False)
        for term in ("NIPT", "孕妇", "胎儿", "染色体"):
            self.assertNotIn(term, serialized)

        timeout_council = ModelCouncil(
            task_id="timeout-fallback",
            scout_llm=_PortfolioCritic(),  # type: ignore[arg-type]
            critic_llm=_RefusingCritic(delay=1),  # type: ignore[arg-type]
        )
        timeout_council.critic_timeout_seconds = 0.01
        redis_manager.publish_message = noop
        try:
            timeout_review, _ = await timeout_council.review(
                coordinator=coordinator,
                primary=primary,
                scout=scout,
            )
        finally:
            redis_manager.publish_message = original_publish

        self.assertIn("ques1", timeout_review.question_reviews)
        self.assertEqual(
            timeout_council.fallback_reasons["ques1"],
            "审稿超时",
        )


if __name__ == "__main__":
    unittest.main()
