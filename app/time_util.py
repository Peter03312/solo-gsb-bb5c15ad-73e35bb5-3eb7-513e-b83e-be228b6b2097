"""UTC 时刻处理工具。

服务内部统一使用“规范化后的 UTC ISO-8601 字符串”存取时刻，
固定形如 ``2026-10-04T08:30:00.000000+00:00``。该格式定宽且按字典序
与时间顺序一致，因此可以直接在 SQLite 中做字符串比较。
"""
from __future__ import annotations

from datetime import datetime, timezone

FORMAT = "%Y-%m-%dT%H:%M:%S.%f%z"
# 统一的规范化 UTC 输出后缀（%z 原生输出 +0000，这里规范为 +00:00）。


def normalize_utc(value: datetime) -> str:
    """接受带时区信息的时间，转换为 UTC 并规范化输出。

    不带 tzinfo 的时间无法确定绝对时刻，抛出 ValueError。
    输出固定形如 ``2026-10-04T08:30:00.000000+00:00``：定宽且按字典序
    与时间顺序一致，因此可以直接在 SQLite 中做字符串比较。
    """
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("时间必须携带时区信息（建议直接使用 UTC）")
    as_utc = value.astimezone(timezone.utc)
    return as_utc.isoformat(timespec="microseconds")


def now_utc() -> str:
    return normalize_utc(datetime.now(timezone.utc))
