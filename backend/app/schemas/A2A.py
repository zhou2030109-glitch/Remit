"""Agent 间通信数据模型定义。"""

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class QuestionAnalysis(BaseModel):
    """协调者针对一个正式小问形成的结构化题目理解。"""

    objective: str
    input_data: list[str]
    decision_variables: list[str]
    constraints: list[str]
    expected_outputs: list[str]
    dependencies: list[str]
    risks: list[str]
    validation_requirements: list[str]
    data_evidence: list[str] = Field(default_factory=list)


class SourceLocation(BaseModel):
    """方法卡中某条结论在原文中的位置，用于人工回溯核对。"""

    section: str = ""
    page: int | None = None
    quote: str = ""


class MethodCard(BaseModel):
    """从单篇文献中提取、建模 Agent 可直接使用的方法卡。

    ``evidence_level`` 明确区分读到全文与只读到摘要：只有 ``full_text``
    的卡片才允许声称原文章节位置。
    """

    card_id: str = Field(min_length=1)
    question_key: str = Field(min_length=1)
    title: str = Field(min_length=1)
    citation: str = ""
    publication_year: int | None = None
    doi: str | None = None
    url: str = ""
    evidence_level: Literal["full_text", "abstract_only"] = "abstract_only"
    fulltext_source: str = ""
    relevance_reason: str = ""
    problem_solved: str = ""
    method: str = ""
    key_steps: list[str] = Field(default_factory=list)
    key_parameters: list[str] = Field(default_factory=list)
    applicable_conditions: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    source_locations: list[SourceLocation] = Field(default_factory=list)
    competition_adaptation: str = ""


class CoordinatorToModeler(BaseModel):
    """协调者传递给建模手的数据结构。"""

    questions: dict
    ques_count: int
    original_problem: str = ""
    analysis_summary: str = ""
    question_analyses: dict[str, QuestionAnalysis] = Field(default_factory=dict)
    user_requirements: str = ""
    # 调研节点注入：确定性数据画像与文献调研摘要，供选型参考
    data_profile: dict | None = None
    literature_brief: str = ""
    # 逐题方法卡：从入选文献（尽量全文）提取的可直接落地方法
    method_cards: dict[str, list[MethodCard]] = Field(default_factory=dict)
    # 本地三级方法库检索结果：每个正式小问的 Top-K 候选及可解释得分
    method_recommendations: dict[str, list[dict[str, Any]]] = Field(
        default_factory=dict
    )


class ModelerToCoder(BaseModel):
    """建模手传递给代码手的数据结构。"""

    questions_solution: dict[str, str]


class ModelScoutQuestionProposal(BaseModel):
    """独立探索模型针对一个小问提出的候选集。"""

    candidate_models: list["ModelCandidate"] = Field(min_length=2)
    recommended_model: str = Field(min_length=1)
    strategy: str = Field(min_length=40)
    validation_plan: str = Field(min_length=20)
    failure_risks: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_recommendation(self) -> "ModelScoutQuestionProposal":
        names = {item.name.strip().casefold() for item in self.candidate_models}
        if self.recommended_model.strip().casefold() not in names:
            raise ValueError("recommended_model 必须来自 candidate_models")
        if not any(item.role == "baseline" for item in self.candidate_models):
            raise ValueError("candidate_models 必须包含 baseline")
        return self


class ModelScoutProposal(BaseModel):
    """独立模型探索者的结构化候选方案。"""

    questions: dict[str, ModelScoutQuestionProposal]
    global_data_risks: list[str] = Field(min_length=1)
    cross_question_strategy: str = Field(min_length=20)


class ModelCouncilQuestionReview(BaseModel):
    """盲审者对匿名方案 A/B 的逐题裁决。"""

    selected_source: Literal["A", "B", "hybrid"]
    recommended_models: list[str] = Field(min_length=1)
    rationale: str = Field(min_length=20)
    rejected_options: list[str] = Field(default_factory=list)
    required_experiments: list[str] = Field(min_length=1)
    final_plan: str = Field(min_length=40)


class ModelCouncilReview(BaseModel):
    """独立盲审者给出的模型评审组结论。"""

    question_reviews: dict[str, ModelCouncilQuestionReview]
    global_risks: list[str] = Field(min_length=1)
    minimum_experiment_matrix: list[str] = Field(min_length=1)
    human_review_focus: list[str] = Field(min_length=1)


class ModelCouncilResult(BaseModel):
    """可落盘、可追溯的完整模型评审组记录。"""

    scout_model: str
    critic_model: str
    critic_scope: Literal["strategic_portfolio"] = "strategic_portfolio"
    critic_call_limit: int = 1
    critic_calls_used: int = 0
    critic_status: Literal["completed", "fallback"] = "completed"
    blind_label_map: dict[str, Literal["primary", "scout"]]
    reviewer_by_question: dict[str, str] = Field(default_factory=dict)
    fallback_reasons: dict[str, str] = Field(default_factory=dict)
    scout_proposal: ModelScoutProposal
    review: ModelCouncilReview


class ModelCandidate(BaseModel):
    """一次模型返修中需要真实运行的候选方法。"""

    name: str = Field(min_length=1)
    role: Literal["baseline", "candidate"]
    reason: str = Field(min_length=5)


class ModelRevisionPlan(BaseModel):
    """建模手根据代码运行证据制定的换模方案。"""

    diagnosis: str = Field(min_length=10)
    rejected_models: list[str] = Field(default_factory=list)
    candidate_models: list[ModelCandidate] = Field(min_length=2)
    selected_model: str = Field(min_length=1)
    revised_strategy: str = Field(min_length=40)
    validation_plan: str = Field(min_length=20)
    acceptance_criteria: str = Field(min_length=10)

    @model_validator(mode="after")
    def validate_model_choice(self) -> "ModelRevisionPlan":
        """保证返修方案包含基线，且入选模型属于候选集合。"""
        if not any(item.role == "baseline" for item in self.candidate_models):
            raise ValueError("candidate_models 必须包含一个 baseline")
        candidate_names = {
            item.name.strip().casefold() for item in self.candidate_models
        }
        if self.selected_model.strip().casefold() not in candidate_names:
            raise ValueError("selected_model 必须出现在 candidate_models 中")
        return self


class ModelExecutionReview(BaseModel):
    """建模手对代码真实运行结果的结构化复核。"""

    verdict: Literal["accept", "refine", "manual_review"]
    summary: str = Field(min_length=20)
    evidence: list[str] = Field(min_length=1)
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    writer_guidance: str = Field(min_length=10)
    revision_plan: ModelRevisionPlan | None = None

    @model_validator(mode="before")
    @classmethod
    def discard_non_refinement_plan(cls, data: Any) -> Any:
        """非返修结论忽略模型误填的返修计划。

        LLM 经常在 ``manual_review`` 或 ``accept`` 时按示例输出空对象或
        仅供解释的候选信息。这些内容不具备可执行语义，也不应触发严格的
        ``ModelRevisionPlan`` 字段校验。
        """
        if isinstance(data, dict) and data.get("verdict") != "refine":
            normalized = dict(data)
            normalized["revision_plan"] = None
            return normalized
        return data

    @model_validator(mode="after")
    def validate_revision_requirement(self) -> "ModelExecutionReview":
        """要求所有返修结论同时给出可执行的新模型方案。"""
        if self.verdict == "refine" and self.revision_plan is None:
            raise ValueError("verdict=refine 时必须提供 revision_plan")
        return self


class PilotCandidate(BaseModel):
    """探索实验中需要真实小样本运行的一个候选方案。

    ``source_card_id`` 把候选方案与方法卡绑定，使"参考了哪篇文献、做了什么修改"
    在代码验证前就固定下来，验证后才能判断该文献是否真的影响了建模。
    """

    name: str = Field(min_length=1)
    role: Literal["baseline", "candidate"] = "candidate"
    # 中文描述信息密度高，阈值过严会把有效协议误杀成格式错误
    approach: str = Field(min_length=6)
    # 引用的方法卡 ID；纯经典基线或自研方案留空
    source_card_id: str = ""
    # 相对原文做了什么修改（换数据、简化、改参数、只取其中一步等）
    adaptation: str = ""


class PilotQuestionPlan(BaseModel):
    """一个小问的探索实验协议。"""

    candidates: list[PilotCandidate] = Field(min_length=2, max_length=4)
    sampling_rule: str = Field(min_length=5)
    primary_metric: str = Field(min_length=2)
    higher_is_better: bool = False
    time_budget_minutes: int = Field(default=5, ge=1, le=15)

    @model_validator(mode="after")
    def validate_baseline(self) -> "PilotQuestionPlan":
        if not any(item.role == "baseline" for item in self.candidates):
            raise ValueError("candidates 必须包含一个 baseline")
        return self


class PilotPlan(BaseModel):
    """全部小问的探索实验协议。"""

    questions: dict[str, PilotQuestionPlan]


class CitationDecision(BaseModel):
    """一张方法卡在代码验证后的去留裁决。

    只有 ``adopted`` 和 ``modified`` 才算真正影响了建模，也只有它们能进参考文献。
    """

    card_id: str = Field(min_length=1)
    decision: Literal["adopted", "modified", "rejected"]
    evidence: str = Field(min_length=4)
    influence: str = ""


class PilotDecisionItem(BaseModel):
    """基于探索实验数据对一个小问的最终选型。"""

    selected_model: str = Field(min_length=1)
    revised_strategy: str = Field(min_length=40)
    justification: str = Field(min_length=6)
    citation_decisions: list[CitationDecision] = Field(default_factory=list)


class PilotDecision(BaseModel):
    """基于探索实验数据的整体选型定案。"""

    questions: dict[str, PilotDecisionItem]


class CoderToWriter(BaseModel):
    """代码手传递给写作手的数据结构。"""

    code_response: str | None = None
    code_output: str | None = None
    created_images: list[str] | None = None


class WriterResponse(BaseModel):
    """写作手的响应数据结构。"""

    response_content: Any
    footnotes: list[tuple[str, str]] | None = None
