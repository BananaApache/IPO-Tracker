"""Opaque cursors for keyset pagination.

Mental model, since this replaces the LIMIT/OFFSET you'd reach for by default:

OFFSET makes the database walk and discard every row it skips, so page 500 costs
500 pages of work, and any row inserted while a user pages will shift everything
down -- they see a duplicate or miss a row entirely. Keyset pagination instead
remembers *where the last page ended* and asks for "the next N rows after this
exact point." The database jumps straight there on the sort index, so every page
costs the same, and a concurrent insert cannot shift a page you already passed.

The cost is that you can only step forward or backward from a known position --
there is no "jump to page 47." For an infinite-scrolling surveillance feed that
is the right trade.

The cursor is base64 of a JSON blob purely to make it *opaque*: clients that
cannot read it also cannot depend on its shape, which leaves us free to change
the sort key later without breaking them. It is not encryption and carries
nothing secret -- only values already visible in the response.
"""

import base64
import binascii
import json
from typing import Any


def encode_cursor(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), default=str).encode()
    # Strip '=' padding for a URL-friendly cursor; decode_cursor puts it back.
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> dict[str, Any]:
    """Raises ValueError if the cursor is not one we produced."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("malformed cursor") from exc
    if not isinstance(payload, dict):
        raise ValueError("malformed cursor")
    return payload
