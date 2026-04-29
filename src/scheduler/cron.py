"""最小 cron 解析器：支持 5 段标准 cron（分 时 日 月 周）。

语法：
    字段 := 通配 | 列表 | 范围 | 步长
    *           全部
    N           单值
    A-B         范围
    A,B,C       列表
    */N 或 A-B/N 步长

dow: 0..6，Sunday=0（与 crontab 一致）。
"""

from __future__ import annotations

from datetime import datetime, timedelta

_FIELD_BOUNDS = [
    (0, 59),  # minute
    (0, 23),  # hour
    (1, 31),  # day of month
    (1, 12),  # month
    (0, 6),   # day of week (Sun=0)
]


def _parse_field(field: str, lo: int, hi: int) -> set[int]:
    vals: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        if not part:
            raise ValueError(f"Empty cron segment in '{field}'")
        step = 1
        if "/" in part:
            head, s = part.split("/", 1)
            step = int(s)
            if step <= 0:
                raise ValueError(f"Invalid step in '{field}'")
            part = head or "*"
        if part == "*":
            start, end = lo, hi
        elif "-" in part:
            a, b = part.split("-", 1)
            start, end = int(a), int(b)
        else:
            start = end = int(part)
        if start < lo or end > hi or start > end:
            raise ValueError(f"Out-of-range cron segment '{part}' for bounds [{lo},{hi}]")
        for v in range(start, end + 1, step):
            vals.add(v)
    return vals


def _parse(expr: str) -> list[set[int]]:
    parts = expr.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Cron expression must have 5 fields, got {len(parts)}: '{expr}'")
    return [_parse_field(p, lo, hi) for p, (lo, hi) in zip(parts, _FIELD_BOUNDS, strict=False)]


def validate_cron(expr: str) -> tuple[bool, str]:
    """返回 (ok, message)。"""
    try:
        _parse(expr)
        return True, ""
    except Exception as e:
        return False, str(e)


def _dow_sun_first(dt: datetime) -> int:
    """把 Python 的 Mon=0..Sun=6 转成 cron 的 Sun=0..Sat=6。"""
    return (dt.weekday() + 1) % 7


def cron_match(expr: str, dt: datetime) -> bool:
    try:
        minute_set, hour_set, dom_set, mon_set, dow_set = _parse(expr)
    except Exception:
        return False
    return (
        dt.minute in minute_set
        and dt.hour in hour_set
        and dt.day in dom_set
        and dt.month in mon_set
        and _dow_sun_first(dt) in dow_set
    )


def next_run_after(expr: str, from_dt: datetime, max_minutes: int = 366 * 24 * 60) -> datetime | None:
    """从 from_dt 之后开始向前扫描，返回下一个匹配时刻；最多扫描约 1 年。"""
    try:
        _parse(expr)
    except Exception:
        return None
    # 从下一分钟开始扫，精确到分钟
    cur = (from_dt.replace(second=0, microsecond=0) + timedelta(minutes=1))
    for _ in range(max_minutes):
        if cron_match(expr, cur):
            return cur
        cur += timedelta(minutes=1)
    return None
