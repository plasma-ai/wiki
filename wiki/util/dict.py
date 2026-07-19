"""Functions for dictionaries."""

from __future__ import annotations

__all__ = []


def merge(data: dict, other: dict, /, *, inplace: bool = False) -> dict:
    """Recursively merge ``other`` into ``data``.

    Dict values are merged recursively. All other types
    are replaced by the override value.

    Returns a new dict by default. Set ``inplace=True``
    to modify ``data`` directly.

    >>> merge({'a': {'x': 1, 'y': 2}, 'b': 3}, {'a': {'y': 9, 'z': 10}, 'c': 4})
    {'a': {'x': 1, 'y': 9, 'z': 10}, 'b': 3, 'c': 4}
    """
    if not inplace:
        data = data.copy()
    for key, value in other.items():
        if (key in data) and isinstance(data[key], dict) and isinstance(value, dict):
            data[key] = merge(data[key], value, inplace=inplace)
        else:
            data[key] = value
    return data
