"""결정적 프리필터 — 명백한 쓰레기만 자르고 애매하면 반드시 LLM으로 통과.

false negative(진짜 뉴스가 LLM을 못 보고 소실)가 이 시스템 최악의 사고
유형이므로, '컷' 케이스보다 '통과' 케이스 검증이 더 중요하다.
"""

from __future__ import annotations

from src.application.use_cases.process_posts import (
    ProcessPostsUseCase,
    _is_obvious_junk,
)
from src.domain.entities import Post


def _post(text: str, source: str = "twitter") -> Post:
    return Post(
        source=source, external_id="e1", url="https://x.com/1",
        author="a", content_text=text,
    )


# ── 컷: 명백한 쓰레기 ──────────────────────────────────────────


def test_empty_text_is_junk():
    assert _is_obvious_junk(_post(""))
    assert _is_obvious_junk(_post("   \n  "))


def test_ultra_short_reaction_is_junk():
    assert _is_obvious_junk(_post("ㅋㅋㅋㅋㅋ"))
    assert _is_obvious_junk(_post("축하드립니다!"))
    assert _is_obvious_junk(_post("So true"))


def test_link_only_short_reaction_is_junk():
    assert _is_obvious_junk(_post("이거 대박이네요 https://example.com/article"))
    assert _is_obvious_junk(_post("https://example.com/a https://example.com/b"))


# ── 통과: 애매하거나 실제 뉴스일 수 있는 것 ─────────────────────


def test_substantive_text_passes():
    assert not _is_obvious_junk(
        _post("Vercel이 Next.js 15.2를 출시했다. Turbopack 빌드 시간이 40% 단축됐다.")
    )


def test_short_text_with_link_but_enough_body_passes():
    assert not _is_obvious_junk(
        _post("OpenAI가 GPT-5 코딩 벤치마크 40% 향상을 발표 https://openai.com/news")
    )


def test_no_link_medium_text_passes():
    # 링크가 없으면 초단문 하한(15자)만 적용 — 그 이상은 내용 판단이라 LLM 몫
    assert not _is_obvious_junk(_post("삼성전자 6G 로드맵 발표"))


def test_curated_sources_are_never_prefiltered():
    # news/36kr/producthunt는 헤드라인 위주 짧은 본문이 정상 — 길이 컷 금지
    for source in ("news", "36kr", "producthunt"):
        assert not _is_obvious_junk(_post("짧은 헤드라인 https://n.com/1", source=source))
        assert not _is_obvious_junk(_post("", source=source))


# ── 분리 동작 ──────────────────────────────────────────────────


def test_split_marks_junk_filtered_and_keeps_rest():
    uc = ProcessPostsUseCase.__new__(ProcessPostsUseCase)
    junk_post = _post("ㅋㅋ")
    real_post = _post("Anthropic이 Claude 5 패밀리를 공개했다. 최상위 티어 모델이 추가됐다.")

    keep, junk = uc._split_obvious_junk([junk_post, real_post])

    assert keep == [real_post]
    assert junk == [junk_post]
    assert junk_post.is_relevant is False
    assert junk_post.summary == "[filtered]"
    assert real_post.is_relevant is None  # 판정은 LLM 몫으로 남는다
