"""자동 팔로우 — 후보 집계·제외 로직과 팔로우 버튼 셀렉터 회귀 방지.

셀렉터 테스트가 있는 이유: X 프로필에는 **대상 계정 말고 추천 계정의 팔로우
버튼도 함께 렌더된다**(실측: @CNBC 프로필에 팔로우 버튼 4개). 첫 매치를 집는
방식이면 엉뚱한 계정을 팔로우하므로 핸들 정확 매칭이어야 한다.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

from src.application.use_cases.follow_accounts import FollowAccountsUseCase
from src.infrastructure.collectors.account_follower import _selectors
from src.infrastructure.config.settings import FollowConfig


# ─── 셀렉터 ───


def test_selector_targets_exact_handle():
    """핸들이 aria-label에 정확히 박혀야 한다 — 추천 계정 오팔로우 방지."""
    follow, unfollow = _selectors("CNBC")
    assert 'aria-label$="@CNBC"' in follow
    assert 'aria-label$="@CNBC"' in unfollow
    assert '-follow"]' in follow
    assert '-unfollow"]' in unfollow


def test_selector_not_confused_by_handle_suffix():
    """@CNBC 셀렉터가 @SquawkCNBC를 잡으면 안 된다 (@ 접두가 경계 역할)."""
    follow, _ = _selectors("CNBC")
    # 실제 CSS 매칭 규칙을 문자열로 재현: aria-label 이 "@CNBC"로 끝나는지
    assert "팔로우 @SquawkCNBC".endswith("@CNBC") is False
    assert "팔로우 @CNBC".endswith("@CNBC") is True
    assert "@CNBC" in follow


# ─── 후보 집계 ───


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    """임시 DB를 쓰는 레포 (실제 data/posts.db를 건드리지 않는다)."""
    import src.infrastructure.database.repositories.post_repo_sqlite as mod

    monkeypatch.setattr(mod, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(mod._thread_local, "db", None, raising=False)
    r = mod.PostRepositorySQLite()
    yield r
    conn = mod._get_db()
    conn.close()
    monkeypatch.setattr(mod._thread_local, "db", None, raising=False)


def _add_liked(repo, author_url: str, n: int, source: str = "twitter"):
    """author_url 계정의 좋아요된 게시물 n건을 심는다."""
    import src.infrastructure.database.repositories.post_repo_sqlite as mod

    conn = mod._get_db()
    for i in range(n):
        now = datetime.utcnow().isoformat()
        conn.execute(
            "INSERT INTO posts (id, source, external_id, content_text, author, author_url,"
            " url, collected_at, liked_at, is_relevant) VALUES (?,?,?,?,?,?,?,?,?,1)",
            (f"{author_url}#{i}", source, f"{author_url}#{i}", "본문", "이름",
             author_url, f"https://x.com/x/status/{i}", now, now),
        )
    conn.commit()


def test_candidates_respect_min_likes(repo):
    _add_liked(repo, "https://x.com/alpha", 5)
    _add_liked(repo, "https://x.com/beta", 4)

    got = repo.get_follow_candidates(source="twitter", min_likes=5, limit=10)

    assert [c["screen_name"] for c in got] == ["alpha"]
    assert got[0]["like_count"] == 5


def test_followed_account_excluded_next_time(repo):
    _add_liked(repo, "https://x.com/alpha", 6)
    assert repo.get_follow_candidates(source="twitter", min_likes=5, limit=10)

    repo.record_follow("https://x.com/alpha", "twitter", "alpha", 6, "followed")

    assert repo.get_follow_candidates(source="twitter", min_likes=5, limit=10) == []


def test_already_following_also_excluded(repo):
    _add_liked(repo, "https://x.com/alpha", 6)
    repo.record_follow("https://x.com/alpha", "twitter", "alpha", 6, "already")
    assert repo.get_follow_candidates(source="twitter", min_likes=5, limit=10) == []


def test_failed_retries_until_cap(repo):
    """실패는 재시도하되 상한을 넘으면 후보에서 빠진다 (무한 재시도 방지)."""
    _add_liked(repo, "https://x.com/alpha", 6)

    for _ in range(2):
        repo.record_follow("https://x.com/alpha", "twitter", "alpha", 6, "failed")
        assert repo.get_follow_candidates(
            source="twitter", min_likes=5, limit=10, max_attempts=3
        ), "상한 전에는 재시도 후보로 남아야 한다"

    repo.record_follow("https://x.com/alpha", "twitter", "alpha", 6, "failed")
    assert repo.get_follow_candidates(
        source="twitter", min_likes=5, limit=10, max_attempts=3
    ) == []


def test_posts_without_author_are_ignored(repo):
    """작성자 미상 수집분(2026-08 스키마 변경 이전)은 집계에 끼지 않는다."""
    _add_liked(repo, "", 9)
    assert repo.get_follow_candidates(source="twitter", min_likes=5, limit=10) == []


# ─── 유즈케이스 ───


class _FakeFollower:
    def __init__(self, status="followed"):
        self.status = status
        self.calls = []

    async def follow_accounts(self, source, candidates):
        self.calls.append((source, candidates))
        return [{**c, "status": self.status} for c in candidates]


async def test_dry_run_does_not_record(repo):
    """dry-run이 기록하면 실제 적용 때 후보에서 빠져 영영 팔로우되지 않는다."""
    _add_liked(repo, "https://x.com/alpha", 6)
    cfg = FollowConfig({"enabled": True, "dry_run": True, "min_likes": 5})
    uc = FollowAccountsUseCase(repo, _FakeFollower(), cfg)

    await uc.execute()

    assert repo.get_follow_candidates(source="twitter", min_likes=5, limit=10), \
        "dry-run 후에도 후보로 남아야 한다"


async def test_real_run_records_and_reports(repo):
    _add_liked(repo, "https://x.com/alpha", 6)
    cfg = FollowConfig({"enabled": True, "dry_run": False, "min_likes": 5})
    uc = FollowAccountsUseCase(repo, _FakeFollower(), cfg)

    result = await uc.execute()

    assert result == {"twitter": 1}
    assert repo.get_follow_candidates(source="twitter", min_likes=5, limit=10) == []


async def test_disabled_does_nothing(repo):
    _add_liked(repo, "https://x.com/alpha", 6)
    follower = _FakeFollower()
    cfg = FollowConfig({"enabled": False, "min_likes": 5})

    assert await FollowAccountsUseCase(repo, follower, cfg).execute() == {}
    assert follower.calls == []
