"""Test the ``wiki.util.markdown`` module."""

from __future__ import annotations

from typing import Optional

import pytest

from wiki.util.markdown import find_heading, mask_code

__all__ = [
    'test_mask_code',
    'test_find_heading',
]


@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        ('```\nsecret\n```\nshown', '\n\n\nshown'),
        ('~~~\nsecret\n~~~\nshown', '\n\n\nshown'),
        ('```python\ncode\n```\nshown', '\n\n\nshown'),
        ('   ```\nsecret\n   ```\nshown', '\n\n\nshown'),
        ('```\nnever closed', '\n'),
        ('an `inline span` masked', 'an  masked'),
        ('a ``double run`` here', 'a  here'),
        ('wraps `a\nnewline` once', 'wraps \n once'),
        ('never `crosses\n\na blank` line', 'never `crosses\n\na blank` line'),
        ('an `unclosed run stays', 'an `unclosed run stays'),
    ],
    ids=[
        'backtick-fence',
        'tilde-fence',
        'fence-info-string',
        'fence-indented',
        'fence-unclosed',
        'inline-span',
        'inline-double-run',
        'span-wraps-newline',
        'span-stops-at-blank-line',
        'dangling-backtick',
    ],
)
def test_mask_code(text: str, expected: str) -> None:
    """``mask_code`` blanks fences and spans, preserving line structure.

    Fenced lines become empty lines and a span's interior newlines
    survive, so a masked scan attributes findings to source lines.
    """
    assert mask_code(text) == expected


@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        ('# Title\n\nbody', (0, 'Title')),
        ('intro\n\n# Title', (2, 'Title')),
        ('```\n# sample\n```\n# Title', (3, 'Title')),
        ('# `code`', (0, '`code`')),
        ('## deeper heading only', None),
        ('```\n# fenced\n```', None),
    ],
    ids=[
        'first-line',
        'after-prose',
        'skips-fenced',
        'code-span-title',
        'no-h1',
        'only-fenced',
    ],
)
def test_find_heading(text: str, expected: Optional[tuple[int, str]]) -> None:
    """``find_heading`` returns the first H1 line index outside fences.

    A heading whose text is an inline code span still matches -- the
    scan walks fences only, never inline spans, so the H1 rewrite sees
    the same line a reader does.
    """
    assert find_heading(text) == expected
