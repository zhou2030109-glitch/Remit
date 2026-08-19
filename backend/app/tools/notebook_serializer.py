"""任务过程笔记本（.ipynb）的增量写入器。

编码 Agent 每执行一段代码，就把源码、输出、图表同步进笔记本，
同时按小节缓存 HTML 输出供写作阶段引用。
"""

from pathlib import Path

import ansi2html  # type: ignore[import-unresolved]
import nbformat
from nbformat import v4 as nbf


class NotebookSerializer:
    """维护单个任务目录下的 notebook.ipynb 文件。"""

    def __init__(self, work_dir: str | None = None, notebook_name: str = "notebook.ipynb") -> None:
        self.nb = nbf.new_notebook()
        self.notebook_path: str | None = None
        self.initialized = True
        # 小节名 -> 该小节累计的 HTML 输出
        self.segmentation_output_content: dict[str, str] = {}
        self.current_segmentation: str = ""
        self.init_notebook(work_dir, notebook_name)

    def init_notebook(self, work_dir: str | None = None, notebook_name: str = "notebook.ipynb") -> None:
        """确定 notebook 落盘位置；目录为空时仅驻留内存。"""
        if not work_dir:
            return
        if not notebook_name.lower().endswith(".ipynb"):
            notebook_name += ".ipynb"
        self.notebook_path = str(Path(work_dir) / notebook_name)

    @staticmethod
    def ansi_to_html(ansi_text: str) -> str:
        """终端 ANSI 文本转 HTML，用于保留输出配色。"""
        return ansi2html.Ansi2HTMLConverter().convert(ansi_text)

    def write_to_notebook(self) -> None:
        """把当前内存状态刷到磁盘。"""
        if self.notebook_path:
            Path(self.notebook_path).write_text(nbformat.writes(self.nb), encoding="utf-8")

    # ---- 单元追加 ----

    def add_code_cell_to_notebook(self, code: str) -> None:
        self.nb["cells"].append(nbf.new_code_cell(source=code))
        self.write_to_notebook()

    def add_code_cell_output_to_notebook(self, output: str) -> None:
        """把执行输出挂到最近一个代码单元，并计入当前小节的缓存。"""
        html_content = self.ansi_to_html(output)
        if self.current_segmentation:
            self.segmentation_output_content[self.current_segmentation] = (
                self.segmentation_output_content.get(self.current_segmentation, "")
                + html_content
            )
        self.nb["cells"][-1]["outputs"].append(
            nbf.new_output(output_type="display_data", data={"text/html": html_content})
        )
        self.write_to_notebook()

    def add_code_cell_error_to_notebook(self, error: str) -> None:
        self.nb["cells"][-1]["outputs"].append(
            nbf.new_output(
                output_type="error",
                ename="Error",
                evalue="Error message",
                traceback=[error],
            )
        )
        self.write_to_notebook()

    def add_image_to_notebook(self, image: str, mime_type: str) -> None:
        self.nb["cells"][-1]["outputs"].append(
            nbf.new_output(output_type="display_data", data={mime_type: image})
        )
        self.write_to_notebook()

    def add_markdown_to_notebook(self, content: str, title: str | None = None) -> None:
        if title:
            content = f"##### {title}:\n{content}"
        self.nb["cells"].append(nbf.new_markdown_cell(content))
        self.write_to_notebook()

    def add_markdown_segmentation_to_notebook(self, content: str, segmentation: str) -> None:
        """开一个新小节：切换当前小节并初始化其输出缓存。"""
        self.current_segmentation = segmentation
        self.segmentation_output_content[segmentation] = ""
        self.add_markdown_to_notebook(content, segmentation)

    def get_notebook_output_content(self, segmentation: str) -> str:
        """取回某小节累计的 HTML 输出。"""
        return self.segmentation_output_content[segmentation]
