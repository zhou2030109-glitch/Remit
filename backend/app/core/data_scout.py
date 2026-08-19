"""确定性数据侦察：在建模选型前廉价摸清数据底细，零 LLM 调用。"""

from pathlib import Path
import re
from typing import Any

from app.utils.log_util import logger
from app.utils.file_types import DATA_FILE_SUFFIXES

_DATA_SUFFIXES = DATA_FILE_SUFFIXES | {".tsv"}
_MAX_FILES = 24
_MAX_FILE_BYTES = 80 * 1024 * 1024
# xlsx 解压后可放大 10 倍以上，磁盘字节数上限须单独收紧
_MAX_EXCEL_BYTES = 15 * 1024 * 1024
_MAX_COLUMNS = 30
_SAMPLE_ROWS = 3
_SAMPLE_CELL_CHARS = 80

_BOOKSHELF_METADATA_PATTERN = re.compile(
    r"^(NumHardBlocks|NumSoftRectangularBlocks|NumTerminals|NumNets|NumPins)\s*:\s*(\d+)\s*$",
    re.IGNORECASE,
)
_BOOKSHELF_COORDINATE_PATTERN = re.compile(
    r"\(\s*(-?(?:\d+(?:\.\d*)?|\.\d+))\s*,\s*"
    r"(-?(?:\d+(?:\.\d*)?|\.\d+))\s*\)"
)


def _read_text_attachment(path: Path) -> str:
    """Read a modeling text attachment with common Chinese encodings."""
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        return path.read_text(encoding="gb18030")


def _bookshelf_lines(path: Path) -> list[str]:
    """Return meaningful Bookshelf lines without comments or file banners."""
    lines: list[str] = []
    for raw_line in _read_text_attachment(path).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.upper().startswith("UCLA "):
            continue
        lines.append(line)
    return lines


def _profile_bookshelf_blocks(path: Path) -> dict[str, Any]:
    """Recognize Bookshelf ``.blocks`` geometry and terminal definitions."""
    metadata: dict[str, int] = {}
    records: list[dict[str, Any]] = []
    hard_blocks = 0
    soft_blocks = 0
    terminals = 0
    malformed_rows = 0

    for line in _bookshelf_lines(path):
        metadata_match = _BOOKSHELF_METADATA_PATTERN.fullmatch(line)
        if metadata_match:
            metadata[metadata_match.group(1)] = int(metadata_match.group(2))
            continue

        parts = line.split()
        if len(parts) >= 2 and parts[1].lower().startswith("terminal"):
            terminals += 1
            records.append(
                {"name": parts[0], "kind": "terminal", "width": None, "height": None, "area": None}
            )
            continue

        if len(parts) >= 4 and parts[1].lower() in {"block", "hardrectilinear"}:
            coordinates = [
                (float(x), float(y))
                for x, y in _BOOKSHELF_COORDINATE_PATTERN.findall(line)
            ]
            if not coordinates:
                malformed_rows += 1
                continue
            hard_blocks += 1
            xs = [coordinate[0] for coordinate in coordinates]
            ys = [coordinate[1] for coordinate in coordinates]
            width = max(xs) - min(xs)
            height = max(ys) - min(ys)
            records.append(
                {
                    "name": parts[0],
                    "kind": "hard_block",
                    "width": width,
                    "height": height,
                    "area": width * height,
                }
            )
            continue

        if len(parts) >= 5 and parts[1].lower() == "softrectangular":
            try:
                area = float(parts[2])
            except ValueError:
                malformed_rows += 1
                continue
            soft_blocks += 1
            records.append(
                {
                    "name": parts[0],
                    "kind": "soft_block",
                    "width": None,
                    "height": None,
                    "area": area,
                }
            )
            continue

        malformed_rows += 1

    if not records:
        raise ValueError("no recognizable Bookshelf block or terminal records")

    return {
        "file": path.name,
        "format": "bookshelf_blocks",
        "rows": len(records),
        "columns_count": 5,
        "columns": [
            {"name": "name", "dtype": "text", "missing_rate": 0.0},
            {"name": "kind", "dtype": "text", "missing_rate": 0.0},
            {"name": "width", "dtype": "number", "missing_rate": 0.0},
            {"name": "height", "dtype": "number", "missing_rate": 0.0},
            {"name": "area", "dtype": "number", "missing_rate": 0.0},
        ],
        "time_range": None,
        "high_missing_columns": [],
        "sample_rows": records[:_SAMPLE_ROWS],
        "metadata": metadata,
        "statistics": {
            "parsed_hard_blocks": hard_blocks,
            "parsed_soft_blocks": soft_blocks,
            "parsed_terminals": terminals,
            "malformed_rows": malformed_rows,
        },
    }


def _profile_bookshelf_nets(path: Path) -> dict[str, Any]:
    """Recognize Bookshelf ``.nets`` connectivity groups and their degrees."""
    lines = _bookshelf_lines(path)
    metadata: dict[str, int] = {}
    records: list[dict[str, Any]] = []
    malformed_nets = 0
    parsed_pins = 0
    index = 0

    while index < len(lines):
        line = lines[index]
        metadata_match = _BOOKSHELF_METADATA_PATTERN.fullmatch(line)
        if metadata_match:
            metadata[metadata_match.group(1)] = int(metadata_match.group(2))
            index += 1
            continue

        degree_match = re.fullmatch(r"NetDegree\s*:\s*(\d+)(?:\s+\S+)?", line, re.IGNORECASE)
        if not degree_match:
            index += 1
            continue
        declared_degree = int(degree_match.group(1))
        nodes = lines[index + 1 : index + 1 + declared_degree]
        if len(nodes) != declared_degree or any(
            _BOOKSHELF_METADATA_PATTERN.fullmatch(node)
            or re.match(r"NetDegree\s*:", node, re.IGNORECASE)
            for node in nodes
        ):
            malformed_nets += 1
        parsed_pins += len(nodes)
        records.append(
            {
                "net": len(records) + 1,
                "degree": declared_degree,
                "nodes": ", ".join(nodes)[:_SAMPLE_CELL_CHARS],
            }
        )
        index += 1 + len(nodes)

    if not records:
        raise ValueError("no recognizable Bookshelf NetDegree groups")

    return {
        "file": path.name,
        "format": "bookshelf_nets",
        "rows": len(records),
        "columns_count": 3,
        "columns": [
            {"name": "net", "dtype": "integer", "missing_rate": 0.0},
            {"name": "degree", "dtype": "integer", "missing_rate": 0.0},
            {"name": "nodes", "dtype": "text", "missing_rate": 0.0},
        ],
        "time_range": None,
        "high_missing_columns": [],
        "sample_rows": records[:_SAMPLE_ROWS],
        "metadata": metadata,
        "statistics": {
            "parsed_nets": len(records),
            "parsed_pins": parsed_pins,
            "malformed_nets": malformed_nets,
        },
    }


def _profile_bookshelf_placement(path: Path) -> dict[str, Any]:
    """Recognize Bookshelf ``.pl`` node coordinates and placement bounds."""
    records: list[dict[str, Any]] = []
    malformed_rows = 0
    for line in _bookshelf_lines(path):
        parts = line.replace(":", " : ").split()
        if len(parts) < 3:
            malformed_rows += 1
            continue
        try:
            x = float(parts[1])
            y = float(parts[2])
        except ValueError:
            malformed_rows += 1
            continue
        orientation = None
        if ":" in parts:
            orientation_index = parts.index(":") + 1
            if orientation_index < len(parts):
                orientation = parts[orientation_index]
        records.append(
            {
                "name": parts[0],
                "x": x,
                "y": y,
                "orientation": orientation,
                "fixed": any(token.upper().startswith("/FIXED") for token in parts),
            }
        )

    if not records:
        raise ValueError("no recognizable Bookshelf placement rows")
    xs = [record["x"] for record in records]
    ys = [record["y"] for record in records]

    return {
        "file": path.name,
        "format": "bookshelf_placement",
        "rows": len(records),
        "columns_count": 5,
        "columns": [
            {"name": "name", "dtype": "text", "missing_rate": 0.0},
            {"name": "x", "dtype": "number", "missing_rate": 0.0},
            {"name": "y", "dtype": "number", "missing_rate": 0.0},
            {"name": "orientation", "dtype": "text", "missing_rate": 0.0},
            {"name": "fixed", "dtype": "boolean", "missing_rate": 0.0},
        ],
        "time_range": None,
        "high_missing_columns": [],
        "sample_rows": records[:_SAMPLE_ROWS],
        "coordinate_bounds": {
            "min_x": min(xs),
            "max_x": max(xs),
            "min_y": min(ys),
            "max_y": max(ys),
        },
        "statistics": {
            "parsed_placements": len(records),
            "fixed_placements": sum(bool(record["fixed"]) for record in records),
            "malformed_rows": malformed_rows,
        },
    }


def _profile_one_file(path: Path) -> dict[str, Any]:
    import pandas as pd

    if path.suffix.lower() == ".blocks":
        return _profile_bookshelf_blocks(path)
    if path.suffix.lower() == ".nets":
        return _profile_bookshelf_nets(path)
    if path.suffix.lower() == ".pl":
        return _profile_bookshelf_placement(path)
    if path.suffix.lower() == ".txt":
        return _profile_text_file(path)
    if path.suffix.lower() in {".csv", ".tsv"}:
        sep = "\t" if path.suffix.lower() == ".tsv" else ","
        frame = pd.read_csv(path, sep=sep, encoding="utf-8-sig", low_memory=False)
    else:
        frame = pd.read_excel(path)

    columns: list[dict[str, Any]] = []
    for name in list(frame.columns)[:_MAX_COLUMNS]:
        series = frame[name]
        missing_rate = float(series.isna().mean())
        entry: dict[str, Any] = {
            "name": str(name),
            "dtype": str(series.dtype),
            "missing_rate": round(missing_rate, 4),
        }
        if pd.api.types.is_numeric_dtype(series) and bool(series.notna().any()):
            entry["min"] = float(series.min())
            entry["max"] = float(series.max())
        columns.append(entry)

    time_range = None
    for name in list(frame.columns)[:_MAX_COLUMNS]:
        series = frame[name]
        if pd.api.types.is_datetime64_any_dtype(series):
            parsed = series
        elif series.dtype == object:
            try:
                parsed = pd.to_datetime(series, errors="coerce")
            except (TypeError, ValueError):
                continue
            if float(parsed.notna().mean()) < 0.8:
                continue
        else:
            continue
        if bool(parsed.notna().any()):
            time_range = {
                "column": str(name),
                "start": str(parsed.min()),
                "end": str(parsed.max()),
            }
            break

    high_missing = [item["name"] for item in columns if item["missing_rate"] >= 0.2]
    return {
        "file": path.name,
        "rows": int(frame.shape[0]),
        "columns_count": int(frame.shape[1]),
        "columns": columns,
        "time_range": time_range,
        "high_missing_columns": high_missing,
        "sample_rows": (
            frame.iloc[:_SAMPLE_ROWS, :_MAX_COLUMNS]
            .astype(str)
            .map(lambda value: value[:_SAMPLE_CELL_CHARS])
            .to_dict("records")
        ),
    }


def _split_structured_text_row(line: str) -> list[str]:
    """按最外层逗号切分，保留附件中括号内的坐标和接口列表。"""
    fields: list[str] = []
    current: list[str] = []
    depth = 0
    quote = ""
    for character in line.strip():
        if quote:
            current.append(character)
            if character == quote:
                quote = ""
            continue
        if character in {'"', "'"}:
            quote = character
            current.append(character)
        elif character == "(":
            depth += 1
            current.append(character)
        elif character == ")":
            depth = max(0, depth - 1)
            current.append(character)
        elif character == "," and depth == 0:
            fields.append("".join(current).strip())
            current = []
        else:
            current.append(character)
    fields.append("".join(current).strip())
    return fields


def _profile_text_file(path: Path) -> dict[str, Any]:
    """画像华数杯等赛题常见的多段、括号嵌套 TXT 表格。"""
    content = _read_text_attachment(path)

    blocks: list[list[str]] = []
    active: list[str] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if line:
            active.append(line)
        elif active:
            blocks.append(active)
            active = []
    if active:
        blocks.append(active)
    if not blocks:
        raise ValueError("文本附件为空")

    sections: list[dict[str, Any]] = []
    all_columns: list[str] = []
    sample_rows: list[dict[str, str]] = []
    total_rows = 0
    for index, block in enumerate(blocks, start=1):
        headers = _split_structured_text_row(block[0])[:_MAX_COLUMNS]
        if not headers:
            continue
        rows: list[dict[str, str]] = []
        malformed_rows = 0
        for line in block[1:]:
            values = _split_structured_text_row(line)
            if len(values) != len(headers):
                malformed_rows += 1
            normalized = (values + [""] * len(headers))[: len(headers)]
            row = {
                header: value[:_SAMPLE_CELL_CHARS]
                for header, value in zip(headers, normalized, strict=True)
            }
            if len(rows) < _SAMPLE_ROWS:
                rows.append(row)
            if len(sample_rows) < _SAMPLE_ROWS:
                sample_rows.append(row)
        row_count = max(0, len(block) - 1)
        total_rows += row_count
        for header in headers:
            if header not in all_columns:
                all_columns.append(header)
        sections.append(
            {
                "section": index,
                "columns": headers,
                "columns_count": len(headers),
                "rows": row_count,
                "malformed_rows": malformed_rows,
                "sample_rows": rows,
            }
        )
    if not sections:
        raise ValueError("文本附件没有可识别的表格段")

    return {
        "file": path.name,
        "format": "structured_text",
        "rows": total_rows,
        "columns_count": max(item["columns_count"] for item in sections),
        "columns": [
            {"name": name, "dtype": "text", "missing_rate": 0.0}
            for name in all_columns[:_MAX_COLUMNS]
        ],
        "time_range": None,
        "high_missing_columns": [],
        "sample_rows": sample_rows,
        "sections": sections,
    }


def build_data_profile(work_dir: str | Path) -> dict[str, Any]:
    """扫描工作目录数据文件，产出结构化画像；单文件失败只记录不中断。

    Args:
        work_dir: 任务工作目录。

    Returns:
        {"files": [...], "notes": [...]}；无数据文件时 files 为空。
    """
    root = Path(work_dir)
    profile: dict[str, Any] = {
        "status": "not_found",
        "discovered_files": [],
        "files": [],
        "notes": [],
    }
    if not root.is_dir():
        return profile

    candidates = sorted(
        (
            path
            for path in root.iterdir()
            if path.is_file() and path.suffix.lower() in _DATA_SUFFIXES
        ),
        key=lambda path: path.stat().st_size,
        reverse=True,
    )
    profile["discovered_files"] = [path.name for path in candidates]
    for path in candidates[:_MAX_FILES]:
        size_limit = (
            _MAX_EXCEL_BYTES
            if path.suffix.lower() in {".xlsx", ".xls"}
            else _MAX_FILE_BYTES
        )
        if path.stat().st_size > size_limit:
            profile["notes"].append(f"{path.name} 过大，跳过画像")
            continue
        try:
            profile["files"].append(_profile_one_file(path))
        except Exception as exc:
            logger.warning(f"数据画像失败 {path.name}: {exc}")
            profile["notes"].append(f"{path.name} 画像失败: {exc}")
    if len(candidates) > _MAX_FILES:
        profile["notes"].append(
            f"共 {len(candidates)} 个数据文件，仅画像最大的 {_MAX_FILES} 个"
        )
    if profile["files"]:
        profile["status"] = "completed"
    elif candidates:
        profile["status"] = "failed"
    return profile


def summarize_data_profile(profile: dict[str, Any]) -> str:
    """把数据画像压缩成给 LLM 的短摘要。"""
    files = profile.get("files") or []
    if not files:
        return ""
    lines: list[str] = []
    for item in files:
        file_format = item.get("format")
        statistics = item.get("statistics") or {}
        if file_format == "bookshelf_blocks":
            parts = [
                f"{item['file']}: Bookshelf 块定义，"
                f"{statistics.get('parsed_hard_blocks', 0)} 个硬块、"
                f"{statistics.get('parsed_soft_blocks', 0)} 个软块、"
                f"{statistics.get('parsed_terminals', 0)} 个端口"
            ]
        elif file_format == "bookshelf_nets":
            parts = [
                f"{item['file']}: Bookshelf 网络定义，"
                f"{statistics.get('parsed_nets', 0)} 个网络、"
                f"{statistics.get('parsed_pins', 0)} 个引脚"
            ]
        elif file_format == "bookshelf_placement":
            bounds = item.get("coordinate_bounds") or {}
            parts = [
                f"{item['file']}: Bookshelf 布局坐标，"
                f"{statistics.get('parsed_placements', 0)} 个节点，"
                f"X=[{bounds.get('min_x')}, {bounds.get('max_x')}]，"
                f"Y=[{bounds.get('min_y')}, {bounds.get('max_y')}]"
            ]
        else:
            parts = [
                f"{item['file']}: {item['rows']} 行 × {item['columns_count']} 列"
            ]
        if item.get("time_range"):
            time_range = item["time_range"]
            parts.append(
                f"时间列 {time_range['column']} 覆盖 "
                f"{time_range['start']} ~ {time_range['end']}"
            )
        if item.get("high_missing_columns"):
            parts.append(
                "高缺失列(≥20%)：" + "、".join(item["high_missing_columns"][:6])
            )
        lines.append("；".join(parts))
    for note in (profile.get("notes") or [])[:4]:
        lines.append(f"注意：{note}")
    return "\n".join(lines)
