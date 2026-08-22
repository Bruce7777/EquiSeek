from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

from aegisrun.agents.investment_conversation import StoredAttachment
from aegisrun.research.vision import OpenAICompatibleVisionClient

MAX_ATTACHMENT_COUNT = 4
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENT_TOTAL_BYTES = 20 * 1024 * 1024
MAX_EXTRACTED_CHARS = 24_000
TEXT_EXTENSIONS = frozenset({".txt", ".md", ".csv", ".json"})
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp"})
ALLOWED_EXTENSIONS = TEXT_EXTENSIONS | IMAGE_EXTENSIONS | {".pdf"}


@dataclass(frozen=True, slots=True)
class ProcessedAttachments:
    metadata: tuple[StoredAttachment, ...]
    context: str
    vision_used: bool
    warnings: tuple[str, ...]


async def process_attachments(
    raw_items: object,
    *,
    vision: OpenAICompatibleVisionClient | None,
) -> ProcessedAttachments:
    validated = validate_attachment_inputs(raw_items)
    metadata: list[StoredAttachment] = []
    sections: list[str] = []
    warnings: list[str] = []
    vision_used = False
    for path, attachment in validated:
        extension = path.suffix.casefold()
        mime_type = attachment.mime_type
        metadata.append(attachment)
        if extension in TEXT_EXTENSIONS:
            text = path.read_text(encoding="utf-8")[:MAX_EXTRACTED_CHARS]
            sections.append(f"### 附件 {path.name}\n\n{text}")
        elif extension == ".pdf":
            reader = PdfReader(path)
            text = "\n".join((page.extract_text() or "") for page in reader.pages)
            sections.append(f"### PDF 附件 {path.name}\n\n{text[:MAX_EXTRACTED_CHARS]}")
        elif vision is None:
            warnings.append(f"图片 {path.name} 已附加，但未启用视觉模型，当前未读取图片内容")
            sections.append(f"### 图片附件 {path.name}\n\n[尚未执行视觉识别]")
        else:
            description = await vision.describe(
                path.read_bytes(),
                mime_type,
                "请提取图片中的文字、表格、图表和与投资研究有关的可核验信息；"
                "区分图片直接可见内容与推断，不给出交易指令。",
            )
            vision_used = True
            sections.append(
                f"### 图片附件 {path.name} · 视觉模型描述\n\n{description}\n\n"
                "> 该段由视觉模型生成，重要数字仍需回看原图复核。"
            )
    return ProcessedAttachments(
        tuple(metadata),
        "\n\n".join(sections)[: MAX_EXTRACTED_CHARS * 2],
        vision_used,
        tuple(warnings),
    )


def validate_attachment_inputs(
    raw_items: object,
) -> tuple[tuple[Path, StoredAttachment], ...]:
    if raw_items is None:
        return ()
    if not isinstance(raw_items, list) or len(raw_items) > MAX_ATTACHMENT_COUNT:
        raise ValueError("单次最多上传 4 个附件")
    result: list[tuple[Path, StoredAttachment]] = []
    total = 0
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise ValueError("附件参数必须是对象")
        path = Path(str(raw.get("path", "")))
        if not path.is_absolute() or path.is_symlink() or not path.is_file():
            raise ValueError("附件必须是用户选择的普通本地文件")
        extension = path.suffix.casefold()
        if extension not in ALLOWED_EXTENSIONS:
            raise ValueError(f"不支持的附件类型：{extension or '无扩展名'}")
        size = path.stat().st_size
        if size > MAX_ATTACHMENT_BYTES:
            raise ValueError(f"附件 {path.name} 超过 10 MiB")
        total += size
        if total > MAX_ATTACHMENT_TOTAL_BYTES:
            raise ValueError("附件总大小超过 20 MiB")
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        result.append((path, StoredAttachment(path.name[:200], mime_type, size)))
    return tuple(result)
