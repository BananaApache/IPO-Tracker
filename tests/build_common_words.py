"""Regenerates backend/match/common_words.py from a Hacker News corpus.

    uv run python -m tests.build_common_words

Requires an HN corpus on disk (tests/build_matching_corpus.py) and a system
word list at /usr/share/dict/words. Run on a development machine; the output is
committed so production never needs either.
"""
