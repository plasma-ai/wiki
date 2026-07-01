"""Dict utilities for ``wiki``."""

from __future__ import annotations

__all__ = ['merge']


def merge(data: dict, other: dict) -> dict:
    """Recursively merge ``other`` into ``data``.

    Dict values are merged recursively; all other types are
    replaced by the override value.

    Args:
        data: Base dict to merge into.
        other: Override dict whose values take precedence.

    Returns:
        A new merged dict.

    >>> merge({'a': {'x': 1}}, {'a': {'y': 2}})
    {'a': {'x': 1, 'y': 2}}
    """
    data = data.copy()
    for key, value in other.items():
        if key in data and isinstance(data[key], dict) and isinstance(value, dict):
            data[key] = merge(data[key], value)
        else:
            data[key] = value
    return data
