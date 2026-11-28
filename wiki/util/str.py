"""Functions for strings."""

from __future__ import annotations

__all__ = []


def format_words(count: int, /) -> str:
    """Format a word count with ``k``/``m``/``b``/``t`` suffix.

    >>> format_words(950)
    '950'
    >>> format_words(15_200)
    '15.2k'
    >>> format_words(999_950)
    '1.0m'
    """
    tiers = {
        'k': 1_000,
        'm': 1_000_000,
        'b': 1_000_000_000,
        't': 1_000_000_000_000,
    }
    # walk high -> low so the largest applicable suffix wins
    ordered = list(reversed(tiers.items()))
    for i, (suffix, threshold) in enumerate(ordered):
        if count >= threshold:
            scaled = count / threshold
            # promote into the next tier when rounding would overflow this one
            if round(scaled, 1) >= 1_000:
                if i > 0:
                    upper_suffix, upper_threshold = ordered[i - 1]
                    return f'{count / upper_threshold:.1f}{upper_suffix}'
                # top tier has nowhere to promote: clamp below the roll-over
                return f'{(count * 10 // threshold) / 10:.1f}{suffix}'
            return f'{scaled:.1f}{suffix}'
    return str(count)


def split_lines(text: str, /) -> list[str]:
    r"""Split ``text`` into its lines on ``\n`` alone.

    A final ``\n`` ends the last line without opening another, and no
    other character breaks a line: ``str.splitlines`` also breaks on a
    form feed and on the Unicode separators, which a page keeps inside
    its lines.

    >>> split_lines('a\nb\n')
    ['a', 'b']
    >>> split_lines('a\n\nb')
    ['a', '', 'b']
    >>> split_lines('a\x0cb\n')
    ['a\x0cb']
    >>> split_lines('')
    []
    """
    lines = text.split('\n')
    if not lines[-1]:
        lines.pop()
    return lines
