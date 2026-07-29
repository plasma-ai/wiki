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
            # the opening fence, indented at most three spaces
            close = re.match(r'^ {0,3}(`+|~+)[ \t]*$', line)
            if close and close.group(1).startswith(fence):
                fence = None
            continue
        # a backtick fence's info string may not contain a backtick
        match = re.match(r'^ {0,3}(`{3,}(?=[^`]*$)|~{3,})', line)
        if match:
            fence = match.group(1)
            lines.append('')
            continue
        lines.append(line)
    # blank inline code spans (equal-length backtick runs, newline-tolerant;
    # a span's interior newlines survive so line numbers stay aligned, and
    # interior backtick runs of a different length are span content)
    return re.sub(
        pattern=r'(?<!`)(`+)(?!`)(?:[^`\n]|\n(?![ \t]*\n)|(?!\1(?!`))`+(?!`))+?\1(?!`)',
        repl=lambda match: '\n' * match.group(0).count('\n'),
        string='\n'.join(lines),
    )


def mask_comments(text: str, /) -> str:
    """Blank HTML comment bodies, preserving line structure.

    A comment's interior newlines survive so positional checks stay
    aligned to source lines. Region-directive comments are blanked with
    the rest, so a caller that also parses regions must parse them first,
    off the :func:`mask_code` output.

    >>> mask_comments('keep <!-- hide [[x]] --> keep')
    'keep  keep'
    """
    return re.sub(
        pattern=r'<!--.*?-->',
        repl=lambda match: '\n' * match.group(0).count('\n'),
        string=text,
        flags=re.DOTALL,
    )


def mask_indented_code(text: str, /) -> str:
    r"""Blank four-space indented code blocks, preserving line structure.

    A block opens on a four-space-indented line that follows a blank line
    (or opens the text) and runs to the next non-blank line indented
    less. List context is exempt: a bullet indents its continuation and
    its nested items just as far, so masking on indentation alone would
    blank real prose -- an indented chunk is code only when no list
    marker is open above it.

    >>> mask_indented_code('para\n\n    code [[x]]\n')
    'para\n\n\n'
    >>> mask_indented_code('- item\n\n    still the list [[x]]\n')
    '- item\n\n    still the list [[x]]\n'
    """
    lines = []
    in_list = False
    in_code = False
    after_blank = True
    for line in text.split('\n'):
        if not line.strip():
            lines.append(line)
            after_blank = True
            continue
        # a tab indents like four spaces, so measure the expanded line
        expanded = line.expandtabs(4)
        indent = len(expanded) - len(expanded.lstrip(' '))
        # an unindented line closes any block and re-reads list context
        if indent < 4:
            in_code = False
            in_list = bool(re.match(r' {0,3}(?:[-*+]|\d{1,9}[.)])(?:\s|$)', expanded))
        elif after_blank and not in_list:
            in_code = True
        lines.append('' if in_code else line)
        after_blank = False
    return '\n'.join(lines)


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
            close = re.match(r'^ {0,3}(`+|~+)[ \t]*$', line)
            if close and close.group(1).startswith(fence):
                fence = None
            continue
        match = re.match(r'^ {0,3}(`{3,}(?=[^`]*$)|~{3,})', line)
        if match:
            fence = match.group(1)
            continue
        match = re.match(r'^ {0,3}# (.+)$', line)
        if match:
            return index, match.group(1)
    return None
