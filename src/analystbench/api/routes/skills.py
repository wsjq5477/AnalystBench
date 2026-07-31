"""Skill registry and immutable version endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, ConfigDict, Field

from analystbench.errors import AnalystBenchError
from analystbench.skill_optimization.registry import SkillRegistryService

router = APIRouter(tags=["skills"])


class SkillCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    source_path: str = Field(min_length=1)
    invoke_as: str | None = Field(default=None, min_length=2, max_length=64)
    harness_key: str = Field(default="claude", min_length=1, max_length=100)
    install_relative_path: str | None = None
    description: str = ""
    editable_paths: list[str] = Field(default_factory=lambda: ["SKILL.md"])
    limits: dict[str, int] = Field(default_factory=dict)
    import_initial_version: bool = True


class SkillImport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_path: str | None = None
    parent_version_id: str | None = None
    source_type: str = Field(default="import", min_length=1, max_length=32)
    created_by: str | None = Field(default=None, max_length=128)


class SkillBind(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluation_target_id: str
    version_id: str
    active_level: str = "provisional"
    expected_lock_version: int | None = Field(default=None, ge=0)


class VariantCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluation_target_id: str
    version_id: str


class SkillResponse(BaseModel):
    id: str
    key: str
    name: str
    description: str
    source_path: str
    invoke_as: str
    harness_key: str
    install_relative_path: str
    publish_mode: str
    editable_paths: list[str]
    limits: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class SkillVersionResponse(BaseModel):
    id: str
    skill_id: str
    version: int
    parent_version_id: str | None
    package_hash: str
    git_commit: str
    git_tree: str
    git_object_format: str
    manifest: dict[str, Any]
    source_type: str
    status: str
    created_by: str | None
    created_at: datetime


def registry(request: Request) -> SkillRegistryService:
    return request.app.state.skill_registry_service


@router.post("/skills", status_code=status.HTTP_201_CREATED)
def create_skill(payload: SkillCreate, request: Request) -> dict[str, Any]:
    values = payload.model_dump(exclude={"key", "import_initial_version"})
    item = registry(request).create(skill_key=payload.key, **values)
    version = (
        registry(request).import_version(item.id, source_type="initial")
        if payload.import_initial_version
        else None
    )
    return {
        "skill": SkillRegistryService.skill_view(item),
        "initial_version": (
            SkillRegistryService.version_view(version) if version is not None else None
        ),
    }


@router.get("/skills", response_model=list[SkillResponse])
def list_skills(request: Request) -> list[dict[str, Any]]:
    return [SkillRegistryService.skill_view(item) for item in registry(request).list()]


@router.get("/skills/{skill_id}", response_model=SkillResponse)
def get_skill(skill_id: str, request: Request) -> dict[str, Any]:
    return SkillRegistryService.skill_view(registry(request).get(skill_id))


@router.post(
    "/skills/{skill_id}/versions",
    response_model=SkillVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
def import_skill_version(
    skill_id: str, payload: SkillImport, request: Request
) -> dict[str, Any]:
    item = registry(request).import_version(
        skill_id, **payload.model_dump(exclude_none=True)
    )
    return SkillRegistryService.version_view(item)


@router.get(
    "/skills/{skill_id}/versions", response_model=list[SkillVersionResponse]
)
def list_skill_versions(skill_id: str, request: Request) -> list[dict[str, Any]]:
    return [
        SkillRegistryService.version_view(item)
        for item in registry(request).list_versions(skill_id)
    ]


@router.get("/skills/{skill_id}/diff")
def diff_skill_versions(
    skill_id: str, from_version_id: str, to_version_id: str, request: Request
) -> dict[str, str]:
    current = registry(request).get(skill_id)
    old = registry(request).get_version(from_version_id)
    new = registry(request).get_version(to_version_id)
    if old.skill_id != current.id or new.skill_id != current.id:
        raise AnalystBenchError(
            "skill_version_mismatch", "版本不属于 URL 指定的 Skill。"
        )
    return {
        "from_version_id": from_version_id,
        "to_version_id": to_version_id,
        "diff": registry(request).diff_versions(from_version_id, to_version_id),
    }


@router.put("/skills/{skill_id}/bindings")
def bind_skill(
    skill_id: str, payload: SkillBind, request: Request
) -> dict[str, Any]:
    item = registry(request).bind(
        skill_id=skill_id, **payload.model_dump()
    )
    return {
        "id": item.id,
        "skill_id": item.skill_id,
        "evaluation_target_id": item.evaluation_target_id,
        "active_version_id": item.active_version_id,
        "active_level": item.active_level,
        "lock_version": item.lock_version,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


@router.post("/evaluation-variants", status_code=status.HTTP_201_CREATED)
def create_evaluation_variant(
    payload: VariantCreate, request: Request
) -> dict[str, Any]:
    item = registry(request).freeze_variant(**payload.model_dump())
    return {
        "id": item.id,
        "evaluation_target_id": item.evaluation_target_id,
        "skill_package_version_id": item.skill_package_version_id,
        "materialized_method_id": item.materialized_method_id,
        "install_relative_path": item.install_relative_path,
        "invoke_as": item.invoke_as,
        "content_hash": item.content_hash,
        "status": item.status,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }
