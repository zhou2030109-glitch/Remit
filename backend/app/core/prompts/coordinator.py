"""协调 Agent 的提示词：原题提取与逐题结构化初判。

注意：JSON 字段名是工作流解析契约，改动需同步
workflow / problem_analysis 的解析与校验逻辑。
"""

FORMAT_QUESTIONS_PROMPT = """
用户会贴来一段数学建模赛题。请一次性完成两件事：
1. 原题忠实转录：title、background、ques1、ques2... 必须逐字来自原文，
   你的分析、建议、返修意见一律不得混入这些字段。
2. 对每个正式小问做初步结构化理解：这一步只是读懂题，不是定模型；
   凡是无法从题面确认的内容，写"待附件核验"。

只允许输出如下 JSON：

```json
{
  "title": <题目标题>,
  "background": <题目背景；凡是不属于 title 和 ques1..quesN 的题面内容都归入此处>,
  "ques_count": <问题总数，整数>,
  "ques1": <问题1原文>,
  "ques2": <问题2原文>,
  "ques3": <问题3原文；题面有几问就列几问，ques1..quesN 依此类推>,
  "analysis_summary": <各问的递进关系、共享变量与整体求解边界>,
  "question_analyses": {
    "ques1": {
      "objective": <本问真正要优化、预测、评价或解释的对象>,
      "input_data": [<题面明确给出的输入与所需附件>],
      "decision_variables": [<待求参数、状态或决策变量；没有就说明>],
      "constraints": [<题面、物理、业务上的边界约束>],
      "expected_outputs": [<必须交付的数值、文件、表格或图形>],
      "dependencies": [<与前后小问的输入输出依赖>],
      "risks": [<误读、数据泄漏、不可辨识、近似误差或计算风险>],
      "validation_requirements": [<必须做的模型检验与验收标准>],
      "data_evidence": [<目前只允许引题面事实；待附件核验项必须显式标注>]
    }
  }
}
```

question_analyses 必须覆盖 ques1..quesN 全部小问，九个字段一个都不能少。
"""


COORDINATOR_PROMPT = f"""
    先判断用户输入是否是一道数学建模题。
    如果是：先忠实转录原题，再逐题形成初步结构化理解：
    {FORMAT_QUESTIONS_PROMPT}
    用户的额外交付要求、上一版分析和累计返修意见只能用来改进 question_analyses，
    不得改写原题转录字段，历史意见一条都不许丢。
    本轮还没有扫描任何附件：凡涉及文件行数、字段分布、数据质量的表述，
    必须写"待附件核验"，禁止编造。
    如果不是数学建模题：沿用同一个 JSON 外壳返回拒绝说明。
"""


REFINE_ANALYSIS_PROMPT = """
你的任务：在原题转录不动的前提下，用真实的附件画像和文献摘要校正逐题理解。
title、background、ques_count、ques1... 等原题字段一律禁止改写。
只输出如下 JSON：
{
  "analysis_summary": "各问的递进关系与整体求解边界",
  "question_analyses": {
    "ques1": {
      "objective": "本问真正要优化、预测、评价或解释的对象",
      "input_data": ["题面或附件中实际可用的输入"],
      "decision_variables": ["待求参数、状态或决策变量"],
      "constraints": ["题面、物理、业务上的边界约束"],
      "expected_outputs": ["必须交付的数值、文件或图表"],
      "dependencies": ["与其他小问的输入输出依赖"],
      "risks": ["误读、泄漏、不可辨识或计算风险"],
      "validation_requirements": ["必须执行的检验与验收标准"],
      "data_evidence": ["来自具体附件画像或文献摘要的事实依据"]
    }
  }
}
每个正式小问都必须出现且字段齐全；附件无法证实的内容标注"待验证"，禁止编造。
"""
