from src.domain.entities import Post
from src.domain.services.ai_processor import MergedTopic
from src.infrastructure.ai.importance_scorer import score_topics
from src.infrastructure.config.settings import ScoringConfig


def _post(
    post_id: int,
    categories: list[str],
    importance: float,
    likes: int = 0,
) -> Post:
    return Post(
        id=post_id,
        source="twitter",
        external_id=str(post_id),
        url=f"https://example.com/{post_id}",
        author=f"author-{post_id}",
        content_text=f"post {post_id}",
        category_names=categories,
        importance_score=importance,
        engagement_likes=likes,
    )


def test_score_topics_assigns_one_primary_category_for_overlapping_topic():
    posts = [
        _post(1, ["AI", "Startup"], 0.9),
        _post(2, ["Startup"], 0.4),
    ]
    topics = [
        MergedTopic(
            post_ids=[1],
            headline="overlapping high score topic",
            body_bullets=[],
            primary_category="Startup",
            importance_score=0.5,
            sources=["twitter"],
        ),
        MergedTopic(
            post_ids=[2],
            headline="startup only topic",
            body_bullets=[],
            primary_category="Startup",
            importance_score=0.5,
            sources=["twitter"],
        ),
    ]

    score_topics(
        topics,
        {str(p.id): p for p in posts},
        posts,
        ScoringConfig({"engagement_weight": 0.2}),
    )

    assert topics[0].primary_category == "AI"
    assert topics[0].score_features["category_scores"].keys() == {"AI", "Startup"}
    assert topics[0].importance_score == 1.0


def test_score_topics_keeps_engagement_as_secondary_signal():
    posts = [
        _post(1, ["AI"], 0.9, likes=0),
        _post(2, ["AI"], 0.6, likes=1000),
    ]
    topics = [
        MergedTopic([1], "higher ai score", [], "AI", 0.5, ["twitter"]),
        MergedTopic([2], "higher engagement", [], "AI", 0.5, ["twitter"]),
    ]

    score_topics(
        topics,
        {str(p.id): p for p in posts},
        posts,
        ScoringConfig({"engagement_weight": 0.2}),
    )

    assert topics[0].importance_score > topics[1].importance_score
