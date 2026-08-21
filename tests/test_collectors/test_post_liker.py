from __future__ import annotations

from datetime import datetime

import pytest

from src.infrastructure.collectors.post_liker import (
    _LIKE_SELECTORS,
    _THREADS_MAIN_POST,
    _get_like_state,
)


class _FakeLocator:
    def __init__(self, path: tuple[str, ...], counts: dict[tuple[str, ...], int]):
        self.path = path
        self._counts = counts

    @property
    def first(self):
        return self

    def locator(self, selector: str):
        return _FakeLocator((*self.path, selector), self._counts)

    async def count(self) -> int:
        return self._counts.get(self.path, 0)


class _FakePage:
    def __init__(self, counts: dict[tuple[str, ...], int]):
        self._counts = counts

    def locator(self, selector: str):
        return _FakeLocator((selector,), self._counts)


async def test_threads_like_uses_main_post_locator():
    not_liked = _LIKE_SELECTORS["threads"]["not_liked"]
    control_xpath = 'xpath=ancestor::*[@role="button"][1]'
    counts = {(_THREADS_MAIN_POST, not_liked): 1}

    state, control = await _get_like_state(_FakePage(counts), "threads")

    assert state == "not_liked"
    assert control.path == (_THREADS_MAIN_POST, not_liked, control_xpath)


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    import src.infrastructure.database.repositories.post_repo_sqlite as mod

    monkeypatch.setattr(mod, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(mod._thread_local, "db", None, raising=False)
    repository = mod.PostRepositorySQLite()
    yield repository
    mod._get_db().close()
    monkeypatch.setattr(mod._thread_local, "db", None, raising=False)


def _add_linkedin_post(repo, post_id: str, url: str) -> None:
    import src.infrastructure.database.repositories.post_repo_sqlite as mod

    now = datetime.utcnow().isoformat()
    conn = mod._get_db()
    conn.execute(
        "INSERT INTO posts (id, source, external_id, url, content_text, collected_at, "
        "importance_score, is_relevant) VALUES (?,?,?,?,?,?,?,1)",
        (post_id, "linkedin", post_id, url, "본문", now, 0.9),
    )
    conn.commit()


def test_linkedin_like_candidates_require_individual_post_url(repo):
    valid = "https://www.linkedin.com/feed/update/urn:li:ugcPost:123/"
    _add_linkedin_post(repo, "valid", valid)
    _add_linkedin_post(repo, "company", "https://www.linkedin.com/company/openai/")
    _add_linkedin_post(repo, "profile", "https://www.linkedin.com/in/example/")

    got = repo.get_likeable(source="linkedin", min_importance=0.7, limit=10)

    assert [post.id for post in got] == ["valid"]
