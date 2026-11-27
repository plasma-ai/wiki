"""Functions for regular expressions."""

from __future__ import annotations

import re
import warnings

__all__ = []


def quiet_compile(pattern: str, flags: int = 0, /) -> re.Pattern[str]:
    """Extends ``re.compile`` to silence its warnings about set syntax.

    ``re.compile`` warns that ``[[`` and a doubled ``--``, ``&&``, ``~~``
    or ``||`` inside a character class may change meaning. The warning
    names the calling source line, which says nothing to whoever wrote
    the pattern, so a caller compiling one it was handed lets none reach
    stderr.

    >>> quiet_compile('[[a-z]+').pattern
    '[[a-z]+'
    """
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', FutureWarning)
        return re.compile(pattern, flags)
