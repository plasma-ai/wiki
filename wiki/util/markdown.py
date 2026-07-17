"""Functions for markdown text."""

from __future__ import annotations

import re
from typing import Optional

__all__ = []


def mask_code(text: str, /) -> str:
    """Blank fenced code blocks and inline code spans in text.

    Fenced blocks (backtick or tilde fences) blank whole lines via a fence
    state machine; inline spans are removed per CommonMark's backtick-run
    rule -- a span opens with a run of backticks and closes at the next
    run of the same length, and may wrap across a newline but never a
    blank line. The line structure is preserved (masked regions become
    empty lines), so positional checks can attribute their findings to
    source lines. Lint checks scan the masked text so code samples never
    trip them.

    >>> mask_code('an `inline span` masked')
    'an  masked'
    """
    # blank fenced code blocks (line count preserved); the fence walk is
    # mirrored in find_heading, which must skip fences without the
    # inline-span masking below
    lines = []
    fence = None
    for line in text.split('\n'):
        if fence is not None:
            lines.append('')
            # CommonMark closes on a same-char run at least as long as
            # the opening fence
            close = line.strip()
            if set(close) == {fence[0]} and len(close) >= len(fence):
                fence = None
            continue
        match = re.match(r'^ {0,3}(`{3,}|~{3,})', line)
        if match:
            fence = match.group(1)
            lines.append('')
            continue
        lines.append(line)
    # blank inline code spans (equal-length backtick runs, newline-tolerant;
    # a span's interior newlines survive so line numbers stay aligned)
    return re.sub(
        r'(?<!`)(`+)(?!`)(?:[^`\n]|\n(?![ \t]*\n))+?\1(?!`)',
        lambda match: '\n' * match.group(0).count('\n'),
        '\n'.join(lines),
    )


def find_heading(text: str, /) -> Optional[tuple[int, str]]:
    r"""Find the first ``# heading`` outside fenced code blocks.

    Returns ``(line_index, title)`` for the heading, or
    ``None`` if there is no top-level heading. The line index
    lets callers rewrite the exact heading line rather than the
    first textual match (which could be inside a code block).

    >>> find_heading('intro `code`\n# Title')
    (1, 'Title')
    """
    # walk fences with mask_code's fence state machine, deliberately not
    # mask_code itself: its inline-span masking would blank a heading
    # whose text is a code span, changing H1 detection
    fence = None
    for index, line in enumerate(text.split('\n')):
        if fence is not None:
            close = line.strip()
            if set(close) == {fence[0]} and len(close) >= len(fence):
                fence = None
            continue
        match = re.match(r'^ {0,3}(`{3,}|~{3,})', line)
        if match:
            fence = match.group(1)
            continue
        match = re.match(r'^# (.+)$', line)
        if match:
            return index, match.group(1)
    return None
