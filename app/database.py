"""SQLite 访问层：连接管理、建表初始化。

设计要点：
- 每个请求使用独立短连接，开启外键约束与 WAL，配合 busy_timeout 应对并发；
- 父版本引用一旦写入即不可修改（代码层不提供修改接口，表结构也不支持级联变更）；
- license_changes 上对 (version_id, effective_at) 建唯一索引，
  从存储层强制“同一版本同一生效时刻至多一条变更”。
"""
from __future__ import annotations

import contextlib
import os
import sqlite3
from typing import Iterator

from . import config

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS versions (
    version_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS version_parents (
    version_id TEXT NOT NULL REFERENCES versions(version_id),
    parent_id  TEXT NOT NULL REFERENCES versions(version_id),
    position   INTEGER NOT NULL,
    PRIMARY KEY (version_id, parent_id)
);

CREATE TABLE IF NOT EXISTS license_changes (
    change_id         TEXT PRIMARY KEY,
    version_id        TEXT NOT NULL REFERENCES versions(version_id),
    effective_at      TEXT NOT NULL,
    allowed_uses_json TEXT NOT NULL,
    content_hash      TEXT NOT NULL,
    created_at        TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_changes_version_effective
    ON license_changes(version_id, effective_at);

CREATE INDEX IF NOT EXISTS idx_changes_version_time
    ON license_changes(version_id, effective_at, change_id);
"""


def connect(database_path: str | None = None) -> sqlite3.Connection:
    """打开一个开启外键约束的 SQLite 连接。"""
    path = database_path or config.DATABASE_PATH
    db_dir = os.path.dirname(os.path.abspath(path))
    os.makedirs(db_dir, exist_ok=True)

    conn = sqlite3.connect(path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


@contextlib.contextmanager
def session() -> Iterator[sqlite3.Connection]:
    """请求级连接上下文管理器。"""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def init_db(database_path: str | None = None) -> None:
    """创建表结构并启用 WAL（幂等）。"""
    conn = connect(database_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()
