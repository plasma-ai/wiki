"""Functions for the wiki on-disk format."""

from __future__ import annotations

import re
import textwrap
from typing import Optional

from wiki.typing import Link
from wiki.util.markdown import find_heading

__all__ = []

# index link row as [[target|label]] with an optional ': desc' tail
_LINK_ROW = re.compile(r'^\[\[(.+?)\|(.+?)\]\](?::\s*(.*))?$')

# region-directive marker grammar; pairing semantics live in parse_regions
_REGION_DIRECTIVE = re.compile(
    r'<!--\s+(start|end):\s+([a-z0-9]+(?:-[a-z0-9]+)*)'
    r'((?:\s+[a-z0-9]+(?:-[a-z0-9]+)*)*)\s+-->'
)


def extract_frontmatter(lines: list[str]) -> tuple[str, int]:
    """Extract YAML frontmatter from lines.

    Returns ``(frontmatter, line_number)`` where ``line_number``
    is the first line after the closing ``---``. Returns
    ``('', 0)`` if no frontmatter is found.

    The ``_index.md`` merge driver (``_assets/git/merge_index.sh``)
    mirrors this block detection in shell; keep the two in sync.
    """
    # require an opening '---' (tolerating a UTF-8 BOM, which common
    # Windows editors prepend and str.strip does not remove)
    if lines and lines[0].lstrip('\ufeff').strip() == '---':
        line_number = 1
        # only an unindented '---' closes the frontmatter (an indented one is
        # content in a block scalar), so match on rstrip rather than strip
        while line_number < len(lines) and lines[line_number].rstrip() != '---':
            line_number += 1
        # no closing '---' -> malformed/unclosed frontmatter; treat the file as
        # having none so the body is preserved as content rather than silently
        # consumed to EOF (which would let an update discard the whole body)
        if line_number >= len(lines):
            return '', 0
        line_number += 1
        return '\n'.join(lines[:line_number]), line_number
    return '', 0


def match_link_row(line: str, *, repair: bool = True) -> Optional[Link]:
    r"""Match one index link row, tolerating formatter escape damage.

    Tries the raw line first (a name may hold a real backslash); when
    ``repair`` is set and the line opens with the damage shape (``\[\[``
    or ``\[[``), retries with formatter escapes (``\[ \] \_``) undone,
    so an escaped link block repairs in place. A healthy desc
    continuation escapes inside its brackets (``[\[``) and never
    matches, so it is never promoted to a link. Returns
    ``(target, label, desc)`` or ``None``.
    """
    stripped = line.strip()
    match = _LINK_ROW.match(stripped)
    if match is None and repair and stripped.startswith('\\['):
        candidate = re.sub(r'\\([\[\]_])', r'\1', stripped)
        match = _LINK_ROW.match(candidate)
    if match is None:
        return None
    return match.group(1), match.group(2), match.group(3) or ''


def parse_index(text: str, *, delimiter: str) -> tuple[str, list[Link], str]:
    r"""Parse an ``_index.md`` file into components.

    Returns ``(frontmatter, links, user_content)``:

    - ``frontmatter``: raw frontmatter text including ``---`` delimiters
    - ``links``: list of ``(target, label, description)`` tuples
    - ``user_content``: everything after the first delimiter, with any
      prose found above the first link folded in so it is never dropped

    Supports multi-line descriptions: continuation lines (not a link,
    not a delimiter, not blank) are appended to the previous link's
    description.

    A link row carrying the leading-escape formatter damage (``\[\[``
    or ``\[[``) parses as a link, so an escaped block above an intact
    delimiter repairs in place rather than demoting to user content.

    When the delimiter is missing, a leading link run is reclaimed
    (see :func:`reclaim_link_run`) and the rest of the body is the user
    content, so a formatter-mangled index repairs instead of
    duplicating its link block.

    The ``_index.md`` merge driver (``_assets/git/merge_index.sh``)
    mirrors the frontmatter/delimiter split in shell; keep the region
    rules in sync.

    Args:
        text: Raw file content.
        delimiter: The generated/user-content delimiter (``***``).

    """
    # alias lines
    lines = text.split('\n')
    # extract frontmatter
    frontmatter, line_number = extract_frontmatter(lines)
    # find the first delimiter after the frontmatter
    marker = None
    for i in range(line_number, len(lines)):
        if lines[i].rstrip() == delimiter:
            marker = i
            break
    # no delimiter: reclaim a demoted link run (a mangled marker), then
    # fold the rest into user content rather than risk dropping prose
    if marker is None:
        body = lines[line_number:]
        while body and not body[0].strip():
            body.pop(0)
        if body and re.match(r'^#\s', body[0]):
            body.pop(0)
        while body and not body[0].strip():
            body.pop(0)
        links, body = reclaim_link_run(body)
        return frontmatter, links, '\n'.join(body)
    # extract user content (everything after the marker)
    user_content = '\n'.join(lines[marker + 1 :])
    # extract links (everything between frontmatter and the marker)
    end = marker
    links = []
    current_link = None
    # prose above the first link is neither a link nor a continuation;
    # capture it as preamble rather than dropping it (the H1 and surrounding
    # blanks drop out, regenerated on render)
    preamble = []
    # blank lines inside a description are held until we know whether a
    # continuation follows (a paragraph break, kept) or the next link /
    # delimiter does (the separator before the next entry, dropped)
    pending_blanks = 0
    for i in range(line_number, end):
        line = lines[i]
        # skip delimiters
        if line.strip() == delimiter:
            if current_link is not None:
                links.append(current_link)
                current_link = None
            pending_blanks = 0
            continue
        # try to match a new link (formatter escape damage tolerated)
        link = match_link_row(line)
        if link is not None:
            # flush previous link
            if current_link is not None:
                links.append(current_link)
            pending_blanks = 0
            current_link = link
        elif current_link is not None:
            # hold a blank line pending the next line's type
            if not line.strip():
                pending_blanks += 1
                continue
            # continuation line: restore held blanks (paragraph breaks)
            target, label, desc = current_link
            desc = desc + '\n' * (pending_blanks + 1) + line.rstrip()
            current_link = (target, label, desc)
            pending_blanks = 0
        else:
            # before the first link: hold for the preamble
            preamble.append(line)
    # flush last link
    if current_link is not None:
        links.append(current_link)
    # strip the regenerated H1 (wherever it sits -- lead prose can precede it)
    # and surrounding blanks, then fold surviving prose into user content;
    # the no-delimiter branch above preserves the body the same way
    for i, line in enumerate(preamble):
        if re.match(r'^#\s', line):
            # drop the H1 and an adjacent blank so removal leaves no gap
            del preamble[i]
            if i < len(preamble) and not preamble[i].strip():
                del preamble[i]
            elif i > 0 and not preamble[i - 1].strip():
                del preamble[i - 1]
            break
    while preamble and not preamble[0].strip():
        preamble.pop(0)
    while preamble and not preamble[-1].strip():
        preamble.pop()
    if preamble:
        kept = '\n'.join(preamble)
        user_content = f'{kept}\n\n{user_content}' if user_content else kept
    # return index sections
    return frontmatter, links, user_content


def reclaim_link_run(body: list[str]) -> tuple[list[Link], list[str]]:
    """Reclaim the leading link run from a markerless index body.

    A formatter that mangles the ``***`` delimiter (rewriting it to a
    ``---`` thematic break, or backslash-escaping the wikilinks) demotes
    the generated link block to user content; re-rendering would then
    emit a fresh block above the stale one, duplicating every link on
    each update. When ``body`` opens with lines that parse as links
    (formatter escapes tolerated), take that run -- plus the thematic
    break standing where the delimiter was -- as the link block and
    return the remainder as user content. A body that opens with prose
    reclaims nothing, so prose is never parsed into invented links.

    Args:
        body: Index body lines (frontmatter, H1, and surrounding
            blanks already stripped).

    Returns:
        Tuple of ``(links, remainder)`` where ``links`` are
        ``(target, label, description)`` tuples and ``remainder`` is
        the surviving user content lines.

    """
    # walk the head of the body, consuming link lines, their directly
    # attached continuations, and the blanks between entries
    links = []
    current_link = None
    consumed = 0
    pending_blanks = 0
    for i, line in enumerate(body):
        stripped = line.strip()
        # hold blanks until the next line decides whether the run goes on
        if not stripped:
            pending_blanks += 1
            continue
        # try to match a new link (formatter escape damage tolerated)
        link = match_link_row(line)
        if link is not None:
            # flush previous link
            if current_link is not None:
                links.append(current_link)
            current_link = link
            pending_blanks = 0
            consumed = i + 1
            continue
        # a thematic break after the run is the mangled delimiter: drop it
        if current_link is not None:
            if re.fullmatch(r'\*{3,}|-{3,}|_{3,}', stripped):
                consumed = i + 1
                break
        # a line directly under a link continues its description
        if current_link is not None and not pending_blanks:
            target, label, desc = current_link
            current_link = (target, label, f'{desc}\n{line.rstrip()}')
            consumed = i + 1
            continue
        # prose: the run (and the reclaim) ends here
        break
    # flush last link
    if current_link is not None:
        links.append(current_link)
    # drop the blanks held between the run and the surviving remainder
    remainder = body[consumed:]
    while remainder and not remainder[0].strip():
        remainder.pop(0)
    return links, remainder


def parse_page(text: str) -> tuple[str, str]:
    """Parse a page file into ``(frontmatter, content)``.

    Extracts YAML frontmatter delimited by ``---`` lines.
    If no frontmatter is present, returns ``('', text)``.

    Args:
        text: Raw file content.

    Returns:
        Tuple of ``(frontmatter, content)``. Frontmatter includes
        the ``---`` delimiters. Content is everything after the
        closing ``---``.

    """
    lines = text.split('\n')
    frontmatter, line_number = extract_frontmatter(lines)
    if frontmatter:
        content = '\n'.join(lines[line_number:])
        return frontmatter, content
    return '', text


def build_frontmatter(*, name: str, created: str, updated: str) -> str:
    """Build YAML frontmatter string.

    Args:
        name: Display name for the index.
        created: ISO 8601 timestamp.
        updated: ISO 8601 timestamp.

    Returns:
        Complete frontmatter block including ``---`` delimiters.

    """
    lines = [
        '---',
        f'name: {quote(name)}',
        'desc: ...',
        'category: null',
        'tags: []',
        'sources: []',
        f'created: {created}',
        f'updated: {updated}',
        '---',
    ]
    return '\n'.join(lines)


def repair_frontmatter(frontmatter: str, *, name: str, now: str) -> str:
    """Refresh ``name:`` and fill missing/blank desc/created/updated keys.

    The shared frontmatter surgery for index and page planning: callable
    ``name:`` replacement (backslash-digit safe), placeholder restore on
    blank keys, in-place stamps so duplicates are never appended, and
    insertions in schema order (desc after name; created before
    updated).

    Args:
        frontmatter: Closed frontmatter block including delimiters.
        name: Path-derived display name to refresh ``name:`` from.
        now: Timestamp for seeding missing or blank ``created:``/
            ``updated:`` fields (never to re-stamp a present value).

    """
    # update name from the path-derived name (add it if the field is
    # missing, so frontmatter with no name: does not stay un-named)
    if re.search(r'^name:', frontmatter, re.MULTILINE):
        # callable repl so a backslash-digit in the
        # name is not read as a group reference
        frontmatter = re.sub(
            r'^name:.*$',
            lambda _: f'name: {quote(name)}',
            frontmatter,
            count=1,
            flags=re.MULTILINE,
        )
    else:
        pos = frontmatter.rfind('---')
        frontmatter = frontmatter[:pos] + f'name: {quote(name)}\n---'
    # add desc field if missing (after name line); restore the
    # placeholder on a present-but-blank key
    if re.search(r'^desc:[^\S\n]*$', frontmatter, re.MULTILINE):
        frontmatter = re.sub(
            r'^desc:[^\S\n]*$',
            'desc: ...',
            frontmatter,
            count=1,
            flags=re.MULTILINE,
        )
    elif not re.search(r'^desc:', frontmatter, re.MULTILINE):
        frontmatter = re.sub(
            r'^(name:.*\n)',
            r'\1desc: ...\n',
            frontmatter,
            count=1,
            flags=re.MULTILINE,
        )
    # add created/updated if missing; stamp a present-but-blank key
    # in place so a duplicate is never appended
    if re.search(r'^created:[^\S\n]*$', frontmatter, re.MULTILINE):
        frontmatter = re.sub(
            r'^created:[^\S\n]*$',
            f'created: {now}',
            frontmatter,
            count=1,
            flags=re.MULTILINE,
        )
    elif not re.search(r'^created:', frontmatter, re.MULTILINE):
        match = re.search(r'^updated:', frontmatter, re.MULTILINE)
        pos = match.start() if match else frontmatter.rfind('---')
        frontmatter = frontmatter[:pos] + f'created: {now}\n' + frontmatter[pos:]
    if re.search(r'^updated:[^\S\n]*$', frontmatter, re.MULTILINE):
        frontmatter = re.sub(
            r'^updated:[^\S\n]*$',
            f'updated: {now}',
            frontmatter,
            count=1,
            flags=re.MULTILINE,
        )
    elif not re.search(r'^updated:', frontmatter, re.MULTILINE):
        pos = frontmatter.rfind('---')
        frontmatter = frontmatter[:pos] + f'updated: {now}\n' + frontmatter[pos:]
    return frontmatter


def replace_heading(content: str, name: str) -> str:
    """Rewrite the H1 heading line (fence-aware) to ``# {name}``.

    Rewrites the exact heading line, not a ``# ...`` that may appear
    inside a fenced code block (see :func:`wiki.util.markdown.find_heading`).
    Content with no top-level heading is returned unchanged.
    """
    heading = find_heading(content)
    if heading:
        heading_index, _ = heading
        content_lines = content.split('\n')
        content_lines[heading_index] = f'# {name}'
        content = '\n'.join(content_lines)
    return content


def read_frontmatter_field(frontmatter: str, key: str) -> Optional[str]:
    """Read a scalar frontmatter ``key``, resolving block scalars.

    A plain ``key: value`` returns the stripped value, with one pair of
    matching surrounding YAML quotes stripped (a desc containing ``: ``
    must be quoted to stay valid YAML). A block scalar (``|``/``>`` with
    optional chomping/indentation indicators, e.g. ``|-``, ``>+``, ``|2``)
    resolves to its body: a literal ``|`` keeps line breaks, a folded
    ``>`` joins consecutive non-empty lines with a single space (a blank
    line is a paragraph break). Inline text on the indicator line
    (``key: > one liner.``) is taken as the value when no indented body
    follows. Returns ``None`` if the field is absent; an empty block body
    resolves to an empty string.
    """
    # single-line value
    match = re.search(rf'^{key}:[^\S\n]*(.+)$', frontmatter, re.MULTILINE)
    if match:
        value = match.group(1).strip()
        if not value.startswith(('|', '>')):
            return unquote(value)
    else:
        return None
    # block scalar: tolerate any header (chomping/indentation indicators
    # |- |+ >- |2 ...) plus trailing inline text, then capture the indented
    # body (blank lines inside the block are kept so a folded break survives)
    match = re.search(
        rf'^{key}:[^\S\n]*([|>])[-+0-9]*[^\S\n]*(.*)\n((?:[ \t]+.*\n|[ \t]*\n)*)',
        frontmatter,
        re.MULTILINE,
    )
    if not match:
        return None
    indicator, inline, body = match.group(1), match.group(2), match.group(3)
    # no indented body: the inline text on the header line is the value
    if not body:
        return inline.strip()
    body = textwrap.dedent(body)
    # folded scalar (>): join non-empty lines with a space, blank line breaks
    if indicator == '>':
        return fold_lines(body)
    return body.strip()


def read_frontmatter_name(frontmatter: str) -> Optional[str]:
    """Read the ``name`` field from frontmatter text.

    Handles multi-line YAML values (block scalars ``|``, ``>``) the
    same way :func:`read_frontmatter_desc` does, so a block-scalar
    name resolves to its body text rather than the ``|``/``>``
    indicator. Returns ``None`` if no name field is found.
    """
    return read_frontmatter_field(frontmatter, 'name')


def read_frontmatter_desc(frontmatter: str) -> Optional[str]:
    """Read the ``desc`` field from frontmatter text.

    Handles multi-line YAML values (block scalars ``|``, ``>``, with
    chomping/indentation indicators). Returns ``None`` if no desc
    field is found; an empty block body resolves to an empty string.
    """
    return read_frontmatter_field(frontmatter, 'desc')


def read_frontmatter_category(frontmatter: str) -> str:
    """Read the ``category`` field from frontmatter text.

    Returns an empty string if the field is absent, empty, or ``null``.
    """
    value = read_frontmatter_field(frontmatter, 'category')
    if value is None or value == 'null':
        return ''
    return value


def field_value(line: str) -> str:
    """Extract one frontmatter line's value for per-line matching.

    Strips a ``key:`` prefix and surrounding YAML quotes
    (:func:`unquote`), else returns the stripped line -- search's
    per-line field-mode extraction, kept beside the quoting rules it
    inverts. Unlike :func:`read_frontmatter_field`, which resolves the
    joined value of a whole field, this reads a single line so matches
    keep their line numbers.
    """
    match = re.match(r'^(\w+):[^\S\n]*', line)
    if match:
        return unquote(line[match.end() :])
    return line.strip()


def body_words(text: str) -> int:
    """Count the body words of a page or index text.

    Counts the body -- everything below the frontmatter, which is the only
    special region -- so the count matches the searchable/sliceable region
    exactly. The H1 heading and an index's auto-generated link block are body
    content, so they are counted (they are part of what ``read`` returns).
    """
    _, body = parse_page(text)
    return len(body.split())


def field_line_ranges(
    frontmatter: str,
    lines: list[str],
    fields: list[str],
) -> set[int]:
    r"""Return 1-based line numbers belonging to named frontmatter fields.

    Walks the frontmatter region of ``lines`` and collects line
    numbers for each field key line and its continuation lines
    (multi-line block scalars).

    Args:
        frontmatter: Parsed frontmatter string (including delimiters).
        lines: Full file lines (from ``text.split('\n')``).
        fields: Field names to match.

    """
    # initialize result
    result = set()
    frontmatter_end = len(frontmatter.split('\n'))
    current_field = None
    for lineno, line in enumerate(lines, 1):
        if lineno >= frontmatter_end:
            break
        # check for field key
        match = re.match(r'^(\w+):', line)
        if match:
            current_field = match.group(1)
            if current_field in fields:
                result.add(lineno)
            continue
        # continuation line of current field
        if current_field in fields:
            result.add(lineno)
    return result


def render_index(
    name: str,
    frontmatter: str,
    links: list[Link],
    user_content: str,
    *,
    delimiter: str,
) -> str:
    """Render a complete ``_index.md`` file.

    All links are in a single section. One delimiter separates
    links from user content (always present).
    """
    # initialize index contents
    parts = [frontmatter, '', f'# {name}', '']
    # render links
    for target, label, desc in links:
        parts.append(format_link(target, label, desc))
        parts.append('')
    # delimiter + user content
    parts.append(delimiter)
    if user_content:
        parts.append(user_content)
    else:
        parts.append('')
    # join parts and return index
    return '\n'.join(parts)


def render_page(frontmatter: str, content: str) -> str:
    """Combine frontmatter and content into a page file.

    Inverse of :func:`parse_page`.

    Args:
        frontmatter: YAML frontmatter block including ``---`` delimiters.
        content: Page content after the frontmatter.

    Returns:
        Complete page text.

    """
    if content:
        return frontmatter + '\n' + content
    return frontmatter + '\n'


def format_link(target: str, label: str, description: str) -> str:
    """Format a single link line.

    Parent links (``..``) have no description.
    All other links include a description (at minimum ``...``).
    """
    if label == '..':
        return f'[[{target}|{label}]]'
    desc = description or '...'
    return f'[[{target}|{label}]]: {desc}'


def escape_desc(desc: str, *, delimiter: str) -> str:
    r"""Escape desc lines that would parse as index structure.

    A propagated multi-line desc renders its continuation lines at
    column 0 inside the link block, where a line equal to the ``***``
    delimiter would end the block early (every later link re-added as
    new on the next update, growing the index without bound) and a
    link-shaped line would parse as a phantom entry. A delimiter line
    gets a leading backslash; a link-shaped line gets the backslash
    inside its leading brackets (``[\[``) so the healthy escape never
    carries the ``\[[`` formatter-damage signature lint scans for.
    Markdown renders the text unchanged either way, and the parser
    reads both as ordinary continuations. The escape is stable, so
    re-propagation converges. The first line never needs it -- it sits
    on the link line itself.
    """
    first, *rest = desc.split('\n')
    lines = [first]
    for line in rest:
        stripped = line.strip()
        if stripped == delimiter:
            line = line.replace(stripped, f'\\{stripped}', 1)
        elif match_link_row(stripped, repair=False) is not None:
            line = line.replace(stripped, f'[\\{stripped[1:]}', 1)
        lines.append(line)
    return '\n'.join(lines)


def join_lines(text: str) -> str:
    """Join multi-line text into a single line."""
    return ' '.join(line.strip() for line in text.strip().split('\n'))


def fold_lines(text: str) -> str:
    """Fold a YAML folded-scalar body (``>``) into paragraphs.

    Consecutive non-empty lines join with a single space; a blank line is
    a paragraph break (preserved as a newline). Mirrors the YAML
    folded-scalar rule.
    """
    # group consecutive non-empty lines into paragraphs
    paragraphs = []
    current = []
    for line in text.split('\n'):
        if line.strip():
            current.append(line.strip())
        elif current:
            paragraphs.append(' '.join(current))
            current = []
    if current:
        paragraphs.append(' '.join(current))
    return '\n'.join(paragraphs)


def quote(value: str) -> str:
    """YAML-quote a scalar when writing it plain would break the mapping.

    A value containing ``': '`` (or ending with ``:``) reads as a nested
    mapping in YAML, so it is written single-quoted with embedded single
    quotes doubled; any other value passes through unquoted. Inverse of
    :func:`unquote` for the values the wiki writes.
    """
    if ': ' in value or value.endswith(':'):
        escaped = value.replace("'", "''")
        return f"'{escaped}'"
    return value


def unquote(value: str) -> str:
    """Strip one pair of matching surrounding YAML quotes from a scalar.

    A quoted scalar (``"..."`` / ``'...'``) resolves to its body, with the
    YAML escapes undone -- doubled single quotes in a single-quoted value,
    backslash-escaped quotes/backslashes in a double-quoted one. An
    unquoted value is returned unchanged.
    """
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        body = value[1:-1]
        if value[0] == '"':
            return body.replace('\\"', '"').replace('\\\\', '\\')
        return body.replace("''", "'")
    return value


def parse_regions(masked: str) -> tuple[dict[str, list[tuple[int, int]]], list[str]]:
    """Parse region-directive comments into per-directive line ranges.

    One grammar covers all comment-bracketed regions:
    ``<!-- start: <directive> [args] -->`` ... ``<!-- end: <directive> -->``,
    each marker alone on its line, with bare kebab-word directives and
    args. Every directive pairs as an independent bracket stream, so
    regions of different directives interleave freely while
    same-directive nesting and dangling markers are structural errors.
    ``masked`` is pre-masked text (the caller masks once, via
    ``util.markdown.mask_code``, and shares the mask), so a fenced
    marker is a sample, not a directive. ``no-lint`` is the sole
    directive with shipped semantics; unknown well-formed pairs are
    inert.

    Returns:
        Tuple of ``(regions, errors)`` where ``regions`` maps each
        directive to its well-formed ``(start, end)`` line ranges
        (1-based, inclusive; a pair poisoned by a nested start is
        malformed and never recorded) and ``errors`` describe
        nesting/dangling violations, each naming its marker and line.

    """
    # collect marker events per directive from the masked text
    regions: dict[str, list[tuple[int, int]]] = {}
    errors = []
    open_starts: dict[str, Optional[int]] = {}
    poisoned: set[str] = set()
    for lineno, line in enumerate(masked.split('\n'), 1):
        match = _REGION_DIRECTIVE.fullmatch(line.strip())
        if not match:
            continue
        kind, directive = match.group(1), match.group(2)
        # a nested start poisons the open region (a malformed pair must
        # suppress nothing); an end without an open start dangles
        if kind == 'start':
            if open_starts.get(directive) is not None:
                errors.append(f"Nested '<!-- start: {directive} -->' (line {lineno})")
                poisoned.add(directive)
            else:
                open_starts[directive] = lineno
        elif open_starts.get(directive) is None:
            errors.append(f"Dangling '<!-- end: {directive} -->' (line {lineno})")
        else:
            if directive in poisoned:
                poisoned.discard(directive)
            else:
                regions.setdefault(directive, []).append(
                    (open_starts[directive], lineno)
                )
            open_starts[directive] = None
    # a start still open at EOF dangles
    for directive, start in open_starts.items():
        if start is not None:
            errors.append(f"Dangling '<!-- start: {directive} -->' (line {start})")
    return regions, errors


def no_lint_lines(masked: str) -> set[int]:
    """Return 1-based line numbers inside well-formed ``no-lint`` regions.

    ``masked`` is pre-masked text, per :func:`parse_regions`.
    """
    regions, _ = parse_regions(masked)
    result = set()
    for start, end in regions.get('no-lint', []):
        result.update(range(start, end + 1))
    return result


def escaped_wikilink_lines(masked: str) -> list[int]:
    r"""Return 1-based line numbers carrying formatter-escaped wikilinks.

    Markdown formatters backslash-escape ``[[...]]`` link brackets
    (``\[\[`` or ``\[[``); the sequence never appears in healthy
    generated content, so it is the signature lint uses to name likely
    formatter damage. ``masked`` is pre-masked text (per
    :func:`parse_regions`), so a sample documenting the escape never
    trips it.
    """
    result = []
    for lineno, line in enumerate(masked.split('\n'), 1):
        if re.search(r'\\\[\\?\[', line):
            result.append(lineno)
    return result
