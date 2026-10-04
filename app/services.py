"""核心业务逻辑。

规则摘要：
1. 登记版本时固定引用零个或多个已存在的父版本；父版本引用一经创建不可修改。
   - 同 ID、同父引用集合（顺序不敏感的比较使用规范化集合，存储保留提交顺序）重复提交
     视为幂等重放，返回 replayed=true；父引用不同则 409。
2. 许可变更记录某版本自某 UTC 时刻起生效的“完整允许用途集合”。
   - change_id 相同且内容（版本/生效时刻/用途集合）相同：幂等返回同一结果；
   - change_id 相同但内容不同：409；
   - 同一版本同一生效时刻出现不同变更：409（数据库唯一索引兜底）。
3. 判定时取被查版本自身及全部祖先（父引用闭包），各取“不晚于查询时刻的最近一次
   变更”为依据，任一版本无生效许可或不允许该用途即拒绝，并列出全部阻断版本。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from typing import Any

from .time_util import normalize_utc, now_utc


class ServiceError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        code: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.details = details or {}


class NotFoundError(ServiceError):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, status_code=404, code="NOT_FOUND", details=details)


class ConflictError(ServiceError):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, status_code=409, code="CONFLICT", details=details)


class ValidationError(ServiceError):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, status_code=422, code="INVALID_REQUEST", details=details)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _content_hash(version_id: str, effective_at: str, allowed_uses: list[str]) -> str:
    """对变更内容计算稳定哈希；用途集合按排序后参与计算，顺序不敏感。"""
    payload = json.dumps(
        {
            "versionId": version_id,
            "effectiveAt": effective_at,
            "allowedUses": sorted(allowed_uses),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalize_or_422(at: datetime) -> str:
    try:
        return normalize_utc(at)
    except ValueError as exc:
        raise ValidationError(
            str(exc), details={"field": "effectiveAt/at"}
        ) from exc


def _parents_of(conn: sqlite3.Connection, version_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT parent_id FROM version_parents WHERE version_id=? ORDER BY position",
        (version_id,),
    ).fetchall()
    return [r["parent_id"] for r in rows]


def get_closure(conn: sqlite3.Connection, version_id: str) -> list[str]:
    """返回自身及全部祖先的版本 ID 列表。

    按引用层次 BFS 展开并去重：自身在首位，随后是按提交顺序遍历到的
    父版本、祖父版本……多父引用产生的菱形依赖不会重复。
    """
    order = [version_id]
    seen = {version_id}
    frontier = [version_id]
    while frontier:
        next_frontier: list[str] = []
        for vid in frontier:
            for pid in _parents_of(conn, vid):
                if pid not in seen:
                    seen.add(pid)
                    order.append(pid)
                    next_frontier.append(pid)
        frontier = next_frontier
    return order


def _change_from_row(row: sqlite3.Row, *, replayed: bool) -> dict[str, Any]:
    return {
        "changeId": row["change_id"],
        "versionId": row["version_id"],
        "effectiveAt": row["effective_at"],
        "allowedUses": json.loads(row["allowed_uses_json"]),
        "contentHash": row["content_hash"],
        "replayed": replayed,
    }


def _version_detail(
    conn: sqlite3.Connection, version_id: str, *, replayed: bool
) -> dict[str, Any]:
    row = conn.execute(
        "SELECT version_id, created_at FROM versions WHERE version_id=?", (version_id,)
    ).fetchone()
    if row is None:  # 理论上不会发生
        raise NotFoundError(f"版本 {version_id} 不存在")
    parents = _parents_of(conn, version_id)
    closure = get_closure(conn, version_id)
    return {
        "version_id": row["version_id"],
        "parent_ids": parents,
        "ancestor_ids": closure[1:],
        "created_at": row["created_at"],
        "replayed": replayed,
    }


# ---------------------------------------------------------------------------
# 版本登记
# ---------------------------------------------------------------------------

def register_version(
    conn: sqlite3.Connection, *, version_id: str, parent_ids: list[str]
) -> dict[str, Any]:
    existing = conn.execute(
        "SELECT 1 FROM versions WHERE version_id=?", (version_id,)
    ).fetchone()

    if existing is not None:
        existing_parents = _parents_of(conn, version_id)
        # 父引用是集合语义：提交顺序不影响幂等判定。
        if set(existing_parents) != set(parent_ids) or len(existing_parents) != len(parent_ids):
            raise ConflictError(
                f"版本 {version_id} 已存在，且父版本引用与本次提交不同；"
                "父版本引用创建后不可修改",
                details={
                    "existingParentIds": existing_parents,
                    "incomingParentIds": parent_ids,
                },
            )
        return _version_detail(conn, version_id, replayed=True)

    if version_id in parent_ids:
        raise ValidationError(f"版本 {version_id} 不能引用自身作为父版本")

    for pid in parent_ids:
        row = conn.execute(
            "SELECT 1 FROM versions WHERE version_id=?", (pid,)
        ).fetchone()
        if row is None:
            raise ValidationError(
                f"父版本 {pid} 不存在；登记版本时只能引用已存在的版本",
                details={"unknownParentId": pid},
            )

    ts = now_utc()
    try:
        conn.execute(
            "INSERT INTO versions(version_id, created_at) VALUES(?, ?)", (version_id, ts)
        )
        for position, pid in enumerate(parent_ids):
            conn.execute(
                "INSERT INTO version_parents(version_id, parent_id, position) "
                "VALUES(?, ?, ?)",
                (version_id, pid, position),
            )
        conn.commit()
    except sqlite3.IntegrityError:
        # 并发下可能在检查后被其他请求抢先插入，回退后按“已存在”路径重新判定。
        conn.rollback()
        if conn.execute(
            "SELECT 1 FROM versions WHERE version_id=?", (version_id,)
        ).fetchone() is not None:
            existing_parents = _parents_of(conn, version_id)
            if set(existing_parents) == set(parent_ids) and len(existing_parents) == len(parent_ids):
                return _version_detail(conn, version_id, replayed=True)
            raise ConflictError(
                f"版本 {version_id} 已存在，且父版本引用与本次提交不同；"
                "父版本引用创建后不可修改",
                details={
                    "existingParentIds": existing_parents,
                    "incomingParentIds": parent_ids,
                },
            )
        raise

    return _version_detail(conn, version_id, replayed=False)


# ---------------------------------------------------------------------------
# 许可变更登记
# ---------------------------------------------------------------------------

def add_license_change(
    conn: sqlite3.Connection,
    *,
    change_id: str,
    version_id: str,
    effective_at: datetime,
    allowed_uses: list[str],
) -> dict[str, Any]:
    at = _normalize_or_422(effective_at)

    if conn.execute(
        "SELECT 1 FROM versions WHERE version_id=?", (version_id,)
    ).fetchone() is None:
        raise NotFoundError(
            f"版本 {version_id} 不存在，请先登记版本",
            details={"versionId": version_id},
        )

    digest = _content_hash(version_id, at, allowed_uses)

    existing = conn.execute(
        "SELECT * FROM license_changes WHERE change_id=?", (change_id,)
    ).fetchone()
    if existing is not None:
        if existing["content_hash"] == digest:
            return _change_from_row(existing, replayed=True)
        raise ConflictError(
            f"变更标识 {change_id} 已存在，但提交内容与既有变更不同",
            details={
                "existing": _change_from_row(existing, replayed=False),
                "incoming": {
                    "versionId": version_id,
                    "effectiveAt": at,
                    "allowedUses": allowed_uses,
                },
            },
        )

    ts = now_utc()
    try:
        conn.execute(
            "INSERT INTO license_changes"
            "(change_id, version_id, effective_at, allowed_uses_json, content_hash, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (
                change_id,
                version_id,
                at,
                json.dumps(allowed_uses, ensure_ascii=False),
                digest,
                ts,
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        # 竞争情形 A：同一 change_id 已被其他请求写入。
        row = conn.execute(
            "SELECT * FROM license_changes WHERE change_id=?", (change_id,)
        ).fetchone()
        if row is not None:
            if row["content_hash"] == digest:
                return _change_from_row(row, replayed=True)
            raise ConflictError(
                f"变更标识 {change_id} 已存在，但提交内容与既有变更不同",
                details={"existing": _change_from_row(row, replayed=False)},
            )
        # 竞争情形 B：同版本同时刻已有另一条变更。
        other = conn.execute(
            "SELECT * FROM license_changes WHERE version_id=? AND effective_at=?",
            (version_id, at),
        ).fetchone()
        raise ConflictError(
            f"版本 {version_id} 在 {at} 已存在不同的许可变更 "
            f"({other['change_id'] if other else '?'})；同版本同生效时刻不可有不同变更",
            details={
                "versionId": version_id,
                "effectiveAt": at,
                "existingChangeId": other["change_id"] if other else None,
            },
        )

    row = conn.execute(
        "SELECT * FROM license_changes WHERE change_id=?", (change_id,)
    ).fetchone()
    return _change_from_row(row, replayed=False)


# ---------------------------------------------------------------------------
# 许可判定
# ---------------------------------------------------------------------------

_LATEST_CHANGE_SQL = (
    "SELECT change_id, version_id, effective_at, allowed_uses_json "
    "FROM license_changes "
    "WHERE version_id=? AND effective_at<=? "
    "ORDER BY effective_at DESC, rowid DESC "
    "LIMIT 1"
)


def check_permission(
    conn: sqlite3.Connection, *, version_id: str, use: str, at: datetime
) -> dict[str, Any]:
    use = use.strip()
    if not use:
        raise ValidationError("use 不能为空字符串")

    at_norm = _normalize_or_422(at)

    if conn.execute(
        "SELECT 1 FROM versions WHERE version_id=?", (version_id,)
    ).fetchone() is None:
        raise NotFoundError(
            f"版本 {version_id} 不存在", details={"versionId": version_id}
        )

    closure = get_closure(conn, version_id)

    basis_changes: list[dict[str, Any]] = []
    blocking: list[dict[str, Any]] = []
    intersection: set[str] | None = None
    allowed = True

    for vid in closure:
        row = conn.execute(_LATEST_CHANGE_SQL, (vid, at_norm)).fetchone()
        if row is None:
            allowed = False
            blocking.append(
                {
                    "version_id": vid,
                    "reason": "NO_EFFECTIVE_LICENSE",
                    "change_id": None,
                    "effective_at": None,
                    "allowed_uses": None,
                }
            )
            continue

        uses = json.loads(row["allowed_uses_json"])
        basis_changes.append(
            {
                "version_id": vid,
                "change_id": row["change_id"],
                "effective_at": row["effective_at"],
                "allowed_uses": uses,
            }
        )
        if intersection is None:
            intersection = set(uses)
        else:
            intersection &= set(uses)

        if use not in uses:
            allowed = False
            blocking.append(
                {
                    "version_id": vid,
                    "reason": "USE_NOT_ALLOWED",
                    "change_id": row["change_id"],
                    "effective_at": row["effective_at"],
                    "allowed_uses": uses,
                }
            )

    return {
        "version_id": version_id,
        "use": use,
        "at": at_norm,
        "allowed": allowed,
        "basis_changes": basis_changes,
        "blocking_versions": blocking,
        "considered_versions": closure,
        "intersection_allowed_uses": sorted(intersection or set()),
    }


# ---------------------------------------------------------------------------
# 查询辅助
# ---------------------------------------------------------------------------

def list_versions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT version_id, created_at FROM versions ORDER BY rowid"
    ).fetchall()
    result = []
    for row in rows:
        vid = row["version_id"]
        result.append(
            {
                "version_id": vid,
                "parent_ids": _parents_of(conn, vid),
                "ancestor_ids": get_closure(conn, vid)[1:],
                "created_at": row["created_at"],
                "replayed": False,
            }
        )
    return result


def get_version_detail(
    conn: sqlite3.Connection, version_id: str
) -> dict[str, Any] | None:
    if conn.execute(
        "SELECT 1 FROM versions WHERE version_id=?", (version_id,)
    ).fetchone() is None:
        return None
    return _version_detail(conn, version_id, replayed=False)


def list_license_changes(
    conn: sqlite3.Connection, version_id: str | None = None
) -> list[dict[str, Any]]:
    if version_id is not None:
        if conn.execute(
            "SELECT 1 FROM versions WHERE version_id=?", (version_id,)
        ).fetchone() is None:
            raise NotFoundError(
                f"版本 {version_id} 不存在", details={"versionId": version_id}
            )
        rows = conn.execute(
            "SELECT * FROM license_changes WHERE version_id=? "
            "ORDER BY effective_at, change_id",
            (version_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM license_changes ORDER BY version_id, effective_at, change_id"
        ).fetchall()
    return [_change_from_row(r, replayed=False) for r in rows]
