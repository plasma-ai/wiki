"""Test the ``wiki.util.dict`` module."""

from __future__ import annotations

import pytest

from wiki.util.dict import merge

__all__ = [
    'test_merge',
    'test_merge_copies_by_default',
    'test_merge_inplace',
]


@pytest.mark.parametrize(
    argnames=('data', 'other', 'expected'),
    argvalues=[
        # dict values merge recursively, key by key
        (
            {'a': {'x': 1, 'y': 2}, 'b': 3},
            {'a': {'y': 9, 'z': 10}, 'c': 4},
            {'a': {'x': 1, 'y': 9, 'z': 10}, 'b': 3, 'c': 4},
        ),
        # non-dict values are replaced by the override outright
        (
            {'a': {'x': 1}, 'b': [1, 2]},
            {'a': 5, 'b': [3]},
            {'a': 5, 'b': [3]},
        ),
        # an empty override leaves data unchanged
        ({'a': 1}, {}, {'a': 1}),
    ],
    ids=['recursive', 'replace-non-dict', 'empty-override'],
)
def test_merge(data: dict, other: dict, expected: dict) -> None:
    """``merge`` merges dict values recursively and replaces all others."""
    assert merge(data, other) == expected


def test_merge_copies_by_default() -> None:
    """``merge`` returns a new dict, leaving ``data`` untouched."""
    data = {'a': {'x': 1}}
    result = merge(data, {'a': {'y': 2}})
    assert result == {'a': {'x': 1, 'y': 2}}
    assert data == {'a': {'x': 1}}


def test_merge_inplace() -> None:
    """``merge(inplace=True)`` mutates and returns ``data`` itself."""
    data = {'a': {'x': 1}}
    result = merge(data, {'a': {'y': 2}}, inplace=True)
    assert result is data
    assert data == {'a': {'x': 1, 'y': 2}}
