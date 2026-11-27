"""Functions for JSON text."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterator
from typing import Any

__all__ = []

# the nesting strict_loads refuses, counted in arrays and objects open at once
_DEPTH_LIMIT = 128
# what the nesting count steps over: a string, closed or running to the end
# of the text, whose brackets open nothing, or one bracket
_NESTING_TOKEN = re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"?|[\[\]{}]', re.DOTALL)
# a surrogate code point, which no string of a JSON text holds unpaired
_SURROGATE = re.compile('[\ud800-\udfff]')


def strict_loads(text: str, /) -> Any:
    r"""Extends ``json.loads`` to refuse a text that is not strict JSON.

    ``json.loads`` reads more than the JSON grammar holds: the constants
    ``NaN``, ``Infinity`` and ``-Infinity``, a number past the range of a
    double (read as infinity, or as an integer no other reader keeps), a
    lone surrogate escape, an object that names a key twice (the last
    value wins), and nesting as deep as the interpreter's stack allows.
    Each is refused with a ``ValueError`` that words the cause, so an
    accepted text is one every strict reader accepts and reads alike.
    Nesting is counted on the text before it is parsed, so the verdict
    never depends on the interpreter's stack. A plain syntax error stays
    the ``json.JSONDecodeError`` (a ``ValueError``) ``json.loads`` raises.

    >>> strict_loads('{"a": [1, 2.5, "x"]}')
    {'a': [1, 2.5, 'x']}
    >>> strict_loads('{"a": NaN}')
    Traceback (most recent call last):
    ...
    ValueError: NaN is not a JSON number
    >>> strict_loads('{"a": 1e400}')
    Traceback (most recent call last):
    ...
    ValueError: number too large to represent
    >>> strict_loads('{"a": "\\ud800"}')
    Traceback (most recent call last):
    ...
    ValueError: lone surrogate '\ud800' in a string
    >>> strict_loads('{"a": {"b": 1, "b": 2}}')
    Traceback (most recent call last):
    ...
    ValueError: duplicate key 'b'
    >>> strict_loads('[' * 128 + ']' * 128)
    Traceback (most recent call last):
    ...
    ValueError: nested more than 127 levels deep
    """
    # count the nesting on the text
    depth = 0
    for match in _NESTING_TOKEN.finditer(text):
        token = match.group()
        if token in ('[', '{'):
            depth += 1
            if depth >= _DEPTH_LIMIT:
                raise ValueError(f'nested more than {_DEPTH_LIMIT - 1} levels deep')
        elif token in (']', '}'):
            depth -= 1
    # parse, refusing each constant, number and object as the parser meets it
    result = json.loads(
        text,
        parse_constant=_refuse_constant,
        parse_float=_parse_float,
        parse_int=_parse_int,
        object_pairs_hook=_parse_object,
    )
    # refuse a surrogate no escape paired
    for string in _strings(result):
        if surrogate := _SURROGATE.search(string):
            raise ValueError(f'lone surrogate {surrogate.group()!r} in a string')
    return result


def _refuse_constant(name: str, /) -> float:
    """Refuse ``NaN``, ``Infinity`` or ``-Infinity``, which JSON does not hold."""
    raise ValueError(f'{name} is not a JSON number')


def _parse_float(literal: str, /) -> float:
    """Parse a number literal, refusing one past the range of a double."""
    result = float(literal)
    if math.isinf(result):
        raise ValueError('number too large to represent')
    return result


def _parse_int(literal: str, /) -> int:
    """Parse an integer literal, refusing one past the range of a double."""
    # NOTE: the range check comes first -- past the interpreter's digit limit
    #   int() raises an error of its own, which depends on how it is configured
    _parse_float(literal)
    return int(literal)


def _parse_object(pairs: list[tuple[str, Any]], /) -> dict[str, Any]:
    """Build an object from its pairs, refusing a key named twice."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate key {key!r}')
        result[key] = value
    return result


def _strings(value: Any, /) -> Iterator[str]:
    """Yield every string of a parsed JSON value, keys included, in text order."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
