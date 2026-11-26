"""Print what a v1 side's interpreter makes of every code point.

usage: <a v1 venv's python> -P points.py

Prints one line of JSON for each run of code points that share their
properties: the first code point, the last one, and the properties, as a
list in the order of :data:`PROPERTIES`. The runs cover every code point
from zero to :data:`LAST`, in order. A property is what the interpreter's
Unicode tables say of one code point, read through ``unicodedata``, the
methods of ``str`` and the classes of ``re``. A mapping that leaves the code
point as it is, such as the lower case of a digit, is printed as ``null``, so
the code points of a block the tables hold nothing for share one run.

The harness holds the answers of two sides against each other: a code point
whose properties differ is one the two interpreters may treat differently
(``SPEC.md``, Campaign Run).

Run with ``-P`` so the script's folder stays off ``sys.path``.
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from typing import Any

__all__ = [
    'LAST',
    'PROPERTIES',
    'main',
]

#: the last code point
LAST = sys.maxunicode
# NOTE: `unicodedata.decomposition` is left out. The normal forms hold the
#   mapping it prints, and what it answers for a Hangul syllable turns on the
#   interpreter's version, not on its tables
#: the name of each property of a code point, in the order a run lists them
PROPERTIES = (
    'category',
    'bidirectional',
    'combining',
    'east_asian_width',
    'mirrored',
    'decimal',
    'digit',
    'numeric',
    'NFC',
    'NFD',
    'NFKC',
    'NFKD',
    'lower',
    'upper',
    'title',
    'casefold',
    'isalpha',
    'isalnum',
    'isdecimal',
    'isdigit',
    'isnumeric',
    'isspace',
    'islower',
    'isupper',
    'istitle',
    'isprintable',
    'identifier start',
    'identifier part',
    r'\w',
    r'\s',
    r'\d',
)

# the four normalization forms, as PROPERTIES names them
_FORMS = ('NFC', 'NFD', 'NFKC', 'NFKD')
# the classes of `re` a str pattern reads from the Unicode tables
_WORD = re.compile(r'\w')
_SPACE = re.compile(r'\s')
_DIGIT = re.compile(r'\d')


def main() -> None:
    """Print the properties of every code point, one line for each run."""
    write = sys.stdout.write
    first = 0
    held = _properties(chr(0))
    for point in range(1, LAST + 1):
        found = _properties(chr(point))
        if found != held:
            write(json.dumps([first, point - 1, held]) + '\n')
            first, held = point, found
    write(json.dumps([first, LAST, held]) + '\n')


# ------ helper functions


def _properties(char: str) -> list[Any]:
    """Return the properties of the code point ``char``, as PROPERTIES lists them."""
    mapped = [unicodedata.normalize(form, char) for form in _FORMS]
    mapped += [char.lower(), char.upper(), char.title(), char.casefold()]
    return [
        unicodedata.category(char),
        unicodedata.bidirectional(char),
        unicodedata.combining(char),
        unicodedata.east_asian_width(char),
        unicodedata.mirrored(char),
        unicodedata.decimal(char, None),
        unicodedata.digit(char, None),
        unicodedata.numeric(char, None),
        # a mapping that leaves the code point as it is says nothing of it
        *(None if text == char else text for text in mapped),
        char.isalpha(),
        char.isalnum(),
        char.isdecimal(),
        char.isdigit(),
        char.isnumeric(),
        char.isspace(),
        char.islower(),
        char.isupper(),
        char.istitle(),
        char.isprintable(),
        char.isidentifier(),
        f'a{char}'.isidentifier(),
        _WORD.fullmatch(char) is not None,
        _SPACE.fullmatch(char) is not None,
        _DIGIT.fullmatch(char) is not None,
    ]


if __name__ == '__main__':
    main()
