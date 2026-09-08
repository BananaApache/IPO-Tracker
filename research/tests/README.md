Tests for the research module. Kept out of `tests/` on purpose.

`tests/conftest.py` refuses to run any session against a non-local database, and
it is right to: the main suite reads `.env` and would otherwise open
transactions against production Neon. That guard fires at
`pytest_sessionstart`, so it blocks the whole session regardless of which test
was selected.

These tests touch no database at all — they cover pure functions — so rather
than weakening the guard or overriding it with `TEST_ALLOW_REMOTE_DB=1`, they
live under their own root and run separately:

    uv run --group research python -m pytest research/tests -q
