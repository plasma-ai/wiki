"""String utilities for ``wiki``."""

from __future__ import annotations

__all__ = []


def format_words(count: int) -> str:
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
