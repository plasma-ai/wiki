"""Functions for the oracle-side judge.

The judge answers what a v1 side's interpreter makes of an input.

usage: <a v1 venv's python> -P judge.py < requests > answers

Reads one JSON request per line and prints one JSON answer per line. A
classifier of the registry asks it two things about an input of a command:
whether the interpreter's own library raises on it, and whether it lies
outside the set of inputs every engine supports (``SPEC.md``, Registry). The
answer comes from the interpreter the side runs, so it holds for that side's
``re``, ``json`` and ``zoneinfo`` and for the environment the side runs under.

- ``kind: regex`` with ``pattern`` and ``flags`` (letters of ``imsx``):
  ``error``, ``warnings`` and ``outside``.
- ``kind: format`` with ``format``, a ``strftime`` string: ``outside``.
- ``kind: zone`` with ``key``, an IANA name: ``outside``.
- ``kind: settings`` with ``data``, the base64 of a settings file's bytes:
  ``error``, ``values``, ``fields``, ``outside``, and ``pattern``, the
  ``regex`` answer for its ``naming.pattern``.

``error`` is the text of what the library raised, or null. ``warnings``
holds the text of each ``FutureWarning`` of ``re``. ``outside`` names each
reason the input is outside the supported set, and is empty for an input
inside it. ``fields`` names the fields of a settings file that are outside it.

Run with ``-P`` so the script's folder stays off ``sys.path``.
"""

from __future__ import annotations

import base64
import importlib
import json
import os
import re
import sys
import warnings
import zoneinfo
from typing import Any

__all__ = []

#: the ``strftime`` directives every engine supports
DIRECTIVES = ('%Y', '%m', '%d', '%H', '%M', '%S', '%z', '%%')
#: the nesting of a settings file at which it is outside the supported set
DEPTH = 128

# the parser of `re` and the node names of its parse tree
_constants: Any = importlib.import_module('re._constants')
_parser: Any = importlib.import_module('re._parser')
# the flag letters a request may carry, and the inline flags inside the set
_FLAGS = {
    'i': re.IGNORECASE,
    'm': re.MULTILINE,
    's': re.DOTALL,
    'x': re.VERBOSE,
}
# NOTE: a str pattern always carries the Unicode flag, so `(?u)` changes
#   nothing and the parse tree cannot show it
_INSIDE = re.IGNORECASE | re.MULTILINE | re.DOTALL | re.VERBOSE | re.UNICODE
# the parse-tree nodes inside the set that hold no pattern of their own
_PLAIN = (
    _constants.LITERAL,
    _constants.NOT_LITERAL,
    _constants.ANY,
    _constants.FAILURE,
)
_ANCHORS = (
    _constants.AT_BEGINNING,
    _constants.AT_BEGINNING_STRING,
    _constants.AT_BOUNDARY,
    _constants.AT_NON_BOUNDARY,
    _constants.AT_END,
    _constants.AT_END_STRING,
)
_CATEGORIES = (
    _constants.CATEGORY_DIGIT,
    _constants.CATEGORY_NOT_DIGIT,
    _constants.CATEGORY_SPACE,
    _constants.CATEGORY_NOT_SPACE,
    _constants.CATEGORY_WORD,
    _constants.CATEGORY_NOT_WORD,
)
# one JSON string, and one escape inside it
_STRING = re.compile(r'"(?:[^"\\]|\\.)*"', re.DOTALL)
_ESCAPE = re.compile(r'\\(?:u([0-9a-fA-F]{4})|.)', re.DOTALL)
# a constant only Python's reader takes for a number
_CONSTANT = re.compile(r'-?Infinity|NaN')


def regex(pattern: str, flags: str = '') -> dict[str, Any]:
    """Return what ``re`` makes of ``pattern`` under the flag letters ``flags``.

    ``error`` is the text ``re.compile`` raises with, ``warnings`` the text
    of each ``FutureWarning`` it gives, and ``outside`` the constructs of a
    pattern that compiles which lie outside the supported set.
    """
    bits = 0
    for letter in flags:
        bits |= _FLAGS[letter]
    error = None
    outside: list[str] = []
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        # purge the cache: a cached pattern compiles without its warnings
        re.purge()
        try:
            re.compile(pattern, bits)
        except (re.error, ValueError, RecursionError, OverflowError) as e:
            error = f'{e}'
        future = [
            f'{item.message}'
            for item in caught
            if issubclass(item.category, FutureWarning)
        ]
        if error is None:
            parsed = _parser.parse(pattern, bits)
            if parsed.state.flags & re.ASCII:
                outside.append('flag a')
            ignorecase = bool(parsed.state.flags & re.IGNORECASE)
            outside.extend(_constructs(parsed, ignorecase=ignorecase))
    return {
        'error': error,
        'warnings': future,
        'outside': sorted(set(outside)),
    }


def timestamp(format: str) -> dict[str, Any]:
    """Return the directives of a ``strftime`` format outside :data:`DIRECTIVES`."""
    outside = []
    index = format.find('%')
    while index != -1:
        directive = format[index : index + 2]
        if (directive not in DIRECTIVES) and (directive not in outside):
            outside.append(directive)
        index = format.find('%', index + 2)
    return {'outside': outside}


def zone(key: str) -> dict[str, Any]:
    """Return whether the zone ``key`` has no file on ``zoneinfo``'s search path.

    The interpreter can load such a zone from the ``tzdata`` package; an
    engine that reads zone files alone cannot.
    """
    for folder in zoneinfo.TZPATH:
        if os.path.isfile(os.path.join(folder, key)):
            return {'outside': []}
    return {'outside': ['no zone file on the search path']}


def settings(data: bytes) -> dict[str, Any]:
    """Return what the interpreter makes of the bytes of a settings file.

    ``error`` is the text ``json.loads`` raises with; bytes that are not
    UTF-8 never reach it. ``values`` names what the JSON text holds that
    only Python's reader takes. ``fields`` names those of the three fields
    an engine supports a fixed set of that lie outside it:
    ``naming.pattern``, ``timestamp.format`` and ``timestamp.timezone``.
    ``outside`` holds the values and each field's reasons. ``pattern`` is
    the :func:`regex` answer for ``naming.pattern``, or null without one.
    """
    result: dict[str, Any] = {
        'error': None,
        'values': [],
        'fields': [],
        'outside': [],
        'pattern': None,
    }
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        return result
    # translate newlines as the engine's read of the file does
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    try:
        value = json.loads(text)
    except (ValueError, RecursionError) as e:
        result['error'] = f'{e}'
        return result
    result['values'] = _values(text)
    naming = value.get('naming') if isinstance(value, dict) else None
    stamps = value.get('timestamp') if isinstance(value, dict) else None
    # gather each field's reasons
    reasons: dict[str, list[str]] = {}
    if isinstance(naming, dict) and isinstance(naming.get('pattern'), str):
        result['pattern'] = regex(naming['pattern'])
        reasons['naming.pattern'] = result['pattern']['outside']
    if isinstance(stamps, dict):
        if isinstance(stamps.get('format'), str):
            reasons['timestamp.format'] = timestamp(stamps['format'])['outside']
        if isinstance(stamps.get('timezone'), str) and stamps['timezone']:
            reasons['timestamp.timezone'] = zone(stamps['timezone'])['outside']
    result['fields'] = [field for field, found in reasons.items() if found]
    result['outside'] = [
        *result['values'],
        *(f'{field}: {reason}' for field, found in reasons.items() for reason in found),
    ]
    return result


def answer(request: dict[str, Any]) -> dict[str, Any]:
    """Return the answer to one request."""
    kind = request['kind']
    if kind == 'regex':
        return regex(request['pattern'], request.get('flags', ''))
    if kind == 'format':
        return timestamp(request['format'])
    if kind == 'zone':
        return zone(request['key'])
    if kind == 'settings':
        return settings(base64.b64decode(request['data']))
    raise ValueError(f'Invalid kind: {kind!r}')


def main() -> None:
    """Answer every request on stdin."""
    for line in sys.stdin:
        print(json.dumps(answer(json.loads(line))))


# ------ helper functions


def _constructs(parsed: Any, *, ignorecase: bool) -> list[str]:
    """Return the constructs of a parsed pattern outside the supported set.

    The set is literals and escapes, ``.``, character classes, the digit,
    word and space classes with their negations, the anchors, capturing,
    non-capturing and named groups, alternation, greedy and lazy
    quantifiers, lookahead, lookbehind and backreferences. A backreference
    that is compared without case is outside it.
    """
    result: list[str] = []
    for op, value in parsed:
        if op in _PLAIN:
            continue
        if op is _constants.IN:
            result.extend(_members(value))
        elif op is _constants.AT:
            if value not in _ANCHORS:
                result.append(f'{value}')
        elif op is _constants.BRANCH:
            for branch in value[1]:
                result.extend(_constructs(branch, ignorecase=ignorecase))
        elif op is _constants.SUBPATTERN:
            (_, added, removed, inner) = value
            if (added | removed) & ~_INSIDE:
                result.append('flag a')
            folded = (ignorecase or bool(added & re.IGNORECASE)) and not (
                removed & re.IGNORECASE
            )
            result.extend(_constructs(inner, ignorecase=folded))
        elif op in (_constants.MAX_REPEAT, _constants.MIN_REPEAT):
            result.extend(_constructs(value[2], ignorecase=ignorecase))
        elif op is _constants.POSSESSIVE_REPEAT:
            result.append('possessive quantifier')
            result.extend(_constructs(value[2], ignorecase=ignorecase))
        elif op is _constants.ATOMIC_GROUP:
            result.append('atomic group')
            result.extend(_constructs(value, ignorecase=ignorecase))
        elif op in (_constants.ASSERT, _constants.ASSERT_NOT):
            result.extend(_constructs(value[1], ignorecase=ignorecase))
        elif op is _constants.GROUPREF:
            if ignorecase:
                result.append('backreference under flag i')
        elif op is _constants.GROUPREF_EXISTS:
            result.append('conditional group')
            for branch in value[1:]:
                if branch is not None:
                    result.extend(_constructs(branch, ignorecase=ignorecase))
        else:
            result.append(f'{op}')
    return result


def _members(items: Any) -> list[str]:
    """Return the members of a character class outside the supported set."""
    result = []
    for op, value in items:
        if (op is _constants.CATEGORY) and (value not in _CATEGORIES):
            result.append(f'{value}')
        elif op not in (
            _constants.NEGATE,
            _constants.LITERAL,
            _constants.RANGE,
            _constants.CATEGORY,
        ):
            result.append(f'{op}')
    return result


def _values(text: str) -> list[str]:
    """Return the values of well-formed JSON text outside the supported set.

    The scan reads the text, not the loaded value: a later duplicate of a
    key hides the earlier value from the one, and an engine that refuses
    while it reads does not get that far.
    """
    result: list[str] = []
    # find a surrogate escape that is not one half of a pair
    for string in _STRING.findall(text):
        if _lone_surrogate(string):
            result.append('a lone surrogate escape')
            break
    bare = _STRING.sub('""', text)
    # list the constants a strict reader refuses
    for constant in sorted(set(_CONSTANT.findall(bare))):
        result.append(constant)
    # measure the deepest nesting of arrays and objects
    depth = 0
    deepest = 0
    for char in bare:
        if char in '[{':
            depth += 1
            deepest = max(deepest, depth)
        elif char in ']}':
            depth -= 1
    if deepest >= DEPTH:
        result.append(f'nesting of {DEPTH} levels or more')
    return result


def _lone_surrogate(string: str) -> bool:
    """Return whether a JSON string holds a surrogate escape outside a pair."""
    escapes = list(_ESCAPE.finditer(string))
    index = 0
    while index < len(escapes):
        escape = escapes[index]
        code = int(escape[1], 16) if escape[1] else -1
        index += 1
        if 0xDC00 <= code <= 0xDFFF:
            return True
        if not (0xD800 <= code <= 0xDBFF):
            continue
        # pair a high half with a low half directly behind it
        if (index == len(escapes)) or (escapes[index].start() != escape.end()):
            return True
        follower = escapes[index][1]
        if (not follower) or not (0xDC00 <= int(follower, 16) <= 0xDFFF):
            return True
        index += 1
    return False


if __name__ == '__main__':
    main()
