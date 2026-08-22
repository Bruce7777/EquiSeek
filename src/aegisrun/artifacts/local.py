from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from aegisrun.core.errors import NotFoundError
from aegisrun.core.security import safe_join
from aegisrun.persistence.models import ArtifactModel


@dataclass(frozen=True, slots=True)
class StoredArtifact:
    model: ArtifactModel
    path: Path


class LocalArtifactBackend:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    async def put(
        self,
        session: AsyncSession,
        *,
        run_id: str,
        artifact_type: str,
        content_type: str,
        filename: str,
        content: bytes,
        creator_tool: str | None = None,
        workspace_revision: int = 1,
        is_public: bool = False,
    ) -> ArtifactModel:
        artifact_id = str(uuid4())
        relative_path = f"{run_id}/{artifact_id}/{Path(filename).name}"
        target = safe_join(self.root, relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        model = ArtifactModel(
            id=artifact_id,
            run_id=run_id,
            artifact_type=artifact_type,
            content_type=content_type,
            relative_path=relative_path,
            checksum=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            workspace_revision=workspace_revision,
            creator_tool=creator_tool,
            is_public=is_public,
        )
        session.add(model)
        await session.flush()
        return model

    def path_for(self, artifact: ArtifactModel) -> Path:
        path = safe_join(self.root, artifact.relative_path, must_exist=True)
        if not path.is_file():
            raise NotFoundError(f"artifact content is missing: {artifact.id}")
        return path
