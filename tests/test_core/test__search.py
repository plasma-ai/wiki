"""Test the ``wiki.core._search`` module.

The ranked query, cache, and refresh behavior is covered end-to-end
by ``test_search``; only pure internals with a unit surface of their
own are pinned here.
"""

from __future__ import annotations

import pytest

from wiki.core import _search

__all__ = ['test_search_builds_deduped_match_expressions']


@pytest.mark.parametrize(
    argnames=('query', 'prefix', 'raw', 'expression'),
    argvalues=[
        # exact duplicates collapse to their first occurrence
        ('foo bar foo', False, False, '"foo" "bar"'),
        # the starred final term keeps its exact twin: 'foo*' alone is
        # wider than 'foo AND foo*'
        ('foo bar foo', True, False, '"foo" "bar" "foo"*'),
        ('foo foo', True, False, '"foo" "foo"*'),
        # without duplicates the final term stars in place
        ('foo bar', True, False, '"foo" "bar"*'),
        # raw queries pass through untouched
        ('foo foo', False, True, 'foo foo'),
    ],
)
def test_search_builds_deduped_match_expressions(
    query: str,
    prefix: bool,
    raw: bool,
    expression: str,
) -> None:
    """Safe queries dedupe exact duplicate terms; the prefix term stays exact."""
    built = _search._build_match_expression(query, prefix=prefix, tag=None, raw=raw)
    assert built == expression
