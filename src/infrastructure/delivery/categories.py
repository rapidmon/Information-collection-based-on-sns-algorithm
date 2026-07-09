"""카테고리 표시 정보 단일 소스.

한국어 라벨/이모지가 briefing_builder·email_renderer·api 등 여러 곳에 중복돼
드리프트가 나던 것을 여기로 모은다. (표시 '순서'는 렌더러별로 다를 수 있어 각자 보유)
"""

from __future__ import annotations

# 카테고리 키 → 한국어 라벨
CATEGORY_KO: dict[str, str] = {
    "AI": "AI",
    "Semiconductor": "반도체",
    "Cloud": "클라우드·인프라",
    "BigTech": "빅테크",
    "Startup": "스타트업",
    "Regulation": "규제/정책",
    "Coding": "코딩",
    "Showcase": "메이커·쇼케이스",
    "Other": "기타",
}

VALID_BRIEFING_CATEGORIES: tuple[str, ...] = (
    "AI",
    "Semiconductor",
    "Cloud",
    "BigTech",
    "Startup",
    "Regulation",
    "Coding",
    "Showcase",
)

# 카테고리 키 → 이모지 (이메일 라벨용)
CATEGORY_EMOJI: dict[str, str] = {
    "AI": "🧠",
    "Semiconductor": "🔬",
    "Cloud": "☁️",
    "BigTech": "🏢",
    "Startup": "🚀",
    "Regulation": "⚖️",
    "Coding": "💻",
    "Showcase": "✨",
    "Other": "🗂",
}
