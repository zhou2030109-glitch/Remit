"""任务流程编排：把题目结构转成求解与写作两个阶段的提示词配置。

章节键（firstPage / eda / quesN / judge ...）贯穿工作流、
UserOutput 与模板配置，是稳定契约。
"""

import json
from typing import Any

from app.core.agents.modeler_agent import ModelerToCoder
from app.core.deliverable_contract import build_question_contract, build_stage_contract
from app.models.user_output import UserOutput
from app.tools.base_interpreter import BaseCodeInterpreter

# 论文骨架（顺序即章节顺序）
_BACK_SECTIONS = ["sensitivity_analysis", "judge"]
_FRONT_SECTIONS = ["firstPage", "RepeatQues", "analysisQues", "modelAssumption", "symbol"]


def _question_keys_of(questions: dict) -> list[str]:
    """抽出 ques1..quesN 键，排除 ques_count。"""
    return [k for k in questions if k.startswith("ques") and k != "ques_count"]


class Flows:
    """围绕一份题目结构，生成各阶段的执行配置。"""

    def __init__(
        self,
        questions: dict[str, str | int],
        user_requirements: str = "",
        citation_brief: str = "",
    ) -> None:
        self.questions = questions
        self.user_requirements = user_requirements.strip()
        # 经代码验证后仍被采用的文献清单；探索实验定案后才可用，允许后置注入
        self.citation_brief = citation_brief.strip()
        self.flows: dict[str, dict] = {}

    # ---- 骨架 ----

    def set_flows(self, ques_count: int) -> None:
        """按问题数量初始化流程节点表。"""
        self.flows = {key: {} for key in self.get_seq(ques_count)}

    def get_seq(self, ques_count: int) -> dict[str, str]:
        """返回章节顺序表（键为章节名）。"""
        ordered = [
            *_FRONT_SECTIONS,
            "eda",
            *(f"ques{i}" for i in range(1, ques_count + 1)),
            *_BACK_SECTIONS,
        ]
        return {key: "" for key in ordered}

    def get_questions_quesx(self) -> dict[str, str | int]:
        """返回 ques1..quesN 的键值对。"""
        return {k: self.questions[k] for k in _question_keys_of(self.questions)}

    def get_questions_quesx_keys(self) -> list[str]:
        """返回 ques1..quesN 的键列表。"""
        return _question_keys_of(self.questions)

    # ---- 求解阶段 ----

    def _citation_block(self) -> str:
        """写作提示统一附带的引用硬约束。"""
        return f"\n\n{self.citation_brief}" if self.citation_brief else ""

    def get_solution_flows(
        self, questions: dict[str, str | int], modeler_response: ModelerToCoder
    ) -> dict[str, dict]:
        """生成求解阶段配置：每问一个编码任务，外加 EDA 与敏感性两个固定节点。"""
        solutions = modeler_response.questions_solution
        requirements = self.user_requirements or "无"

        flows: dict[str, dict] = {}

        eda_contract = build_stage_contract("eda")
        flows["eda"] = {
            "contract": eda_contract,
            "question_text": "数据清洗与探索性分析",
            "model_plan": solutions.get("eda", "对数据进行探索性分析"),
            "coder_prompt": f"""
                        参考建模手给出的解决方案{solutions.get("eda", "对数据进行探索性分析")}
                        对当前目录下数据进行EDA分析(数据清洗,可视化),清洗后的数据保存当前目录下,**不需要复杂的模型**。
                        必须识别真实独立分析单位、重复测量/技术重复、缺失、异常、数据泄露风险和关键区间样本支持度。

                        {eda_contract.prompt_block()}
                    """,
        }

        for key in _question_keys_of(questions):
            contract = build_question_contract(
                question_key=key,
                question_text=str(questions[key]),
                user_requirements=self.user_requirements,
            )
            flows[key] = {
                "contract": contract,
                "question_text": str(questions[key]),
                "model_plan": solutions.get(key, ""),
                "coder_prompt": f"""
                        参考建模手给出的解决方案：{solutions.get(key, "")}
                        完成如下问题：{questions[key]}
                        用户额外交付要求：{requirements}

                        {contract.prompt_block()}
                    """,
            }

        sensitivity_contract = build_stage_contract("sensitivity_analysis")
        flows["sensitivity_analysis"] = {
            "contract": sensitivity_contract,
            "question_text": "最终入选模型的灵敏度与稳健性分析",
            "model_plan": solutions.get("sensitivity_analysis", "对模型进行灵敏度分析"),
            "coder_prompt": f"""
                        参考建模手给出的解决方案{solutions.get("sensitivity_analysis", "对模型进行灵敏度分析")}
                        完成敏感性分析。不得只画一张扰动图；必须覆盖最终入选模型的关键参数、数据扰动和结论稳定性。

                        {sensitivity_contract.prompt_block()}
                    """,
        }
        return flows

    # ---- 写作阶段 ----

    def get_write_flows(
        self, user_output: UserOutput, config_template: dict, bg_ques_all: str
    ) -> dict[str, str]:
        """生成写作阶段的固定章节提示词。"""
        solved = user_output.get_model_build_solve()
        requirements = self.user_requirements or "无"
        citations = self._citation_block()

        def _with_background(section: str, extra: str = "") -> str:
            return (
                f"问题背景{bg_ques_all},用户额外交付要求{requirements},不需要编写代码,"
                f"根据模型的求解的信息{solved}，按照如下模板撰写："
                f"{config_template[section]}，{extra}{citations}"
            )

        return {
            "firstPage": _with_background("firstPage", "撰写标题，摘要，关键词"),
            "RepeatQues": _with_background("RepeatQues", "撰写问题重述"),
            "analysisQues": _with_background("analysisQues", "撰写问题分析"),
            "modelAssumption": _with_background("modelAssumption", "撰写模型假设"),
            "symbol": (
                f"用户额外交付要求{requirements},不需要编写代码,"
                f"根据模型的求解的信息{solved}，按照如下模板撰写："
                f"{config_template['symbol']}，撰写符号说明部分"
            ),
            "judge": _with_background("judge", "撰写模型的评价部分"),
        }

    def get_writer_prompt(
        self,
        key: str,
        coder_response: str,
        code_interpreter: BaseCodeInterpreter,
        config_template: dict,
        model_review: dict[str, Any] | None = None,
    ) -> str:
        """生成单章节的写作提示词。

        Raises:
            ValueError: key 不是可写作章节。
        """
        code_output = code_interpreter.get_code_output(key)
        review_block = (
            json.dumps(model_review, ensure_ascii=False, indent=2)
            if model_review
            else "无单独复核意见"
        )
        background = self.questions["background"]
        requirements = self.user_requirements or "无"
        citations = self._citation_block()

        if key in self.get_questions_quesx():
            return f"""
                    问题背景{background},用户额外交付要求{requirements},不需要编写代码,代码手得到的结果{coder_response},{code_output}。
                    建模手基于真实运行结果的复核如下：{review_block}。
                    必须落实 writer_guidance，并如实披露 weaknesses；不得把复核未证实的内容写成结论。按照如下模板撰写：{config_template[key]}{citations}
                """
        if key == "eda":
            return f"""
                    问题背景{background},不需要编写代码,代码手得到的结果{coder_response},{code_output}。建模手复核：{review_block}。必须如实写明复核局限。按照如下模板撰写：{config_template["eda"]}
                """
        if key == "sensitivity_analysis":
            return f"""
                    问题背景{background},不需要编写代码,代码手得到的结果{coder_response},{code_output}。建模手复核：{review_block}。必须落实复核中的稳定性结论与局限。按照如下模板撰写：{config_template["sensitivity_analysis"]}{citations}
                """
        raise ValueError(f"未知的任务类型: {key}")
