"""HTTP 层使用的 Pydantic 模型（字段统一 camelCase 对外）。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _clean_use(value: str) -> str:
    use = value.strip()
    if not use:
        raise ValueError("用途不能为空字符串")
    return use


class RegisterVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_id: str = Field(..., alias="versionId", min_length=1)
    parent_ids: list[str] = Field(default_factory=list, alias="parentIds")

    @field_validator("version_id")
    @classmethod
    def _version_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("versionId 不能为空白")
        return value

    @field_validator("parent_ids")
    @classmethod
    def _clean_parents(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for raw in values:
            pid = raw.strip()
            if not pid:
                raise ValueError("parentIds 中不能包含空值")
            if pid not in cleaned:
                cleaned.append(pid)
        return cleaned


class LicenseChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    change_id: str = Field(..., alias="changeId", min_length=1)
    version_id: str = Field(..., alias="versionId", min_length=1)
    effective_at: datetime = Field(..., alias="effectiveAt")
    allowed_uses: list[str] = Field(..., alias="allowedUses")

    @field_validator("change_id", "version_id")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("字段不能为空白")
        return value

    @field_validator("allowed_uses")
    @classmethod
    def _clean_uses(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for raw in values:
            use = _clean_use(raw)
            if use not in cleaned:
                cleaned.append(use)
        return cleaned


class VersionView(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    version_id: str = Field(..., alias="versionId")
    parent_ids: list[str] = Field(default_factory=list, alias="parentIds")
    ancestor_ids: list[str] = Field(default_factory=list, alias="ancestorIds")
    created_at: str = Field(..., alias="createdAt")
    replayed: bool = False


class LicenseChangeView(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    change_id: str = Field(..., alias="changeId")
    version_id: str = Field(..., alias="versionId")
    effective_at: str = Field(..., alias="effectiveAt")
    allowed_uses: list[str] = Field(..., alias="allowedUses")
    content_hash: str = Field(..., alias="contentHash")
    replayed: bool = False


class BasisChange(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    version_id: str = Field(..., alias="versionId")
    change_id: str = Field(..., alias="changeId")
    effective_at: str = Field(..., alias="effectiveAt")
    allowed_uses: list[str] = Field(..., alias="allowedUses")


class BlockingVersion(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    version_id: str = Field(..., alias="versionId")
    reason: str
    change_id: str | None = Field(default=None, alias="changeId")
    effective_at: str | None = Field(default=None, alias="effectiveAt")
    allowed_uses: list[str] | None = Field(default=None, alias="allowedUses")


class CheckResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    version_id: str = Field(..., alias="versionId")
    use: str
    at: str
    allowed: bool
    basis_changes: list[BasisChange] = Field(default_factory=list, alias="basisChanges")
    blocking_versions: list[BlockingVersion] = Field(
        default_factory=list, alias="blockingVersions"
    )
    considered_versions: list[str] = Field(default_factory=list, alias="consideredVersions")
    intersection_allowed_uses: list[str] = Field(
        default_factory=list, alias="intersectionAllowedUses"
    )
