"""FastAPI 应用：数据集派生许可判定服务。

服务启动时自动建表；如配置 SEED_DEMO_DATA=true（默认），
同时写入幂等的演示数据（见 app.seed）。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastapi import FastAPI, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import config
from .database import init_db, session
from .models import (
    CheckResponse,
    LicenseChangeRequest,
    LicenseChangeView,
    RegisterVersionRequest,
    VersionView,
)
from .services import (
    ServiceError,
    add_license_change,
    check_permission,
    get_version_detail,
    list_license_changes,
    list_versions,
    register_version,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动即完成表结构初始化与（可选）演示数据写入；entrypoint 已做过一次，
    # 直接以 uvicorn 启动（绕过 entrypoint）时这里兜底，均为幂等操作。
    init_db()
    if config.SEED_DEMO_DATA:
        from .seed import seed

        seed()
    yield


app = FastAPI(
    title="数据集派生许可判定服务",
    version="1.0.0",
    description=(
        "登记数据集版本（固定引用不可修改的父版本）与按 UTC 时刻生效的许可变更，"
        "并沿自身及全部祖先的用途交集判定可否派生使用。"
    ),
    lifespan=lifespan,
)


def _error_body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details is not None:
        body["error"]["details"] = details
    return body


@app.exception_handler(ServiceError)
async def _service_error_handler(request: Request, exc: ServiceError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body(exc.code, exc.message, exc.details),
    )


@app.exception_handler(RequestValidationError)
async def _validation_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=_error_body(
            "INVALID_REQUEST", "请求参数校验失败", exc.errors(include_url=False)
        ),
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/versions", response_model=VersionView, status_code=status.HTTP_201_CREATED)
def create_version(payload: RegisterVersionRequest, response: Response) -> dict[str, Any]:
    """登记数据集版本，固定引用若干已存在的父版本（创建后不可修改）。

    完全相同的重复提交幂等重放（HTTP 200，replayed=true）；
    同 ID 但父引用不同返回 409；引用不存在的父版本或自引用返回 422。
    """
    with session() as conn:
        result = register_version(
            conn, version_id=payload.version_id, parent_ids=payload.parent_ids
        )
    # 200 用于幂等重放，201 用于新建。
    response.status_code = (
        status.HTTP_200_OK if result["replayed"] else status.HTTP_201_CREATED
    )
    return result


@app.get("/versions", response_model=list[VersionView])
def get_versions() -> list[dict[str, Any]]:
    with session() as conn:
        return list_versions(conn)


@app.get("/versions/{version_id}", response_model=VersionView)
def get_version(version_id: str) -> dict[str, Any]:
    with session() as conn:
        result = get_version_detail(conn, version_id)
    if result is None:
        raise ServiceError(
            f"版本 {version_id} 不存在",
            status_code=404,
            code="NOT_FOUND",
            details={"versionId": version_id},
        )
    return result


@app.post(
    "/license-changes",
    response_model=LicenseChangeView,
    status_code=status.HTTP_201_CREATED,
)
def create_license_change(
    payload: LicenseChangeRequest, response: Response
) -> dict[str, Any]:
    """登记许可变更。

    - 同 change_id 且内容一致：幂等返回同一结果（HTTP 200，replayed=true）；
    - 同 change_id 但内容不同：409；
    - 同版本同生效时刻的不同变更：409。
    """
    with session() as conn:
        result = add_license_change(
            conn,
            change_id=payload.change_id,
            version_id=payload.version_id,
            effective_at=payload.effective_at,
            allowed_uses=payload.allowed_uses,
        )
    response.status_code = (
        status.HTTP_200_OK if result["replayed"] else status.HTTP_201_CREATED
    )
    return result


@app.get("/license-changes", response_model=list[LicenseChangeView])
def get_license_changes(
    version_id: str | None = Query(default=None, alias="versionId"),
) -> list[dict[str, Any]]:
    with session() as conn:
        return list_license_changes(conn, version_id=version_id)


@app.get("/check", response_model=CheckResponse)
def check(
    version_id: str = Query(..., alias="versionId"),
    use: str = Query(..., description="待判定的用途标识，如 research"),
    at: datetime = Query(
        ..., alias="at", description="判定所用的 UTC 时刻（ISO-8601，须带时区）"
    ),
) -> dict[str, Any]:
    """判定某版本在给定 UTC 时刻是否可用于指定用途。

    对自身及全部祖先，各取不晚于 at 的最近一次许可变更，取用途交集；
    任一版本无有效许可或不允许该用途即拒绝。
    """
    with session() as conn:
        return check_permission(conn, version_id=version_id, use=use, at=at)
