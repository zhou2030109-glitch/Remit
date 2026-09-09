"""在启动昂贵计算前拦截明显失控的生成代码。"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

from app.config.setting import settings

_MAX_REFERENCED_SOURCE_BYTES = 1_000_000
_MATLAB_RUN_RE = re.compile(r"\brun\s*\(\s*['\"]([^'\"]+\.m)['\"]\s*\)", re.IGNORECASE)


@dataclass(frozen=True)
class ComplexityAssessment:
    """生成代码是否适合直接在交互任务中执行。"""

    allowed: bool
    reason: str = ""


def _referenced_matlab_sources(
    code: str, work_dir: str | Path
) -> list[tuple[str, str]]:
    """读取直接 ``run('file.m')`` 引用，且禁止逃出任务目录。"""
    root = Path(work_dir).resolve()
    sources: list[tuple[str, str]] = []
    for relative in _MATLAB_RUN_RE.findall(code):
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if (
            not candidate.is_file()
            or candidate.stat().st_size > _MAX_REFERENCED_SOURCE_BYTES
        ):
            continue
        sources.append((candidate.name, candidate.read_text(encoding="utf-8-sig")))
    return sources


def _python_unbounded_loop(code: str) -> bool:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False

    for node in ast.walk(tree):
        if not isinstance(node, ast.While):
            continue
        always_true = isinstance(node.test, ast.Constant) and node.test.value is True
        if not always_true:
            continue
        if not any(isinstance(child, ast.Break) for child in ast.walk(node)):
            return True
    return False


def _python_literal_loop_product(code: str) -> int:
    """估算直接嵌套 ``range(常量)`` 的最大迭代乘积。"""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return 0

    def range_size(node: ast.For) -> int:
        call = node.iter
        if not (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "range"
            and call.args
            and all(
                isinstance(arg, ast.Constant) and isinstance(arg.value, int)
                for arg in call.args
            )
        ):
            return 1
        values = [int(arg.value) for arg in call.args]  # type: ignore[union-attr]
        try:
            return max(0, len(range(*values)))
        except (TypeError, ValueError):
            return 1

    def visit_statements(statements: list[ast.stmt], parent_product: int) -> int:
        maximum = parent_product
        for statement in statements:
            if isinstance(statement, ast.For):
                product = parent_product * range_size(statement)
                maximum = max(
                    maximum, product, visit_statements(statement.body, product)
                )
            else:
                children = [
                    child
                    for child in ast.iter_child_nodes(statement)
                    if isinstance(child, ast.stmt)
                ]
                maximum = max(maximum, visit_statements(children, parent_product))
        return maximum

    return visit_statements(list(tree.body), 1)


def _matlab_risk(source: str) -> str:
    compact = re.sub(r"%[^\r\n]*", " ", source)
    compact = re.sub(r"\s+", " ", compact)
    lowered = compact.casefold()

    if re.search(r"\bwhile\s+(?:true|1)\b", lowered) and "break" not in lowered:
        return "发现没有退出条件的 while 循环"
    if re.search(r"\bperms\s*\(", lowered):
        return "发现阶乘级全排列 perms，必须先限制输入规模或改用启发式方法"

    quadratic_pair_scan = re.search(
        r"\bfor\s+(\w+)\s*=\s*1\s*:\s*(\w+)\s*-\s*1\b"
        r".{0,500}?\bfor\s+\w+\s*=\s*\1\s*\+\s*1\s*:\s*\2\b",
        lowered,
    )
    repeated_search = re.search(
        r"\b(seeds?|nboot|bootstrap|parfor|levels?|moves_per_temp|cvpartition|crossval)\b",
        lowered,
    )
    metaheuristic = re.search(
        r"\b(btsa|anneal|temperature|moves_per_temp|particleswarm|surrogateopt|bayesopt)\b|\bga\s*\(",
        lowered,
    )
    if quadratic_pair_scan and repeated_search and metaheuristic:
        return (
            "检测到元启发式搜索在重复实验中调用 O(n²) 成对校验，"
            "总体很可能达到 O(n³) 或更高；请先单数据集、单种子、小迭代计时，"
            "再分批保存 checkpoint"
        )

    bootstrap_counts = [
        int(value) for value in re.findall(r"\b(?:nboot|b)\s*=\s*(\d+)\b", lowered)
    ]
    if (
        bootstrap_counts
        and max(bootstrap_counts) >= 200
        and re.search(r"\b(parfor|crossval|cvpartition|grid|lambda|fold)\b", lowered)
    ):
        return (
            f"检测到 {max(bootstrap_counts)} 次 Bootstrap 与内部验证/搜索嵌套；"
            "请先用不超过 20 次复现计时，再拆成可续跑批次"
        )
    return ""


def assess_code_execution(
    code: str,
    *,
    language: str,
    work_dir: str | Path,
) -> ComplexityAssessment:
    """返回静态风险判断；只拦截高置信度的失控模式。"""
    if not settings.CODE_COMPLEXITY_GUARD_ENABLED:
        return ComplexityAssessment(allowed=True)

    sources = [("本次代码", code)]
    if language.casefold() == "matlab":
        sources.extend(_referenced_matlab_sources(code, work_dir))

    for name, source in sources:
        if language.casefold() == "python":
            if _python_unbounded_loop(source):
                return ComplexityAssessment(
                    allowed=False,
                    reason=f"复杂度保护器拒绝 {name}：发现没有 break 的 while True 循环",
                )
            product = _python_literal_loop_product(source)
            if product > settings.CODE_LITERAL_LOOP_ITERATION_LIMIT:
                return ComplexityAssessment(
                    allowed=False,
                    reason=(
                        f"复杂度保护器拒绝 {name}：显式嵌套循环至少 {product:,} 次，"
                        "请向量化或拆成有 checkpoint 的小批次"
                    ),
                )
        else:
            risk = _matlab_risk(source)
            if risk:
                return ComplexityAssessment(
                    allowed=False,
                    reason=f"复杂度保护器拒绝 {name}：{risk}",
                )

    return ComplexityAssessment(allowed=True)
