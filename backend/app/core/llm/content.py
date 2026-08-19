"""多模态消息内容的规范格式与解析工具。

Agent 侧统一使用 OpenAI Chat Completions 的内容块格式描述图文消息：

```python
[
    {"type": "text", "text": "这是第 3 页的插图"},
    {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
]
```

各 Provider 在自己的消息转换里调用 :func:`iter_content_blocks`，把规范格式翻译成
原生协议，避免每个 Provider 各自猜测内容结构。
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from typing import Any, Iterator, Literal

# data URL 形如 data:image/png;base64,xxxx；只接受 base64 内联图，
# 远程 URL 由 Provider 自行决定是否透传
_DATA_URL_PATTERN = re.compile(r"^data:(?P<media_type>[\w.+-]+/[\w.+-]+);base64,")


@dataclass(frozen=True)
class TextBlock:
    """纯文本内容块。"""

    text: str
    type: Literal["text"] = "text"


@dataclass(frozen=True)
class ImageBlock:
    """图像内容块，统一以 base64 与 MIME 类型表示。"""

    media_type: str
    data: str
    type: Literal["image"] = "image"

    @property
    def data_url(self) -> str:
        """还原为 OpenAI 风格的 data URL。"""
        return f"data:{self.media_type};base64,{self.data}"


ContentBlock = TextBlock | ImageBlock


def build_image_block(image_bytes: bytes, media_type: str = "image/png") -> dict:
    """把原始图像字节封装成规范内容块。

    Args:
        image_bytes: 图像二进制内容。
        media_type: 图像 MIME 类型。

    Returns:
        OpenAI 风格的 image_url 内容块。
    """
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{media_type};base64,{encoded}"},
    }


def build_text_block(text: str) -> dict:
    """把文本封装成规范内容块。"""
    return {"type": "text", "text": text}


def has_image_content(messages: list[dict]) -> bool:
    """判断消息列表中是否含图像块，供调用方决定是否需要视觉模型。"""
    return any(
        isinstance(block, ImageBlock)
        for message in messages
        for block in iter_content_blocks(message.get("content"))
    )


def iter_content_blocks(content: Any) -> Iterator[ContentBlock]:
    """把任意消息内容规范化为内容块序列。

    纯字符串内容会产出单个 :class:`TextBlock`，因此 Provider 可以对所有消息使用
    同一条转换路径，不必区分是否多模态。无法识别的块被跳过，宁可丢一张图也不能
    让整轮对话因为格式问题直接失败。

    Args:
        content: 消息的 content 字段，可能是字符串、内容块列表或 None。

    Yields:
        规范化后的文本块或图像块。
    """
    if content is None:
        return
    if isinstance(content, str):
        if content:
            yield TextBlock(text=content)
        return
    if not isinstance(content, list):
        yield TextBlock(text=str(content))
        return

    for item in content:
        if isinstance(item, str):
            if item:
                yield TextBlock(text=item)
            continue
        if not isinstance(item, dict):
            continue

        item_type = str(item.get("type", ""))
        if item_type in {"text", "input_text", "output_text"}:
            text = str(item.get("text", ""))
            if text:
                yield TextBlock(text=text)
            continue

        if item_type in {"image_url", "input_image", "image"}:
            image = _parse_image_item(item)
            if image is not None:
                yield image
            continue

        # 未声明 type 但带 text 的块按文本处理，兼容手写历史
        text = item.get("text")
        if isinstance(text, str) and text:
            yield TextBlock(text=text)


def _parse_image_item(item: dict) -> ImageBlock | None:
    """从各种写法的图像块中取出 base64 数据。"""
    source = item.get("source")
    if isinstance(source, dict) and source.get("type") == "base64":
        data = str(source.get("data", ""))
        if data:
            return ImageBlock(
                media_type=str(source.get("media_type") or "image/png"),
                data=data,
            )

    raw_url = item.get("image_url")
    if isinstance(raw_url, dict):
        raw_url = raw_url.get("url")
    if not isinstance(raw_url, str):
        raw_url = item.get("url") if isinstance(item.get("url"), str) else ""

    match = _DATA_URL_PATTERN.match(str(raw_url))
    if not match:
        return None
    return ImageBlock(
        media_type=match.group("media_type"),
        data=str(raw_url)[match.end() :],
    )
