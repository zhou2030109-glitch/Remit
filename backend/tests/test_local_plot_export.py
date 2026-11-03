"""真实内核覆盖生产字体补丁，不能用纯 matplotlib 冒充产品执行链路。"""

import asyncio
import csv
import importlib.util
import py_compile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.tools.local_interpreter import LocalCodeInterpreter
from app.tools.notebook_serializer import NotebookSerializer
from app.tools import plot_fonts

from unittest.mock import patch
from PIL import Image


@pytest.mark.parametrize("directory", ["普通目录", "中文 O'Brien 数据"])
def test_local_interpreter_exports_chinese_plot(tmp_path, monkeypatch, directory):
    monkeypatch.setattr(
        "app.tools.local_interpreter.task_bus.publish_message", AsyncMock()
    )
    work_dir = tmp_path / directory
    work_dir.mkdir()
    (work_dir / "observed.csv").write_text(
        "x,y\n0,1\n1,3\n2,5\n3,7\n4,9\n5,11\n", encoding="utf-8"
    )

    async def run():
        interpreter = LocalCodeInterpreter(
            "plot-fixture", str(work_dir), NotebookSerializer(str(work_dir)), timeout=30
        )
        try:
            await interpreter.initialize()
            # 哨兵只放在临时目录，确认重复初始化不会删除字体缓存。
            interpreter._run_raw(
                "import matplotlib\nfrom pathlib import Path\nPath('fontlist-fixture.json').write_text('keep', encoding='utf-8')\nmatplotlib.get_cachedir = lambda: work_dir"
            )
            # 同一内核重新初始化不能叠加补丁或重复登记字体。
            before = interpreter._run_raw(
                "from matplotlib import font_manager\nprint(len(font_manager.fontManager.ttflist))"
            )
            await asyncio.to_thread(interpreter._pre_execute_code)
            after = interpreter._run_raw("print(len(font_manager.fontManager.ttflist))")
            assert before == after
            interpreter.add_section("plot-fixture")
            _, failed, error = await interpreter.execute_code(
                "import warnings\nimport numpy as np\nimport pandas as pd\nimport matplotlib.pyplot as plt\nplt.style.use('default')\nwarnings.filterwarnings('error', message='Glyph .* missing from font')\ndata = pd.read_csv('observed.csv')\nfit = np.polyfit(data.x, data.y, 1)\ndata['predicted'] = np.polyval(fit, data.x)\ndata.to_csv('result.csv', index=False)\nplt.plot(data.x, data.predicted); plt.title('数学建模 m³'); plt.savefig('fixture.png'); plt.savefig('fixture.pdf'); plt.close()"
            )
            assert not failed, error
            assert (work_dir / "fontlist-fixture.json").read_text(
                encoding="utf-8"
            ) == "keep"
            assert (work_dir / "fixture.png").stat().st_size > 1000
            assert (work_dir / "fixture.pdf").read_bytes().startswith(b"%PDF-")
            with (work_dir / "result.csv").open(encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            assert len(rows) == 6
            assert (
                max(
                    abs(float(row["predicted"]) - (2 * float(row["x"]) + 1))
                    for row in rows
                )
                < 1e-12
            )
        finally:
            await interpreter.cleanup()

    asyncio.run(run())


def test_font_bootstrap_works_without_python_source(tmp_path):
    compiled = tmp_path / "plot_fonts.pyc"
    py_compile.compile(
        str(Path(plot_fonts.__file__)), cfile=str(compiled), doraise=True
    )
    spec = importlib.util.spec_from_file_location("packaged_plot_fonts", compiled)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # 仅有字节码时也要能生成本地和云端内核的初始化代码。
    code = module.bootstrap(str(tmp_path))
    compile(code, "<font-bootstrap>", "exec")


def test_initialized_kernel_exports_figures_and_preserves_math(tmp_path):
    async def render():
        interpreter = LocalCodeInterpreter(
            "plot-export-test",
            str(tmp_path),
            NotebookSerializer(str(tmp_path)),
            timeout=45,
        )
        with patch(
            "app.tools.local_interpreter.task_bus.publish_message", new=AsyncMock()
        ):
            try:
                await interpreter.initialize()
                interpreter.add_section("plot-export-test")
                output, failed, error = await interpreter.execute_code(
                    "import matplotlib\n"
                    "matplotlib.use('Agg')\n"
                    "import matplotlib.pyplot as plt\n"
                    "fig, ax = plt.subplots()\n"
                    "ax.plot([1,2,3], [2,4,3], label='area (m²)')\n"
                    "title = ax.set_title('volume (m³)')\n"
                    "label = ax.set_xlabel(r'$x^2$')\n"
                    "ax.legend()\n"
                    "fig.savefig('plot.png')\n"
                    "assert title.get_text() == 'volume (m$^3$)'\n"
                    "assert label.get_text() == r'$x^2$'\n"
                    "fig.savefig('plot.pdf')\n"
                    "plt.close(fig)\n"
                    "print('plot export verified')\n"
                )
                assert not failed, error
                assert "plot export verified" in output
            finally:
                await interpreter.cleanup()

    asyncio.run(render())
    with Image.open(tmp_path / "plot.png") as image:
        image.verify()
    assert (tmp_path / "plot.pdf").read_bytes().startswith(b"%PDF-")
