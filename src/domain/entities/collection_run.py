from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class CollectionRun:
    """수집 실행 로그."""

    source: str
    started_at: datetime = field(default_factory=datetime.utcnow)

    id: Optional[int] = None
    completed_at: Optional[datetime] = None
    status: str = "running"  # running, success, failed, partial
    posts_collected: int = 0
    error_message: Optional[str] = None
