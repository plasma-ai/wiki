"""Test the ``wiki.util.glob`` module."""

from __future__ import annotations

import re

import pytest

from wiki.util.glob import translate

__all__ = ['test_translate_matching']


@pytest.mark.parametrize(
    argnames=('pattern', 'candidate', 'matches'),
    argvalues=[
        # a bare name floats to any depth; matching is full-match only
        ('*.tmp', 'x.tmp', True),
        ('*.tmp', 'a/b/x.tmp', True),
        ('*.tmp', 'a/x.tmpz', False),
        ('foo*', 'foo/bar', False),
        # a leading '/' anchors at the root; '/' in the body anchors too
        ('/scratch.md', 'scratch.md', True),
        ('/scratch.md', 'a/scratch.md', False),
        ('a/b', 'a/b', True),
        ('a/b', 'x/a/b', False),
        # one trailing '/' strips; the remainder floats like a bare name
        ('vendor/', 'vendor', True),
        ('vendor/', 'a/vendor', True),
        # whole-segment '**' forms
        ('**/build', 'build', True),
        ('**/build', 'a/b/build', True),
        ('a/**/b', 'a/b', True),
        ('a/**/b', 'a/x/y/b', True),
        ('vendor/**', 'vendor/x', True),
        ('vendor/**', 'vendor', False),
        # segment wildcards never cross '/'; embedded '**' collapses to '*'
        ('temp?', 'temp1', True),
        ('temp?', 'temp22', False),
        ('a**b', 'axxb', True),
        ('a**b', 'a/b', False),
        # fnmatch-style classes; an unmatched '[' is a literal '['
        ('[ab]*', 'alpha', True),
        ('[!a]*', 'alpha', False),
        ('[x', '[x', True),
        # a class never matches '/'; a reversed range matches nothing
        ('a[!b]c', 'axc', True),
        ('a[!b]c', 'a/c', False),
        ('a[.-0]c', 'a.c', True),
        ('a[.-0]c', 'a/c', False),
        ('[z-a]', 'q', False),
        ('[!z-a]', 'q', True),
        ('[a&&b]', '&', True),
        # matching is case-sensitive
        ('Vendor', 'vendor', False),
    ],
    ids=[
        'floating-root',
        'floating-nested',
        'floating-full-match',
        'star-never-crosses-slash',
        'anchored-root-hit',
        'anchored-root-miss',
        'anchored-nested-hit',
        'anchored-nested-miss',
        'trailing-slash-root',
        'trailing-slash-floats',
        'leading-doublestar-root',
        'leading-doublestar-deep',
        'middle-doublestar-empty',
        'middle-doublestar-deep',
        'trailing-doublestar-inside',
        'trailing-doublestar-not-self',
        'question-hit',
        'question-miss',
        'embedded-doublestar-collapses',
        'embedded-doublestar-no-slash',
        'class',
        'negated-class',
        'unmatched-bracket-literal',
        'negated-class-hit',
        'negated-class-no-slash',
        'straddling-range-hit',
        'straddling-range-no-slash',
        'reversed-range-never-matches',
        'negated-reversed-range-matches-any',
        'set-operations-literal',
        'case-sensitive',
    ],
)
def test_translate_matching(pattern: str, candidate: str, matches: bool) -> None:
    """The compiled translation matches exactly the documented glob shapes.

    ``translate`` returns anchored regex source; behavior is asserted by
    compiling and probing root-relative candidate paths, so the table
    reads as the matching contract rather than pinning regex bytes (the
    doctests pin those).
    """
    assert bool(re.match(translate(pattern), candidate)) is matches
