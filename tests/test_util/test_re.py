"""Test the ``wiki.util.re`` module."""

from __future__ import annotations

import re
import warnings

import pytest

from wiki.util.re import quiet_compile

__all__ = ['test_quiet_compile_silences_set_syntax_warnings']


@pytest.mark.parametrize(
    argnames='pattern',
    argvalues=['[[a-z]+', '[+--]', '[a&&b]', '[a~~b]', '[a||b]'],
    ids=['nested-set', 'difference', 'intersection', 'symmetric-difference', 'union'],
)
def test_quiet_compile_silences_set_syntax_warnings(pattern: str) -> None:
    """A pattern Python warns about compiles to the same regex, unwarned.

    Each construct draws a ``FutureWarning`` from ``re.compile``; the
    helper compiles the pattern as written and lets none through, even
    where warnings are errors.
    """
    # a cached pattern would compile unwarned whoever asked
    re.purge()
    with pytest.warns(FutureWarning):
        expected = re.compile(pattern)
    re.purge()
    with warnings.catch_warnings():
        warnings.simplefilter('error')
        assert quiet_compile(pattern) == expected
