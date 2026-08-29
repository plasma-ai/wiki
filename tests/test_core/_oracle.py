"""Frontmatter grammar and strict-YAML oracle for the ``wiki.core.format`` tests.

The grammar enumerates one field under test in every scalar style the
on-disk format admits, over a set of body shapes and decorations, inside
a closed block with fixed neighbors; PyYAML's ``compose`` supplies the
value a strict reader sees (scalar text with folding, chomping, and
escapes applied, no type coercion) and ``safe_load`` the whole mapping.
The extras table adds the valid shapes the grammar's axes do not reach
-- comments on the key line, quoted and block openers under a bare key,
end-of-line comments, escaped line breaks, a space before the colon,
node properties -- which hand-derived reading rules miss first.
"""

from __future__ import annotations

from typing import Any, Optional

import yaml

__all__ = [
    'KEYS',
    'STYLES',
    'BODIES',
    'DECOS',
    'EXTRAS',
    'SEQUENCES',
    'NAME',
    'NOW',
    'block',
    'field_lines',
    'frontmatter',
    'grammar',
    'yaml_body',
    'oracle_scalar',
    'oracle_mapping',
    'oracle_valid',
    'oracle_extent',
    'normalize',
]

#: keys under test: the six schema keys, the optional pair, a custom key and
#: a dotted key outside the ``[\w-]+`` field grammar
KEYS = [
    'name',
    'title',
    'desc',
    'category',
    'tags',
    'sources',
    'created',
    'updated',
    'custom_key',
    'com.example',
]
#: scalar styles: one-line plain, plain continuing below the key line, plain
#: under a bare key, the two quoted forms, and every block-scalar header
STYLES = [
    'plain',
    'plain-inline-multi',
    'bare-key-multi',
    'single-quoted',
    'double-quoted',
    '|',
    '|-',
    '|+',
    '>',
    '>-',
    '>+',
    '|2',
]
#: body shapes a multi-line value can take
BODIES = [
    'one-line',
    'two-lines',
    'paragraph-break',
    'trailing-blanks',
    'more-indented',
    'ws-only-over-indented',
]
#: decorations around the field: comments, stray blanks, trailing spaces
DECOS = [
    'none',
    'comment-before',
    'comment-after-indented',
    'blank-before',
    'blank-after',
    'trailing-spaces',
]
#: valid shapes off the grammar's axes as ``(id, key, field lines)``
EXTRAS = [
    ('keyline-comment-body', 'desc', ['desc: # authored note', '  Alpha beta.']),
    ('keyline-comment-no-body', 'title', ['title: # TODO']),
    (
        'keyline-comment-block-header',
        'desc',
        ['desc: | # header note', '  Alpha beta.'],
    ),
    ('bare-key-single-quoted', 'desc', ['desc:', "  'A: colon here.'"]),
    ('bare-key-double-quoted', 'title', ['title:', '  "Draft: one"']),
    ('bare-key-literal-own-line', 'desc', ['desc:', '  |', '    Alpha beta.']),
    (
        'bare-key-folded-own-line',
        'desc',
        ['desc:', '  >-', '    Alpha beta.', '    Gamma delta.'],
    ),
    ('quoted-eol-comment', 'desc', ["desc: 'Alpha beta.'  # trailing comment"]),
    ('plain-eol-comment', 'desc', ['desc: Alpha beta.  # trailing comment']),
    ('null-eol-comment', 'title', ['title: null # why']),
    ('dq-escaped-linebreak', 'desc', ['desc: "Alpha \\', '  Gamma delta."']),
    (
        'dq-multiline-hash-line',
        'desc',
        ['desc: "Alpha beta.', '  # not a comment', '  Gamma delta."'],
    ),
    (
        'sq-doubled-quote-multiline',
        'desc',
        ["desc: 'Alpha beta.", "  ''quoted'' gamma.'"],
    ),
    (
        'plain-inline-multi-two-blanks',
        'desc',
        ['desc: Alpha beta.', '', '', '  Gamma delta.'],
    ),
    ('plain-inline-multi-hash-no-space', 'desc', ['desc: Alpha beta.', '  #tag line']),
    ('plain-hash-in-value', 'desc', ['desc: Use #1 approach.']),
    ('key-space-colon', 'desc', ['desc : Alpha beta.']),
    ('anchor-alias', 'desc', ['custom_key: &note Alpha beta.', 'desc: *note']),
    ('local-tag', 'desc', ['desc: !bang Alpha beta.']),
    ('bool-like', 'title', ['title: yes']),
    ('sexagesimal', 'title', ['title: 1:20']),
]
#: sequence-valued shapes, which the reader never resolves but whose line
#: extents must still scope right, as ``(id, key, field lines)``
SEQUENCES = [
    ('column0-url-items', 'sources', ['sources:', '- https://doi.org/x', '- b']),
    ('indented-items', 'tags', ['tags:', '  - a', '  - b']),
]
#: the repair's path-derived name and clock
NAME = 'fixed/page'
NOW = '2026-08-27T00:00:00Z'

# the C loader is a build-time option of the PyYAML wheel; values and styles
# are identical to the pure loader's
_LOADER = getattr(yaml, 'CSafeLoader', yaml.SafeLoader)
_QUOTES = {'single-quoted': "'", 'double-quoted': '"'}
_TEXT = ['Alpha beta.', 'Gamma delta.', 'Epsilon zeta.']
_STAMPS = ['2026-07-10T02:36:41Z', '2026-07-11T00:00:00Z', '2026-07-12T00:00:00Z']


def _body_lines(body: str, text: list[str]) -> list[str]:
    """Return the unindented value lines for a body shape."""
    first, second, third = text
    return {
        'one-line': [first],
        'two-lines': [first, second],
        'paragraph-break': [first, '', second],
        'trailing-blanks': [first, second, '', ''],
        'more-indented': [first, f'  {second}', third],
        'ws-only-over-indented': [first, '  ', second],
    }[body]


def _indent(line: str) -> str:
    """Indent a value line one level; a blank line stays blank."""
    return f'  {line}' if line else ''


def _field(key: str, style: str, body: str) -> Optional[list[str]]:
    """Render the field under test, or ``None`` for a pruned combination."""
    lines = _body_lines(body, _STAMPS if key in ('created', 'updated') else _TEXT)
    if style == 'plain':
        return [f'{key}: {lines[0]}'] if body == 'one-line' else None
    if style == 'plain-inline-multi':
        if body == 'one-line':
            return None
        return [f'{key}: {lines[0]}', *(_indent(line) for line in lines[1:])]
    if style == 'bare-key-multi':
        return [f'{key}:', *(_indent(line) for line in lines)]
    if style in _QUOTES:
        quote = _QUOTES[style]
        if body == 'one-line':
            return [f'{key}: {quote}{lines[0]}{quote}']
        out = [f'{key}: {quote}{lines[0]}', *(_indent(line) for line in lines[1:])]
        out[-1] = f'{out[-1]}{quote}' if out[-1] else f'  {quote}'
        return out
    return [f'{key}: {style}', *(_indent(line) for line in lines)]


def _decorate(lines: list[str], deco: str) -> list[str]:
    """Apply one decoration around the field lines."""
    return {
        'none': lines,
        'comment-before': ['# authored note', *lines],
        'comment-after-indented': [*lines, '  # trailing note'],
        'blank-before': ['', *lines],
        'blank-after': [*lines, ''],
        'trailing-spaces': [f'{line}  ' if line.strip() else line for line in lines],
    }[deco]


def field_lines(key: str, style: str, body: str, deco: str) -> list[str]:
    """Return the decorated lines of the field under test."""
    return _decorate(_field(key, style, body), deco)


def block(key: str, lines: list[str]) -> str:
    """Close ``lines`` -- the field under test -- inside the fixed neighbors.

    ``name``, ``desc``, ``created``, and ``updated`` surround the field so
    the repair's tool-owned edits are known in advance; the neighbor
    sharing ``key`` is omitted.
    """
    out = ['---']
    if key != 'name':
        out.append(f'name: {NAME}')
    if key != 'desc':
        out.append('desc: Fixed desc.')
    out.extend(lines)
    if key != 'created':
        out.append('created: 2026-01-01T00:00:00Z')
    if key != 'updated':
        out.append('updated: 2026-01-02T00:00:00Z')
    out.append('---')
    return '\n'.join(out)


def frontmatter(key: str, style: str, body: str, deco: str) -> str:
    """Build the closed block carrying ``key`` in the given shape."""
    return block(key, field_lines(key, style, body, deco))


def grammar() -> list[tuple[str, str, str]]:
    """Enumerate the pruned ``(style, body, deco)`` shapes."""
    return [
        (style, body, deco)
        for style in STYLES
        for body in BODIES
        if _field('desc', style, body) is not None
        for deco in DECOS
    ]


def yaml_body(text: str) -> str:
    """Return the YAML document between the fences, newline-terminated."""
    return '\n'.join(text.split('\n')[1:-1]) + '\n'


def oracle_scalar(body: str, key: str) -> Optional[str]:
    """Return the scalar text a strict reader gives ``key`` (first occurrence).

    ``compose`` keeps the raw scalar text -- folding, chomping, and escapes
    applied, but no type coercion -- so a timestamp compares as text.
    """
    node = yaml.compose(body, Loader=_LOADER)
    for key_node, value_node in node.value:
        if key_node.value == key and isinstance(value_node, yaml.ScalarNode):
            return value_node.value
    return None


def oracle_mapping(body: str) -> dict[str, Any]:
    """Return the whole mapping as a strict reader loads it."""
    return yaml.safe_load(body)


def oracle_valid(body: str) -> bool:
    """Return whether a strict reader accepts ``body`` as a mapping with unique keys.

    Lint's definition of valid frontmatter: the body composes, its root
    is a mapping, and no top-level key repeats (a strict reader refuses
    a duplicate or silently keeps one copy).
    """
    try:
        node = yaml.compose(body, Loader=_LOADER)
    except yaml.YAMLError:
        return False
    if not isinstance(node, yaml.MappingNode):
        return False
    keys = [key_node.value for key_node, _ in node.value]
    return len(keys) == len(set(keys))


def oracle_extent(body: str, key: str) -> set[int]:
    """Return the 1-based file lines a strict reader's key marks give ``key``.

    The top-level keys' start marks partition the body: every line from
    a key's line up to the line before the next key's belongs to that
    key -- comments, blanks, and sequence items included -- and the last
    key runs to the end of the body. Body line 0 is file line 2, after
    the opening fence.
    """
    node = yaml.compose(body, Loader=_LOADER)
    starts = sorted(key_node.start_mark.line for key_node, _ in node.value)
    last = len(body.rstrip('\n').split('\n'))
    for key_node, _ in node.value:
        if key_node.value != key:
            continue
        start = key_node.start_mark.line
        following = [line for line in starts if line > start]
        end = following[0] if following else last
        return set(range(start + 2, end + 2))
    return set()


def normalize(text: Optional[str]) -> str:
    """Reduce a value to the content the wiki's contract compares.

    A block scalar's final line breaks are chomping, not content, and an
    absent field (``None``) compares like an empty value; everything
    else -- interior line structure included -- must agree exactly.
    """
    return (text or '').rstrip('\n')
