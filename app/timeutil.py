"""UTC ISO 8601 시간 문자열 도우미. 저장·응답 모두 'YYYY-MM-DDTHH:MM:SSZ' 형식."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def from_iso(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def plus(dt: datetime, *, minutes: int = 0, hours: int = 0) -> datetime:
    return dt + timedelta(minutes=minutes, hours=hours)
