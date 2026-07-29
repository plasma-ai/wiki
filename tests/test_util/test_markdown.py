"""Test the ``wiki.util.markdown`` module."""

from __future__ import annotations

from typing import Optional

import pytest

from wiki.util.markdown import (
    find_heading,
    mask_code,
    mask_comments,
    mask_indented_code,
)

__all__ = [
    'test_mask_code',
    'test_mask_comments',
    'test_mask_indented_code',
    'test_find_heading',
]


@pytest.mark.parametrize(
    argnames=('text', 'expected'),
    argvalues=[
        ('```\nsecret\n```\nshown', '\n\n\nshown'),
        ('~~~\nsecret\n~~~\nshown', '\n\n\nshown'),
        ('```python\ncode\n```\nshown', '\n\n\nshown'),
        ('   ```\nsecret\n   ```\nshown', '\n\n\nshown'),
        ('```\nnever closed', '\n'),
        ('```\ncode\n````\nshown', '\n\n\nshown'),
        ('````\n```\nnot closed', '\n\n'),
        ('```\ncode\n    ```\nstill hidden', '\n\n\n'),
        ('```a`b\nshown', '```a`b\nshown'),
        ('an `inline span` masked', 'an  masked'),
        ('a ``double run`` here', 'a  here'),
        ('use `` ` `` to escape', 'use  to escape'),
        ('a ``x`y`` b', 'a  b'),
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
        'fence-longer-close',
        'fence-shorter-no-close',
        'fence-overindented-no-close',
        'fence-backtick-info-no-fence',
        'inline-span',
        'inline-double-run',
        'span-literal-backtick',
        'span-inner-run',
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
    argnames=('text', 'expected'),
    argvalues=[
        ('keep <!-- hide [[x]] --> keep', 'keep  keep'),
        ('a\n<!--\nhidden [[x]]\n-->\nb', 'a\n\n\n\nb'),
        ('a <!--x--> b <!--y--> c', 'a  b  c'),
        ('<!-- start: no-lint -->', ''),
        ('<!-- never closed [[x]]', '<!-- never closed [[x]]'),
    ],
    ids=[
        'inline-comment',
        'multiline-comment',
        'two-comments-one-line',
        'region-directive-masked-too',
        'unterminated-stays',
    ],
)
def test_mask_comments(text: str, expected: str) -> None:
    """``mask_comments`` blanks comment bodies, preserving line structure.

    Region directives are comments themselves and blank with the rest,
    so a caller parsing regions must do so before masking.
    """
    assert mask_comments(text) == expected


@pytest.mark.parametrize(
    argnames=('text', 'expected'),
    argvalues=[
        ('para:\n\n    code [[x]]\n', 'para:\n\n\n'),
        ('para:\n\n\tcode [[x]]\n', 'para:\n\n\n'),
        ('    code [[x]] at start\n', '\n'),
        ('para:\n\n    a [[x]]\n\n    b [[x]]\n', 'para:\n\n\n\n\n'),
        (
            'para:\n\n    code [[x]]\nback to prose [[x]]\n',
            'para:\n\n\nback to prose [[x]]\n',
        ),
        (
            'para\n    lazy [[x]] continuation\n',
            'para\n    lazy [[x]] continuation\n',
        ),
        (
            '- item\n\n    still the list [[x]]\n',
            '- item\n\n    still the list [[x]]\n',
        ),
        ('- item\n    nested [[x]]\n', '- item\n    nested [[x]]\n'),
        (
            '1. item\n\n    still the list [[x]]\n',
            '1. item\n\n    still the list [[x]]\n',
        ),
    ],
    ids=[
        'indented-block',
        'tab-indented',
        'block-at-start',
        'blank-line-inside-block',
        'block-ends-at-unindented',
        'lazy-continuation-not-code',
        'bullet-continuation-not-code',
        'nested-bullet-not-code',
        'ordered-continuation-not-code',
    ],
)
def test_mask_indented_code(text: str, expected: str) -> None:
    """``mask_indented_code`` blanks indented blocks, sparing list bodies.

    A block needs a preceding blank line and no open list marker: a
    bullet indents its continuation and its nested items four spaces
    too, so indentation alone would blank real prose.
    """
    assert mask_indented_code(text) == expected


@pytest.mark.parametrize(
    argnames=('text', 'expected'),
    argvalues=[
        ('# Title\n\nbody', (0, 'Title')),
        ('intro\n\n# Title', (2, 'Title')),
        ('```\n# sample\n```\n# Title', (3, 'Title')),
        ('```\ncode\n````\n# Title', (3, 'Title')),
        ('````\n```\n# Fenced', None),
        ('```\ncode\n    ```\n# Title', None),
        ('   # Title\nbody', (0, 'Title')),
        ('# `code`', (0, '`code`')),
        ('## deeper heading only', None),
        ('```\n# fenced\n```', None),
    ],
    ids=[
        'first-line',
        'after-prose',
        'skips-fenced',
        'skips-longer-fence',
        'shorter-run-no-close',
        'overindented-close-stays-fenced',
        'indented-heading',
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
