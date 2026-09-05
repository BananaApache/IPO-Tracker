"""Adapter transforms.

Hacker News is exercised against real captured field names (verified live on
2026-09-05: 1000 items fetched, all unique, all tz-aware).

GDELT is exercised against a **synthetic** payload built from the documented
DOC 2.0 `artlist` schema. Its DOC endpoint returned 429 to every request from
this machine over ~10 minutes, including single requests after 150s of silence,
while GDELT's `summary` endpoint returned 200 -- so the host is reachable and
this is not our request rate. The transform below is therefore tested; the field
*mapping* is documented-but-unconfirmed and is marked as such in the README.
"""

import asyncio
from datetime import UTC, datetime

from backend.config import Settings
from backend.sources.base import RawMention, hash_author
from backend.sources.gdelt import GdeltAdapter
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


def test_gdelt_transform_synthetic():
    adapter = GdeltAdapter(SETTINGS, client=object())
    m = adapter._to_mention({
        "url": "https://news.example.com/story",
        "title": "Company X files for IPO",
        "seendate": "20260905T204500Z",
        "domain": "news.example.com",
        "language": "English",
    })
    assert m.source == "gdelt"
    assert m.source_uid == "https://news.example.com/story"   # GDELT has no article id
    assert m.posted_at == datetime(2026, 9, 5, 20, 45, tzinfo=UTC)
    assert m.channel == "news.example.com"
    # GDELT returns no byline and no engagement signal. Both are left empty
    # rather than invented -- the rollup must be able to tell "no data" from 0.
    assert m.author_hash is None
    assert m.engagement_score == 0


def test_gdelt_skips_malformed_records():
    adapter = GdeltAdapter(SETTINGS, client=object())
    assert adapter._to_mention({"title": "no url"}) is None
    assert adapter._to_mention({"url": "u", "seendate": "not-a-date"}) is None


def test_gdelt_treats_a_200_plain_text_refusal_as_a_failure():
    """GDELT signals throttling with a plain-text body, sometimes at HTTP 200.
    Parsed naively that becomes "no articles found" -- a silent zero."""
    import httpx

    from backend.sources.gdelt import _GdeltClient

    client = _GdeltClient(user_agent="x", per_second=1)
    response = httpx.Response(
        200,
        text="Please limit requests to one every 5 seconds or contact ...",
        request=httpx.Request("GET", "https://api.gdeltproject.org/x"),
    )
    try:
        client.inspect(response)
    except httpx.HTTPStatusError:
        pass
    else:
        raise AssertionError("a 200 plain-text refusal must not be accepted")
    asyncio.run(client.aclose())
