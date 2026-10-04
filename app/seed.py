"""演示数据初始化（幂等）。

仅当 versions 表为空时写入一条三版本链：
    ds-v1  ──>  ds-v2  ──>  ds-v3

许可时间线（UTC）：
- 2026-01-01T00:00:00Z 起
    ds-v1: research, commercial
    ds-v2: research, education
    ds-v3: research
- 2026-06-01T00:00:00Z 起 ds-v2 收回 education，仅保留 research

判定 ds-v3 / research 时沿 v3、v2、v1 取交集；
判定 ds-v3 / commercial 时 v2、v3 均阻断。
"""
from __future__ import annotations

from .database import init_db, session
from .services import add_license_change, register_version

T0 = "2026-01-01T00:00:00+00:00"
T1 = "2026-06-01T00:00:00+00:00"


def seed() -> bool:
    """写入演示数据；数据库非空或已写入时返回 False。"""
    from datetime import datetime

    init_db()
    with session() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM versions").fetchone()["c"]
        if count:
            return False

        register_version(conn, version_id="ds-v1", parent_ids=[])
        register_version(conn, version_id="ds-v2", parent_ids=["ds-v1"])
        register_version(conn, version_id="ds-v3", parent_ids=["ds-v2"])

        add_license_change(
            conn,
            change_id="LC-V1-INIT",
            version_id="ds-v1",
            effective_at=datetime.fromisoformat(T0),
            allowed_uses=["research", "commercial"],
        )
        add_license_change(
            conn,
            change_id="LC-V2-INIT",
            version_id="ds-v2",
            effective_at=datetime.fromisoformat(T0),
            allowed_uses=["research", "education"],
        )
        add_license_change(
            conn,
            change_id="LC-V3-INIT",
            version_id="ds-v3",
            effective_at=datetime.fromisoformat(T0),
            allowed_uses=["research"],
        )
        add_license_change(
            conn,
            change_id="LC-V2-TIGHTEN",
            version_id="ds-v2",
            effective_at=datetime.fromisoformat(T1),
            allowed_uses=["research"],
        )
        return True


if __name__ == "__main__":
    created = seed()
    print("demo data seeded" if created else "demo data already present, skipped")
