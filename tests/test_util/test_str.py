"""Test the ``wiki.util.str`` module."""

from __future__ import annotations

import pytest

from wiki.util.str import format_words

__all__ = ['test_format_words']


@pytest.mark.parametrize(
    ('count', 'expected'),
    [
        (0, '0'),
        (149, '149'),
        (999, '999'),
        (1000, '1.0k'),
        (1001, '1.0k'),
        (1234, '1.2k'),
        (15_200, '15.2k'),
        (999_499, '999.5k'),
        (999_950, '1.0m'),
        (1_000_000, '1.0m'),
        (1_500_000, '1.5m'),
        (2_300_000_000, '2.3b'),
        (1_000_000_000_000, '1.0t'),
        (4_700_000_000_000, '4.7t'),
        (999_999_999_999_999, '999.9t'),
    ],
    ids=[
        'zero',
        'small-exact',
        'sub-thousand',
        'thousand-exact',
        'just-over-thousand',
        'k-one-decimal',
        'k-two-digit',
        'k-near-million',
        'k-rounds-up-to-m',
        'million-exact',
        'million',
        'billion',
        'trillion-exact',
        'trillion',
        'top-tier-clamps-below-rollover',
    ],
)
def test_format_words(count: int, expected: str) -> None:
    """``format_words`` abbreviates with k/m/b/t, promoting on round-up.

    Exact tier thresholds take the suffix (``1000`` -> ``1.0k``) and the
    top tier, which has nowhere to promote, clamps below the roll-over
    instead of rendering a four-digit ``1000.0t``.
    """
    assert format_words(count) == expected
