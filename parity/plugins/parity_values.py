"""The oracle side of the format calls: their JSON form, and the script that makes them again.

usage: <a v1 venv's python> -P parity_values.py MODULE CALLS

Reads one recorded call per line of the file ``CALLS`` and makes each call
again on the function of ``MODULE`` it names, in the interpreter that runs
the script. For each line it prints the call's outcome as one line of JSON:
``returned`` with the value, or ``raised`` with the exception's type and
text.

The recorder and this script share one JSON form for an argument and for an
outcome, so a call that is made twice has two equal lines exactly when the
function did the same thing twice. The module runs inside a v1 side's
interpreter, so it imports nothing from ``parity``.
"""

from __future__ import annotations

import importlib
import json
import sys
from collections.abc import Sequence
from typing import Any

__all__ = []

#: the key that marks a JSON object as a value JSON has no form for
TAG = '~'

# the tags: a tuple, a set and a mapping
_TUPLE = 'tuple'
_SET = 'set'
_MAP = 'map'


def plain(value: Any) -> Any:
    """Return ``value`` as JSON data that :func:`rebuild` reads back.

    A string, a number, a boolean, ``None`` and a list keep their JSON
    form. A tuple, a set and a mapping are objects marked with
    :data:`TAG`; a set lists its members in the order of their JSON text,
    so two equal sets have one form.

    Raises:
        TypeError: If ``value`` holds anything else.

    """
    if (value is None) or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return f'{value}'
    if isinstance(value, list):
        return [plain(item) for item in value]
    if isinstance(value, tuple):
        return {TAG: _TUPLE, 'items': [plain(item) for item in value]}
    if isinstance(value, (set, frozenset)):
        items = sorted((plain(item) for item in value), key=_text)
        return {TAG: _SET, 'items': items}
    if isinstance(value, dict):
        items = [[plain(key), plain(item)] for key, item in value.items()]
        return {TAG: _MAP, 'items': items}
    raise TypeError(f'No JSON form for a {type(value).__name__}.')


def rebuild(data: Any) -> Any:
    """Return the value that :func:`plain` wrote as ``data``."""
    if isinstance(data, list):
        return [rebuild(item) for item in data]
    if not isinstance(data, dict):
        return data
    items = [rebuild(item) for item in data['items']]
    if data[TAG] == _TUPLE:
        return tuple(items)
    if data[TAG] == _SET:
        return set(items)
    return dict(items)


def returned(value: Any) -> dict[str, Any]:
    """Return what a call that returned ``value`` did, as JSON data.

    Returns:
        ``{'returned': <value>}``, or ``{'unformed': <text>}`` for a value
        with no JSON form.

    """
    try:
        return {'returned': plain(value)}
    except TypeError as e:
        return {'unformed': f'{e}'}


def raised(error: Exception) -> dict[str, Any]:
    """Return what a call that raised ``error`` did, as JSON data."""
    return {'raised': type(error).__name__, 'text': f'{error}'}


def main(argv: Sequence[str]) -> int:
    """Make each recorded call again and print its outcome."""
    module = importlib.import_module(argv[0])
    with open(argv[1], encoding='utf-8') as handle:
        for line in handle:
            call = json.loads(line)
            function = getattr(module, call['function'])
            args = rebuild(call['args'])
            kwargs = rebuild(call['kwargs'])
            try:
                done = returned(function(*args, **kwargs))
            except Exception as e:
                done = raised(e)
            print(_text(done))
    return 0


# ------ helper functions


def _text(data: Any) -> str:
    """Return JSON data as the one text two equal values share."""
    return json.dumps(data, sort_keys=True)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
