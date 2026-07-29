"""Functions for gitignore-style glob patterns."""

from __future__ import annotations

import re

__all__ = []


def translate(pattern: str, /) -> str:
    r"""Translate a gitignore-style glob into anchored regex source.

    The candidate is a root-relative POSIX path. One trailing ``/`` is
    stripped first (gitignore paste-friendliness); a leading ``/`` is
    stripped and anchors the pattern at the root; a remainder containing
    ``/`` is anchored, while a bare name floats -- it matches its single
    segment at any depth (so ``vendor/`` floats like ``vendor``, git's
    rule). A whole segment of ``**`` spans directories (leading
    ``**/x`` and middle ``a/**/b`` optionally, trailing ``a/**``
    matching everything strictly inside ``a``, never ``a`` itself);
    within any other segment ``*``, ``?``, and ``[...]`` never match
    ``/`` (a reversed range, ``[z-a]``, matches nothing -- git's rule),
    and an embedded ``**`` (``a**b``) collapses to ``*``. Translation is
    total -- any input yields a valid regex; validation is the caller's
    job.

    >>> translate('*.tmp')
    '\\A(?:.+/)?[^/]*\\.tmp\\Z'
    >>> translate('/scratch.md')
    '\\Ascratch\\.md\\Z'
    >>> translate('vendor/')
    '\\A(?:.+/)?vendor\\Z'
    >>> translate('a/b')
    '\\Aa/b\\Z'
    >>> translate('**/build')
    '\\A(?:.+/)?build\\Z'
    >>> translate('a/**/b')
    '\\Aa/(?:.+/)?b\\Z'
    >>> translate('vendor/**')
    '\\Avendor/.+\\Z'
    >>> translate('a**b')
    '\\A(?:.+/)?a[^/]*b\\Z'
    >>> translate('**/**/x') == translate('**/x')
    True
    >>> translate('file?.[ch]')
    '\\A(?:.+/)?file[^/]\\.[ch]\\Z'
    >>> translate('[!a]/[]x]')
    '\\A(?!/)[^a]/[]x]\\Z'
    >>> translate('[z-a]')
    '\\A(?:.+/)?(?!)\\Z'
    """
    # strip one trailing '/' (the file/dir distinction buys nothing
    # under the subtree rule, so a pasted gitignore line just works)
    if pattern.endswith('/'):
        pattern = pattern[:-1]
    # anchoring (git's rule): a leading '/' is stripped and anchors the
    # pattern at the root; otherwise a pattern containing '/' is
    # anchored and a bare name floats to any depth
    if pattern.startswith('/'):
        pattern = pattern[1:]
        floating = False
    else:
        floating = '/' not in pattern
    # walk the segments: a whole-segment '**' becomes an optional
    # directory run attached to what follows (or everything strictly
    # inside the prefix when trailing), so it never leaks into a
    # same-segment wildcard
    result = '(?:.+/)?' if floating else ''
    pending_sep = False
    segments = pattern.split('/')
    # collapse a run of '**' segments to one: consecutive directory runs
    # describe the same language, and leaving N of them adjacent makes the
    # regex backtrack exponentially on a deep non-matching path
    segments = [
        segment
        for index, segment in enumerate(segments)
        if segment != '**' or index == 0 or segments[index - 1] != '**'
    ]
    for index, segment in enumerate(segments):
        if pending_sep:
            result += '/'
            pending_sep = False
        if segment == '**':
            if index == len(segments) - 1:
                result += '.+'
            else:
                result += '(?:.+/)?'
        else:
            result += _translate_segment(segment)
            pending_sep = True
    return rf'\A{result}\Z'


def _translate_segment(segment: str) -> str:
    """Translate one path segment's fnmatch-style wildcards to regex source.

    ``*`` and ``?`` match within the segment (never ``/``), ``[...]`` is
    an fnmatch-style class that likewise never matches ``/`` (``!``
    first negates, ``]`` first is literal, an unmatched ``[`` is a
    literal ``[``, a reversed range matches nothing), an embedded ``**``
    collapses to ``*``, and everything else is escaped literally.
    Adapted from the stdlib `fnmatch
    <https://github.com/python/cpython/blob/main/Lib/fnmatch.py>`_
    translator.
    """
    # collapse an embedded '**' run to '*' ('**' only spans whole segments)
    segment = re.sub(r'\*{2,}', '*', segment)
    result = ''
    index = 0
    while index < len(segment):
        char = segment[index]
        if char == '*':
            result += '[^/]*'
            index += 1
        elif char == '?':
            result += '[^/]'
            index += 1
        elif char == '[':
            # scan for the closing ']', honoring the fnmatch quirks: a
            # leading '!' negates, a ']' directly after it is literal
            end = index + 1
            if (end < len(segment)) and (segment[end] == '!'):
                end += 1
            if (end < len(segment)) and (segment[end] == ']'):
                end += 1
            while (end < len(segment)) and (segment[end] != ']'):
                end += 1
            if end >= len(segment):
                # unmatched '[' is a literal '['
                result += r'\['
                index += 1
            else:
                stuff = segment[index + 1 : end]
                if '-' not in stuff:
                    stuff = stuff.replace('\\', r'\\')
                    spans_sep = False
                else:
                    # split the contents on range separators (a '-' first,
                    # or first after '!', is literal), then merge reversed
                    # ranges away -- invalid regex; git matches nothing
                    chunks = []
                    sep = index + 3 if segment[index + 1] == '!' else index + 2
                    start = index + 1
                    while True:
                        sep = segment.find('-', sep, end)
                        if sep < 0:
                            break
                        chunks.append(segment[start:sep])
                        start = sep + 1
                        sep = sep + 3
                    chunk = segment[start:end]
                    if chunk:
                        chunks.append(chunk)
                    else:
                        chunks[-1] += '-'
                    for k in range(len(chunks) - 1, 0, -1):
                        if chunks[k - 1][-1] > chunks[k][0]:
                            chunks[k - 1] = chunks[k - 1][:-1] + chunks[k][1:]
                            del chunks[k]
                    # a surviving range straddling '/' would cross segments
                    spans_sep = any(
                        chunks[k - 1][-1] < '/' < chunks[k][0]
                        for k in range(1, len(chunks))
                    )
                    # escape backslashes and hyphens (range-forming ones
                    # excepted) against regex set difference ('--')
                    stuff = '-'.join(
                        chunk.replace('\\', r'\\').replace('-', r'\-')
                        for chunk in chunks
                    )
                if not stuff:
                    # a reversed range merges to nothing: match nothing
                    result += '(?!)'
                elif stuff == '!':
                    # a negated nothing: any single non-separator character
                    result += '[^/]'
                else:
                    # escape reserved regex set operations ('&&', '~~', '||')
                    stuff = re.sub(r'([&~|])', r'\\\1', stuff)
                    if stuff.startswith('!'):
                        stuff = '^' + stuff[1:]
                    elif stuff.startswith(('^', '[')):
                        stuff = '\\' + stuff
                    # a class never matches '/' (git's rule): a negated set
                    # would otherwise admit it, as would a straddling range
                    if stuff.startswith('^') or spans_sep:
                        result += f'(?!/)[{stuff}]'
                    else:
                        result += f'[{stuff}]'
                index = end + 1
        else:
            result += re.escape(char)
            index += 1
    return result
