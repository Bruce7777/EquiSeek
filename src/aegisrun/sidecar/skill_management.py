from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import yaml

from aegisrun.skills import SkillCatalog, SkillValidationError, SkillWorkspace

MAX_EDITABLE_SKILL_BYTES = 256 * 1024


class LocalSkillManager:
    """Safe CRUD for the single user-owned Skill root."""

    def __init__(self, root: Path) -> None:
        if root.is_symlink():
            raise ValueError("用户 Skill 根目录不能是符号链接")
        self.root = root.resolve()

    def detail(self, name: str, workspace: SkillWorkspace) -> dict[str, Any]:
        summary = next((item for item in workspace.list() if item.name == name), None)
        if summary is None:
            raise ValueError(f"Skill 不存在：{name}")
        skill_file = summary.package_root / "SKILL.md"
        content = self._read_skill_file(skill_file)
        editable = summary.provider.startswith("user-") and summary.package_root.is_relative_to(
            self.root
        )
        return {
            **summary.to_dict(),
            "sourceLabel": "用户 Skill" if editable else "内置",
            "content": content,
            "editable": editable,
        }

    def save(self, name: str, content: str) -> None:
        declared_name = self._declared_name(content)
        if declared_name != name:
            raise ValueError("Skill 名称必须与 frontmatter 的 name 一致")
        self._validate_standalone(declared_name, content)
        target_root = self._package_root(name)
        if target_root.exists() and (target_root.is_symlink() or not target_root.is_dir()):
            raise ValueError("Skill 目录不是安全的普通目录")
        target_root.mkdir(parents=True, exist_ok=True)
        target = target_root / "SKILL.md"
        temporary = target.with_suffix(".md.tmp")
        temporary.write_text(content.rstrip() + "\n", encoding="utf-8")
        temporary.chmod(0o600)
        os.replace(temporary, target)

    def import_file(self, source: Path) -> str:
        if not source.is_absolute() or source.is_symlink() or not source.is_file():
            raise ValueError("导入文件必须是用户选择的普通文件")
        if source.suffix.casefold() != ".md":
            raise ValueError("当前只支持导入标准 SKILL.md 文件")
        content = self._read_skill_file(source)
        name = self._declared_name(content)
        self.save(name, content)
        return name

    def delete(self, name: str, workspace: SkillWorkspace) -> None:
        detail = self.detail(name, workspace)
        if not detail["editable"]:
            raise ValueError("内置 Skill 为只读，不能删除")
        package_root = self._package_root(name)
        if package_root.is_symlink() or not package_root.is_dir():
            raise ValueError("Skill 目录不是安全的普通目录")
        shutil.rmtree(package_root)

    def ensure_root(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o700)
        return self.root

    def _package_root(self, name: str) -> Path:
        if not name or any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in name
        ):
            raise ValueError("Skill 名称必须使用小写连字符格式")
        target = (self.root / name).resolve()
        if not target.is_relative_to(self.root):
            raise ValueError("Skill 路径越界")
        return target

    @staticmethod
    def _declared_name(content: str) -> str:
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_EDITABLE_SKILL_BYTES:
            raise ValueError("SKILL.md 超过 256 KiB")
        if not content.startswith("---\n"):
            raise ValueError("SKILL.md 必须以 YAML frontmatter 开始")
        end = content.find("\n---\n", 4)
        if end < 0:
            raise ValueError("SKILL.md frontmatter 未闭合")
        try:
            metadata = yaml.safe_load(content[4:end])
        except yaml.YAMLError as error:
            raise ValueError(f"SKILL.md YAML 无效：{error}") from error
        name = metadata.get("name") if isinstance(metadata, dict) else None
        if not isinstance(name, str) or not name.strip():
            raise ValueError("SKILL.md 缺少 name")
        return name.strip()

    @staticmethod
    def _validate_standalone(name: str, content: str) -> None:
        try:
            with tempfile.TemporaryDirectory(prefix="aegisrun-skill-") as temporary:
                package = Path(temporary) / name
                package.mkdir()
                (package / "SKILL.md").write_text(content, encoding="utf-8")
                catalog = SkillCatalog((Path(temporary),), provider_name="user-validation")
                if catalog.describe(name).name != name:
                    raise ValueError("Skill 校验返回名称不一致")
        except SkillValidationError as error:
            raise ValueError(f"Skill 校验失败：{error}") from error

    @staticmethod
    def _read_skill_file(path: Path) -> str:
        if path.is_symlink() or not path.is_file():
            raise ValueError("SKILL.md 不是安全的普通文件")
        data = path.read_bytes()
        if len(data) > MAX_EDITABLE_SKILL_BYTES:
            raise ValueError("SKILL.md 超过 256 KiB")
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("SKILL.md 必须使用 UTF-8") from error
