"""Verify a relocated installation using only its bundled tools, without model calls."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch


async def verify(root: Path, *, with_latex: bool = False) -> dict:
    expected = root / "runtime/python" / ("python.exe" if sys.platform == "win32" else "bin/python3")
    if Path(sys.executable).resolve() != expected.resolve():
        raise RuntimeError(
            "Run this script with the installation's runtime/python/python.exe"
        )
    backend = root / "backend"
    data = Path(os.environ.get("REMIT_DATA_DIR", str(backend))).resolve()
    (data / "project").mkdir(parents=True, exist_ok=True)
    os.chdir(data)
    sys.path.insert(0, str(backend))
    from loguru import logger
    from app.main import app
    from app.services import paper_library
    from app.tools.local_interpreter import LocalCodeInterpreter
    from app.tools.notebook_serializer import NotebookSerializer
    from app.utils.common_utils import _install_fonts

    logger.remove()
    import pypandoc
    pandoc = Path(pypandoc.get_pandoc_path()).resolve()
    if not pandoc.is_relative_to(root):
        raise RuntimeError("Pandoc came from the build host")
    if "packaged converter" not in pypandoc.convert_text("**packaged converter**", "html", format="md"):
        raise RuntimeError("Bundled Pandoc conversion failed")
    report = {
        "python": str(expected),
        "app_imported": bool(app.routes),
        "writing_skill_loaded": bool(paper_library.context("gmcm")),
        "live_model_tested": False,
        "latex_compilation_tested": False,
        "bundled_pandoc_verified": True,
    }
    with tempfile.TemporaryDirectory(
        prefix="portable-check-", dir=data / "project"
    ) as tmp:
        work = Path(tmp).resolve()
        _install_fonts(work)
        bundled_fonts = [
            p for p in work.iterdir() if p.suffix.lower() in {".ttf", ".otf", ".ttc"}
        ]
        if not bundled_fonts:
            raise RuntimeError("The installation did not provide a plotting font")
        font_path = str(bundled_fonts[0])
        interpreter = LocalCodeInterpreter(
            "portable-check", str(work), NotebookSerializer(str(work)), timeout=45
        )
        # Keep test events local; never touch the user's message archive or active workflow.
        with patch(
            "app.tools.local_interpreter.task_bus.publish_message", new=AsyncMock()
        ):
            try:
                await interpreter.initialize()
                interpreter.add_section("portable-check")
                output, failed, error = await interpreter.execute_code(
                    "import sys,json,numpy,pandas,scipy,h5py,openpyxl,docx,fitz,matplotlib,sklearn,xgboost,shap,statsmodels\n"
                    "matplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"
                    "from matplotlib.font_manager import FontProperties\n"
                    "from matplotlib.ft2font import FT2Font\n"
                    "from matplotlib import font_manager\n"
                    "default_font=font_manager.findfont(font_manager.FontProperties(family=plt.rcParams['font.sans-serif']),fallback_to_default=False)\n"
                    "assert all(ord(c) in FT2Font(default_font).get_charmap() for c in '数学建模')\n"
                    f"font_path={font_path!r}\n"
                    "font_prop=FontProperties(fname=font_path)\n"
                    "assert all(ord(c) in FT2Font(font_path).get_charmap() for c in '数学建模')\n"
                    "pandas.DataFrame({'value':[1,2,3]}).to_csv('sample.csv',index=False)\n"
                    "plt.plot([1,2,3]);plt.title('数学建模',fontproperties=font_prop);plt.savefig('plot.png');plt.close()\n"
                    "print(json.dumps({'python':sys.executable,'sum':int(numpy.array([1,2,3]).sum())}))"
                )
                if failed:
                    raise RuntimeError(error)
                measured = json.loads(
                    next(line for line in output.splitlines() if line.startswith("{"))
                )
                if (
                    Path(measured["python"]).resolve() != expected.resolve()
                    or measured["sum"] != 6
                ):
                    raise RuntimeError(
                        "Kernel did not use the packaged runtime or calculation failed"
                    )
                if (
                    not (work / "sample.csv").is_file()
                    or (work / "plot.png").stat().st_size < 1000
                ):
                    raise RuntimeError("Kernel did not create valid CSV/PNG artifacts")
                report.update(
                    kernel_uses_packaged_python=True,
                    computation_passed=True,
                    csv_export_passed=True,
                    png_export_passed=True,
                    bundled_cjk_font_verified=True,
                )
            finally:
                await interpreter.cleanup()
        if with_latex:
            import fitz
            from app.services import competitions, writing_workspace

            report["latex_compiler"] = shutil.which("xelatex")
            if not Path(report["latex_compiler"] or "").resolve().is_relative_to(root):
                raise RuntimeError("XeLaTeX came from the build host")
            checks = {}
            for contest in ("cumcm", "gmcm", "mcm-icm"):
                build = work / contest
                build.mkdir()
                entry = competitions.select(contest, 2026)
                (build / ".project.json").write_text(
                    json.dumps({"competition": entry}), encoding="utf-8"
                )
                shutil.copy2(work / "plot.png", build / "plot.png")
                fixture = (
                    "Runtime verification fixture.\n"
                    r"\[1+2+3=6\]"
                    "\n"
                    r"\includegraphics[width=0.4\textwidth]{plot.png}"
                    "\n"
                )
                source = competitions.template(build).replace(
                    r"\end{document}", fixture + r"\end{document}"
                )
                (build / "main.tex").write_text(source, encoding="utf-8")
                compiled = writing_workspace.compile_build(build, "main.tex", "fixture")
                if compiled["status"] != "completed":
                    raise RuntimeError(
                        f"{contest} LaTeX check failed: {compiled['log'][-3000:]}"
                    )
                with fitz.open(build / "preview.pdf") as pdf:
                    text = "".join(page.get_text() for page in pdf)
                    if "Runtime verification fixture" not in text:
                        raise RuntimeError(
                            f"{contest} PDF does not contain test content"
                        )
                    images = sum(len(page.get_images()) for page in pdf)
                    if not images:
                        raise RuntimeError(f"{contest} PDF has no exported figure")
                    for page in pdf:
                        page.get_pixmap().tobytes("png")
                    checks[contest] = {
                        "pages": len(pdf),
                        "images": images,
                        "rendered": True,
                    }
            report.update(latex_compilation_tested=True, latex_templates=checks)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-root", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--with-latex",
        action="store_true",
        help="Also compile and render three contest fixtures with the target machine's XeLaTeX",
    )
    args = parser.parse_args()
    destination = args.report.resolve() if args.report else None
    result = asyncio.run(
        verify(args.install_root.resolve(), with_latex=args.with_latex)
    )
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if destination:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
    print(text)
