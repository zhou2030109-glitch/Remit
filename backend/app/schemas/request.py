"""入站请求的数据模型。"""

from typing import Any

from pydantic import BaseModel

from app.schemas.enums import CompTemplate, FormatOutPut


class ExampleRequest(BaseModel):
    """按示例赛题发起建模的请求体。"""

    example_id: str
    source: str


class Problem(BaseModel):
    """一次建模任务的核心输入。"""

    task_id: str
    ques_all: str = ""
    user_requirements: str = ""
    comp_template: CompTemplate = CompTemplate.CHINA
    format_output: FormatOutPut = FormatOutPut.Markdown

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        """导出时把枚举还原为其线协议取值。"""
        data = super().model_dump(**kwargs)
        data["comp_template"] = self.comp_template.value
        data["format_output"] = self.format_output.value
        return data
