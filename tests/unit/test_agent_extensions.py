from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from aegisrun.research.vision import OpenAICompatibleVisionClient, VisionConfig
from aegisrun.sidecar.attachments import process_attachments, validate_attachment_inputs
from aegisrun.sidecar.skill_management import LocalSkillManager
from aegisrun.skills import SkillWorkspace, SkillWorkspacePolicy


def skill_content(name: str = "my-investment-rule") -> str:
    return f"""---
name: {name}
description: 用户自己的可审计投资规则。
version: 1.0.0
allowed-agents: [investment-lead-agent]
allowed-tools: []
network-required: false
resources: []
---

# 用户规则

先核对数据截止日，再讨论失效条件。
"""


def test_user_skill_can_be_imported_viewed_edited_and_deleted(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    source = tmp_path / "SKILL.md"
    source.write_text(skill_content(), encoding="utf-8")
    manager = LocalSkillManager(root)

    name = manager.import_file(source)
    workspace = SkillWorkspace(SkillWorkspacePolicy(include_builtin=False, user_roots=(root,)))
    detail = manager.detail(name, workspace)
    assert detail["editable"] is True
    assert "数据截止日" in detail["content"]

    manager.save(name, skill_content().replace("失效条件", "风险边界"))
    refreshed = SkillWorkspace(SkillWorkspacePolicy(include_builtin=False, user_roots=(root,)))
    assert "风险边界" in manager.detail(name, refreshed)["content"]
    manager.delete(name, refreshed)
    assert not (root / name).exists()


@pytest.mark.asyncio
async def test_text_attachment_is_extracted_and_image_uses_vl2_compatible_request(
    tmp_path: Path,
) -> None:
    note = tmp_path / "note.md"
    note.write_text("组合最大回撤上限为 12%。", encoding="utf-8")
    image = tmp_path / "chart.png"
    image.write_bytes(b"not-a-real-png-but-valid-request-bytes")

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["model"] == "deepseek-ai/deepseek-vl2"
        assert payload["messages"][0]["content"][0]["image_url"]["url"].startswith(
            "data:image/png;base64,"
        )
        return httpx.Response(
            200,
            request=request,
            json={"choices": [{"message": {"content": "图表显示一条价格曲线。"}}]},
        )

    vision = OpenAICompatibleVisionClient(
        VisionConfig(api_key="test-key"), transport=httpx.MockTransport(handler)
    )
    try:
        result = await process_attachments(
            [{"path": str(note)}, {"path": str(image)}], vision=vision
        )
    finally:
        await vision.close()

    assert "最大回撤上限" in result.context
    assert "图表显示一条价格曲线" in result.context
    assert result.vision_used is True
    assert len(result.metadata) == 2


def test_attachment_limits_reject_more_than_four_files(tmp_path: Path) -> None:
    paths = []
    for index in range(5):
        path = tmp_path / f"{index}.txt"
        path.write_text("x", encoding="utf-8")
        paths.append({"path": str(path)})
    with pytest.raises(ValueError, match="最多上传 4"):
        validate_attachment_inputs(paths)
