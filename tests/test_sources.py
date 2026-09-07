"""Adapter transforms.

Hacker News is exercised against real captured field names (verified live on
2026-09-05: 1000 items fetched, all unique, all tz-aware).

GDELT was cut after it returned 429 to every request across three sessions --
see docs/sources.md.
"""

import asyncio
from datetime import UTC, datetime

from backend.config import Settings
from backend.sources.base import RawMention, hash_author
from backend.sources.hackernews import HackerNewsAdapter

SETTINGS = Settings(mention_hash_salt="test-salt")


def test_author_hash_never_reveals_the_handle():
    handle = "ExampleUser"
    digest = hash_author(handle, "salt")
    assert digest is not None
    assert handle.lower() not in digest
    assert len(digest) == 64
    # Same handle, different salt -> unrelated digest. A leaked database alone
    # cannot be replayed against a username dictionary.
    assert digest != hash_author(handle, "other-salt")
    # Case and whitespace do not fork the identity.
    assert digest == hash_author("  exampleuser  ", "salt")
    assert hash_author(None, "salt") is None


def test_raw_mention_has_nowhere_to_put_a_username():
    fields = set(RawMention.__dataclass_fields__)
    assert "author_hash" in fields
    for forbidden in ("author", "username", "user_id", "handle", "profile_url"):
        assert forbidden not in fields


def test_hn_transform():
    adapter = HackerNewsAdapter(SETTINGS, client=object())  # client unused here
    hit = {
        "objectID": "49580717", "title": "Some story", "author": "pg",
        "points": 120, "num_comments": 45, "url": "https://example.com/a",
        "created_at": "2026-09-05T21:10:00Z",
    }
    m = adapter._to_mention(hit)
    assert m.source == "hn"
    assert m.source_uid == "49580717"
    assert m.posted_at == datetime(2026, 9, 5, 21, 10, tzinfo=UTC)
    assert m.engagement_score == 165          # points + comments
    assert m.author_hash == hash_author("pg", "test-salt")
    assert m.url == "https://example.com/a"


def test_hn_comment_falls_back_to_story_title_and_item_url():
    adapter = HackerNewsAdapter(SETTINGS, client=object())
    m = adapter._to_mention({
        "objectID": "1", "story_title": "Parent", "comment_text": "x" * 900,
        "author": "someone", "created_at": "2026-09-05T00:00:00Z",
    })
    assert m.title == "Parent"
    assert len(m.body_excerpt) == 500          # excerpt, not the whole comment
    assert m.url == "https://news.ycombinator.com/item?id=1"


def test_hn_skips_items_without_id_or_timestamp():
    adapter = HackerNewsAdapter(SETTINGS, client=object())
    assert adapter._to_mention({"title": "no id"}) is None
    assert adapter._to_mention({"objectID": "1"}) is None




