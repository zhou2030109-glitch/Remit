"""独立论文项目：证据快照、源码版本与隔离编译。"""

import hashlib
import json
import os
import re
import shutil
import subprocess
from app.utils.tex_process import run_xelatex
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config.setting import settings

EDITABLE = {".tex", ".bib", ".sty", ".cls", ".txt"}
ASSETS = {".png", ".jpg", ".jpeg", ".pdf", ".eps", ".svg", ".csv", ".ttf", ".otf"}
TEMPLATE = r"""\documentclass[UTF8,a4paper,12pt,fontset=fandol]{ctexart}
\usepackage[margin=2.5cm]{geometry}
\usepackage{amsmath,amssymb,graphicx,booktabs}
\title{数学建模论文}
\author{}
\date{}
\begin{document}
\maketitle
\begin{abstract}
在此撰写摘要。建模完成后，可点击「生成论文」将已验证的结果组织为初稿。
\end{abstract}
\section{问题重述}
\section{模型假设与符号说明}
\section{模型建立与求解}
\section{模型检验与评价}
\end{document}
"""


def now() -> str:
    """返回用于持久化的 UTC 时间。"""
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value: Any) -> None:
    """原子替换 JSON 并落盘，断电后只会看到旧版本或新版本。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path: Path) -> dict[str, Any]:
    """读取项目状态，不把损坏的状态当作空白项目。"""
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def figure_overrides(root: Path, revision: str) -> dict[str, str]:
    """Use reviewed re-exports only for the exact original evidence snapshot.

    Original plots remain immutable. A stale, edited, or out-of-project override
    fails closed instead of silently substituting a different experiment's plot.
    """
    manifest = read_json(root / "figure_overrides.json")
    if manifest.get("input_revision") != revision:
        return {}
    result = {}
    for name, entry in manifest.get("files", {}).items():
        original = (root / ".inputs" / revision / "assets" / name).resolve()
        replacement = (root / entry["file"]).resolve()
        if not original.is_relative_to(
            (root / ".inputs" / revision / "assets").resolve()
        ) or not replacement.is_relative_to((root / "assets").resolve()):
            raise ValueError("图表修订路径越出素材目录")
        if (
            hashlib.sha256(original.read_bytes()).hexdigest() != entry["source_sha256"]
            or hashlib.sha256(replacement.read_bytes()).hexdigest()
            != entry["rendered_sha256"]
        ):
            raise ValueError("图表修订版本已变化，请重新核验后再用于论文")
        result[name] = replacement.relative_to(root).as_posix()
    return result


def paper_root(task_root: Path) -> Path:
    """返回独立论文目录，并阻止符号链接越界。"""
    root = (task_root / "paper").resolve()
    if not root.is_relative_to(task_root.resolve()):
        raise ValueError("论文目录越出项目边界")
    return root


def resolve_source(root: Path, name: str) -> Path:
    """只接受项目内部可编辑文件路径。"""
    parts = Path(name).parts
    if (
        not parts
        or "\\" in name
        or ":" in name
        or name.startswith("/")
        or any(
            part.startswith(".")
            or part.endswith((".", " "))
            or re.match(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)", part, re.I)
            for part in parts
        )
    ):
        raise ValueError("无效的源码文件路径")
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()) or path.suffix.lower() not in EDITABLE:
        raise ValueError("仅可编辑项目内的 TeX、Bib、Sty、Cls 和文本文件")
    return path


def digest(content: str) -> str:
    """源码内容的并发修改标识。"""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def ensure_workspace(task_root: Path) -> Path:
    """创建论文项目；已有源码及用户修改保持不变。"""
    root = paper_root(task_root)
    root.mkdir(parents=True, exist_ok=True)
    if not (root / "workspace.json").is_file():
        source = task_root / "res.tex"
        from app.services.competitions import project_competition, template

        initial = template(task_root) if project_competition(task_root) else TEMPLATE
        (root / "main.tex").write_text(
            source.read_text(encoding="utf-8") if source.is_file() else initial,
            encoding="utf-8",
        )
        if source.is_file():
            for asset in task_root.iterdir():
                if (
                    asset.is_file()
                    and asset.suffix.lower() in ASSETS
                    and asset.name != "res.pdf"
                ):
                    shutil.copy2(asset, root / asset.name)
        write_json(
            root / "workspace.json",
            {"main": "main.tex", "created_at": now(), "generation": {"status": "idle"}},
        )
    return root


def collect_evidence(
    task_root: Path, state: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Path]]:
    """读取实际证据与文件指纹，供同步和提案冲突检查共用。"""
    payload = {
        key: state.get(key)
        for key in (
            "problem",
            "questions",
            "ques_count",
            "modeler_response",
            "solution_results",
            "citation_ledger",
            "analysis_response",
            "data_profile",
            "literature_review",
            "workflow_features",
            "node_outcomes",
            "pilot_skipped",
            "pilot_results",
            "pilot_decision",
        )
    }
    sources: dict[str, Path] = {}
    from app.services.competitions import project_competition

    payload["competition"] = project_competition(task_root)
    from app.core.project_audit import pilot_evidence_notice

    payload["evidence_notice"] = pilot_evidence_notice(state)
    for result in (state.get("solution_results") or {}).values():
        for name in set(
            result.get("artifacts", []) + result.get("paper_ready_images", [])
        ):
            source = (task_root / name).resolve()
            if source.is_relative_to(task_root.resolve()) and source.is_file():
                sources[source.relative_to(task_root.resolve()).as_posix()] = source
    payload["artifact_hashes"] = {
        name: hashlib.sha256(path.read_bytes()).hexdigest()
        for name, path in sources.items()
    }
    return payload, sources


def evidence_digest(payload: dict[str, Any]) -> str:
    """沿用快照标识算法，排除计时等非科学状态变化。"""
    return digest(json.dumps(payload, sort_keys=True, ensure_ascii=False))[:20]


def sync_results(task_root: Path, state: dict[str, Any]) -> dict[str, Any]:
    """保存只读的计算证据版本，更新输入指针，不覆盖论文源码。"""
    root = ensure_workspace(task_root)
    payload, sources = collect_evidence(task_root, state)
    revision = evidence_digest(payload)
    snapshot = root / ".inputs" / revision
    assets = snapshot / "assets"
    if not (snapshot / "evidence.json").is_file():
        assets.mkdir(parents=True, exist_ok=True)
        for name, source in sources.items():
            target = (assets / name).resolve()
            if target.is_relative_to(assets.resolve()):
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        write_json(snapshot / "evidence.json", payload)
    summary = {
        "revision": revision,
        "synced_at": now(),
        "sections": list((state.get("solution_results") or {}).keys()),
        "asset_count": sum(1 for path in assets.rglob("*") if path.is_file()),
    }
    from app.services.paper_plan import build_plan

    plan_path = root / "chapter-plan.json"
    previous_plan = read_json(plan_path)
    if previous_plan.get("evidence_revision") != revision:
        write_json(
            plan_path,
            {
                "evidence_revision": revision,
                "sections": build_plan(payload),
                "notes": "",
                "previous_notes_stale": bool(previous_plan.get("notes")),
            },
        )
    # 独立文件避免与编辑/生成状态的写入竞争。
    write_json(root / "input.json", summary)
    return summary


def source_files(root: Path) -> list[Path]:
    """列出可编辑文件和图表，排除内部状态、编译缓存与历史。"""
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.resolve().is_relative_to(root.resolve())
        and not any(part.startswith(".") for part in path.relative_to(root).parts)
        and path.suffix.lower() in EDITABLE | ASSETS
        and path.name != "preview.pdf"
    )


def project_revision(root: Path) -> str:
    """计算编译输入版本，PDF 能据此显示是否落后于源码。"""
    hashed = hashlib.sha256()
    if document_mode(root) != "full_paper":
        hashed.update(document_mode(root).encode())
    hashed.update(read_json(root / "workspace.json").get("main", "main.tex").encode())
    for path in source_files(root):
        hashed.update(path.relative_to(root).as_posix().encode())
        hashed.update(path.read_bytes())
    return hashed.hexdigest()


def document_mode(root: Path, name: str | None = None) -> str:
    """Mode belongs to a source document, not the next-generation preference."""
    from app.core.paper_mode import paper_mode

    meta = read_json(root / "workspace.json")
    return paper_mode(
        meta.get("document_modes", {}).get(
            name or meta.get("main", "main.tex"), "full_paper"
        )
    )


def save_source(root: Path, name: str, content: str, expected: str | None) -> str:
    """带版本检查保存，旧版本留档供恢复。"""
    if len(content.encode("utf-8")) > 2 * 1024 * 1024:
        raise ValueError("单个源码文件不能超过 2MB")
    path = resolve_source(root, name)
    existing = path.read_text(encoding="utf-8") if path.is_file() else None
    if (digest(existing) if existing is not None else None) != expected:
        raise FileExistsError(
            "文件已被其他窗口修改，请重新载入后合并；当前编辑内容仍保留在编辑器"
        )
    if existing is not None and existing != content:
        history = root / ".history" / uuid4().hex
        history.mkdir(parents=True)
        (history / "source.txt").write_text(existing, encoding="utf-8")
        write_json(history / "version.json", {"file": name, "saved_at": now()})
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)
    return digest(content)


def trim_build_cache(root: Path) -> None:
    """限制自动编译缓存；只删除已解析并确认在本项目内部的缓存目录。"""
    parent = (root / ".build").resolve()
    if not parent.is_relative_to(root.resolve()):
        return
    builds = sorted(
        parent.iterdir(), key=lambda path: path.stat().st_mtime, reverse=True
    )
    for build in builds[3:]:
        resolved = build.resolve()
        if resolved.parent == parent and build.is_dir() and not build.is_symlink():
            shutil.rmtree(resolved)


def prepare_build(root: Path) -> tuple[Path, str, str]:
    """锁内复制完整输入，后续编译期间允许继续编辑。"""
    revision = project_revision(root)
    build = root / ".build" / uuid4().hex
    build.mkdir(parents=True)
    for source in source_files(root):
        target = build / source.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    main = read_json(root / "workspace.json").get("main", "main.tex")
    write_json(build / ".document.json", {"mode": document_mode(root, main)})
    resolve_source(build, main)
    return build, main, revision


def compile_build(build: Path, main: str, revision: str) -> dict[str, Any]:
    """在源码快照上运行两轮 XeLaTeX，返回真实日志及错误行。"""
    compiler = shutil.which(settings.LATEX_ENGINE or "xelatex")
    mode = read_json(build / ".document.json").get("mode", "full_paper")
    result: dict[str, Any] = {
        "revision": revision,
        "at": now(),
        "status": "failed",
        "log": "",
        "diagnostics": [],
        "mode": mode,
    }
    if not compiler:
        result["log"] = "未找到 XeLaTeX，请安装 TeX Live 或 MiKTeX 并加入 PATH。"
        return result
    command = [
        compiler,
        "-no-shell-escape",
        "-interaction=nonstopmode",
        "-halt-on-error",
        "-file-line-error",
        "-synctex=1",
        "-jobname=preview",
        main,
    ]
    env = os.environ.copy()
    env.update(openout_any="p", openin_any="p", MIKTEX_ENABLE_INSTALLER="0")
    logs: list[str] = []
    try:
        for run in range(2):
            process = run_xelatex(
                command,
                pdf_path=build / "preview.pdf",
                cwd=build,
                env=env,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=min(settings.LATEX_COMPILE_TIMEOUT_SECONDS, 90),
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            logs.append(f"Pass {run + 1}\n{process.stdout}\n{process.stderr}")
            if process.returncode:
                break
        else:
            if (build / "preview.pdf").is_file():
                result["status"] = "completed"
    except subprocess.TimeoutExpired:
        logs.append("编译超时，请检查无限循环或缺失的 TeX 宏包。")
    except OSError as exc:
        logs.append(str(exc))
    result["log"] = "\n".join(logs)[-40000:]
    if result["status"] == "completed":
        from app.services.paper_layout import inspect_layout

        try:
            result["layout_review"] = inspect_layout(
                build / "preview.pdf", result["log"], mode=mode
            )
        except Exception as exc:
            message = f"版式检查未完成（{type(exc).__name__}），请重试或人工核验"
            result["layout_review"] = {
                "status": "unverified",
                "issues": [message],
                "blocking_issues": [message],
            }
    for match in re.finditer(
        r"(?:\./)?([^\r\n:]+\.tex):(\d+):\s*([^\r\n]+)", result["log"]
    ):
        result["diagnostics"].append(
            {"file": match[1], "line": int(match[2]), "message": match[3]}
        )
    return result
