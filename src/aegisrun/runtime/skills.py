from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SkillDocument:
    name: str
    path: Path
    checksum: str
    content: str


class SkillLoader:
    def __init__(self, root: Path) -> None:
        self.root = root

    def discover(self) -> list[SkillDocument]:
        skills: list[SkillDocument] = []
        for path in sorted(self.root.glob("*/SKILL.md")):
            content = path.read_text(encoding="utf-8")
            skills.append(
                SkillDocument(
                    name=path.parent.name,
                    path=path,
                    checksum=hashlib.sha256(content.encode()).hexdigest(),
                    content=content,
                )
            )
        return skills
