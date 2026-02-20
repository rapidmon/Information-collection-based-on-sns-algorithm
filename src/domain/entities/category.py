from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class Category:
    """토픽 카테고리."""

    name: str
    name_ko: str

    id: Optional[int] = None
    color: str = "#888888"
    keywords: list[str] | None = None
