"""Functions for the wiki on-disk format."""

from __future__ import annotations

import re
import textwrap
from typing import Optional

import wiki.util
from wiki.typing import Link

__all__ = []

# index link row as [[target|label]] with an optional ': desc' tail
_LINK_ROW = re.compile(r'^\[\[(.+?)\|(.+?)\]\](?::\s*(.*))?$')

# region-directive marker grammar; pairing semantics live in parse_regions
_REGION_DIRECTIVE = re.compile(
    r'<!--\s+(start|end):\s+([a-z0-9]+(?:-[a-z0-9]+)*)'
    r'((?:\s+[a-z0-9]+(?:-[a-z0-9]+)*)*)\s+-->'
)

# canonical frontmatter field order: the known head keys, then any
# unrecognized authored keys, then the tool-owned timestamp tail
_FRONTMATTER_HEAD = (
    'name',
    'title',
    'desc',
    'category',
    'tags',
    'sources',
)
_FRONTMATTER_TAIL = (
    'created',
    'updated',
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
    if lines and (lines[0].lstrip('\ufeff').strip() == '---'):
        line_number = 1
        # only an unindented '---' closes the frontmatter (an indented one is
        # content in a block scalar), so match on rstrip rather than strip
        while (line_number < len(lines)) and (lines[line_number].rstrip() != '---'):
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
    if (match is None) and repair and stripped.startswith('\\['):
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
      prose found above the first link folded in so it is never dropped;
      leading and trailing blank lines drop so render owns the canonical
      shape (one blank after the delimiter, one trailing newline)

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
        return frontmatter, links, '\n'.join(body).strip('\n')
    # extract user content (everything after the marker); leading and
    # trailing blank lines drop here so render owns the canonical shape
    # (one blank after the delimiter, one trailing newline)
    user_content = '\n'.join(lines[marker + 1 :]).strip('\n')
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
        # skip delimiters (rstrip, matching the marker rule: an indented
        # delimiter is a desc continuation, not structure)
        if line.rstrip() == delimiter:
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
    # the no-delimiter branch above drops only a leading H1 (one under lead
    # prose stays in its body)
    for i, line in enumerate(preamble):
        if re.match(r'^#\s', line):
            # drop the H1 and an adjacent blank so removal leaves no gap
            del preamble[i]
            if (i < len(preamble)) and not preamble[i].strip():
                del preamble[i]
            elif (i > 0) and not preamble[i - 1].strip():
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
        if (current_link is not None) and not pending_blanks:
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
        'tags: []',
        'sources: []',
        f'created: {created}',
        f'updated: {updated}',
        '---',
    ]
    return '\n'.join(lines)


def repair_frontmatter(
    frontmatter: str,
    *,
    name: str,
    now: str,
    title: bool = False,
    category: bool = False,
    order: bool = False,
) -> str:
    """Refresh ``name:`` and fill missing/blank desc/created/updated keys.

    The shared frontmatter surgery for index and page planning: callable
    ``name:`` replacement (backslash-digit safe), placeholder restore on
    blank keys, in-place stamps so duplicates are never appended,
    insertions in schema order (desc after name; created before
    updated), removal of an unset ``title:``/``category:`` (per their
    flags), and -- when ``order`` is set -- canonical field ordering
    (:func:`order_frontmatter`) with the final word.

    Args:
        frontmatter: Closed frontmatter block including delimiters.
        name: Path-derived display name to refresh ``name:`` from.
        now: Timestamp for seeding missing or blank ``created:``/
            ``updated:`` fields (never to re-stamp a present value).
        title: Remove a ``title:`` field carrying a blank or plain
            lowercase ``null`` value (absence is the canonical unset
            form; a quoted ``'null'`` is authored text). Never inserts
            the field.
        category: Remove an unset ``category:`` field the same way.
        order: Reorder every field into the canonical schema order
            after all other repairs.

    """
    # update name from the path-derived name (add it if the field is
    # missing, so frontmatter with no name: does not stay un-named)
    if re.search(r'^name:', frontmatter, re.MULTILINE):
        # callable repl so a backslash-digit in the
        # name is not read as a group reference
        frontmatter = re.sub(
            pattern=r'^name:.*$',
            repl=lambda _: f'name: {quote(name)}',
            string=frontmatter,
            count=1,
            flags=re.MULTILINE,
        )
    else:
        pos = frontmatter.rfind('---')
        frontmatter = frontmatter[:pos] + f'name: {quote(name)}\n---'
    # add desc field if missing (after name line); restore the placeholder on
    # a present-but-valueless key -- a bare key, a quoted-empty value, or an
    # empty block scalar (mirrors the title branch's valueless check below),
    # all of which read as no description; a real block-scalar body is kept
    desc_match = re.search(
        pattern=r'^desc:.*\n(?:[ \t]+.*\n|[ \t]*\n)*',
        string=frontmatter,
        flags=re.MULTILINE,
    )
    if desc_match:
        indicator, _, body = desc_match.group(0).partition('\n')
        value = indicator.split(':', 1)[1].strip()
        no_value = value in ('', "''", '""')
        bare_indicator = bool(re.fullmatch(r'[|>][-+0-9]*', value))
        if not body.strip() and (no_value or bare_indicator):
            frontmatter = (
                frontmatter[: desc_match.start()]
                + 'desc: ...\n'
                + frontmatter[desc_match.end() :]
            )
    elif not re.search(r'^desc:', frontmatter, re.MULTILINE):
        frontmatter = re.sub(
            pattern=r'^(name:.*\n)',
            repl=r'\1desc: ...\n',
            string=frontmatter,
            count=1,
            flags=re.MULTILINE,
        )
    # add created/updated if missing; stamp a present-but-blank key
    # in place so a duplicate is never appended
    if re.search(r'^created:[^\S\n]*$', frontmatter, re.MULTILINE):
        frontmatter = re.sub(
            pattern=r'^created:[^\S\n]*$',
            # a callable repl, so a backslash in a user timestamp.format is
            # emitted verbatim, not parsed as a group reference
            repl=lambda _: f'created: {now}',
            string=frontmatter,
            count=1,
            flags=re.MULTILINE,
        )
    elif not re.search(r'^created:', frontmatter, re.MULTILINE):
        match = re.search(r'^updated:', frontmatter, re.MULTILINE)
        pos = match.start() if match else frontmatter.rfind('---')
        frontmatter = frontmatter[:pos] + f'created: {now}\n' + frontmatter[pos:]
    if re.search(r'^updated:[^\S\n]*$', frontmatter, re.MULTILINE):
        frontmatter = re.sub(
            pattern=r'^updated:[^\S\n]*$',
            repl=lambda _: f'updated: {now}',
            string=frontmatter,
            count=1,
            flags=re.MULTILINE,
        )
    elif not re.search(r'^updated:', frontmatter, re.MULTILINE):
        pos = frontmatter.rfind('---')
        frontmatter = frontmatter[:pos] + f'updated: {now}\n' + frontmatter[pos:]
    # drop an unset category: absence is the canonical unset form, so a
    # provably valueless field -- a blank value, the plain lowercase null
    # spelling, or an empty block scalar -- removes its whole extent
    # (indicator line plus indented/blank body, so a block scalar goes as
    # one unit); a quoted or block-scalar 'null' is authored text, kept
    # verbatim, and the field is never inserted
    if category:
        match = re.search(
            pattern=r'^category:.*\n(?:[ \t]+.*\n|[ \t]*\n)*',
            string=frontmatter,
            flags=re.MULTILINE,
        )
        if match:
            indicator, _, body = match.group(0).partition('\n')
            value = indicator.split(':', 1)[1].strip()
            no_value = value in ('', 'null')
            bare_indicator = bool(re.fullmatch(r'[|>][-+0-9]*', value))
            if not body.strip() and (no_value or bare_indicator):
                frontmatter = frontmatter[: match.start()] + frontmatter[match.end() :]
    # drop an unset title the same way (the first occurrence wins,
    # matching every reader); an authored title's slot under name: is
    # the order pass's job
    if title:
        match = re.search(
            pattern=r'^title:.*\n(?:[ \t]+.*\n|[ \t]*\n)*',
            string=frontmatter,
            flags=re.MULTILINE,
        )
        if match:
            indicator, _, body = match.group(0).partition('\n')
            value = indicator.split(':', 1)[1].strip()
            no_value = value in ('', 'null')
            bare_indicator = bool(re.fullmatch(r'[|>][-+0-9]*', value))
            if not body.strip() and (no_value or bare_indicator):
                frontmatter = frontmatter[: match.start()] + frontmatter[match.end() :]
    # enforce the canonical field order LAST: the insertions above anchor
    # on schema neighbors and authored fields start anywhere, so the full
    # reorder must have the final word
    if order:
        frontmatter = order_frontmatter(frontmatter)
    return frontmatter


def order_frontmatter(frontmatter: str) -> str:
    """Reorder frontmatter fields into the canonical schema order.

    Fields land as ``name``, ``title``, ``desc``, ``category``,
    ``tags``, ``sources``, then any unrecognized authored keys in their
    original relative order, with the tool-owned ``created``/``updated``
    tail closing the block. Each field moves as its full extent --
    indicator line plus indented/blank body -- byte-verbatim, so a block
    scalar never strands its continuation lines. Duplicate keys stay
    adjacent in original order, so a first-occurrence read resolves to
    the same value after the move. Non-field lines above the first key
    stay above it.
    """
    lines = frontmatter.split('\n')
    # group the body (between the delimiters) into per-field extents: a
    # key line opens an extent, every other line continues the open one
    # (the field_line_ranges grammar), and lines before the first key
    # hold as a preamble
    extents: list[tuple[str, list[str]]] = []
    preamble = []
    current = None
    for line in lines[1:-1]:
        match = re.match(r'^([\w-]+):', line)
        if match:
            current = (match.group(1), [line])
            extents.append(current)
        elif current is not None:
            current[1].append(line)
        else:
            preamble.append(line)

    # stable sort by slot, so same-key extents and the unrecognized run
    # keep their relative order
    def field_slot(extent: tuple[str, list[str]]) -> int:
        key, _ = extent
        if key in _FRONTMATTER_HEAD:
            return _FRONTMATTER_HEAD.index(key)
        if key in _FRONTMATTER_TAIL:
            return len(_FRONTMATTER_HEAD) + 1 + _FRONTMATTER_TAIL.index(key)
        return len(_FRONTMATTER_HEAD)

    extents.sort(key=field_slot)
    # reassemble: the sort permutes whole extents, so every byte of the
    # block survives the move
    result = [lines[0], *preamble]
    for _, extent_lines in extents:
        result.extend(extent_lines)
    result.append(lines[-1])
    return '\n'.join(result)


def seed_frontmatter_title(frontmatter: str, title: Optional[str] = None) -> str:
    """Seed a ``title:`` line directly under ``name:`` when none exists.

    ``title`` is the value to seed -- adopting a bare page preserves its
    authored H1 this way -- and ``None`` seeds the ``title: null``
    placeholder required-titles mode demands. Frontmatter already
    carrying a ``title:`` line is returned unchanged: the field is
    authored, so a present line is never overwritten.
    """
    if re.search(r'^title:', frontmatter, re.MULTILINE):
        return frontmatter
    if title is None:
        value = 'null'
    else:
        # quote the reserved lowercase null spelling: an authored H1
        # reading "null" must read back as text, not the placeholder
        value = "'null'" if title == 'null' else quote(title)
    # callable repl so a backslash-digit in the title is not read as a group reference
    return re.sub(
        pattern=r'^(name:.*\n)',
        repl=lambda match: f'{match.group(1)}title: {value}\n',
        string=frontmatter,
        count=1,
        flags=re.MULTILINE,
    )


def replace_heading(content: str, name: str) -> str:
    """Rewrite the H1 heading line (fence-aware) to ``# {name}``.

    Rewrites the exact heading line, not a ``# ...`` that may appear
    inside a fenced code block (see :func:`wiki.util.markdown.find_heading`).
    Content with no top-level heading is returned unchanged.
    """
    heading = wiki.util.markdown.find_heading(content)
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
    follows. A bare ``key:`` over an indented body is a plain multi-line
    scalar and folds the same way ``>`` does. Returns ``None`` if the
    field is absent; an empty block body resolves to an empty string.
    """
    # single-line value
    match = re.search(rf'^{key}:[^\S\n]*(.+)$', frontmatter, re.MULTILINE)
    if match:
        value = match.group(1).strip()
        if not value.startswith(('|', '>')):
            return unquote(value)
    else:
        # bare key: an indented body is a plain multi-line scalar, folded
        # per the YAML plain-scalar rule; no body reads as an absent value
        match = re.search(
            pattern=rf'^{key}:[^\S\n]*\n((?:[ \t]+.*\n|[ \t]*\n)*)',
            string=frontmatter,
            flags=re.MULTILINE,
        )
        if match and match.group(1).strip():
            return fold_lines(match.group(1))
        return None
    # block scalar: tolerate any header (chomping/indentation indicators
    # |- |+ >- |2 ...) plus trailing inline text, then capture the indented
    # body (blank lines inside the block are kept so a folded break survives)
    match = re.search(
        pattern=rf'^{key}:[^\S\n]*([|>])[-+0-9]*[^\S\n]*(.*)\n((?:[ \t]+.*\n|[ \t]*\n)*)',
        string=frontmatter,
        flags=re.MULTILINE,
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
    """Return the ``name`` field from frontmatter text.

    Handles multi-line YAML values (block scalars ``|``, ``>``) the
    same way :func:`read_frontmatter_desc` does, so a block-scalar
    name resolves to its body text rather than the ``|``/``>``
    indicator. A multi-line value (block scalar, or a bare ``name:``
    over an indented body) is joined to a single line: repair writes
    the name back as a plain ``name:`` scalar and the H1 renders on
    one line, so a raw newline would land a stray unindented
    frontmatter line and a second H1 line that every parse folds
    into user content (authored frontmatter is user input; this is
    boundary validation). Returns ``None`` if no name field is found.
    """
    value = read_frontmatter_field(frontmatter, 'name')
    if value is None:
        return None
    return join_lines(value)


def read_frontmatter_title(frontmatter: str) -> str:
    """Return the ``title`` field from frontmatter text.

    Returns an empty string if the field is absent, blank, or the plain
    lowercase ``null`` spelling, so callers resolve a display heading as
    ``title or name``; a quoted or block-scalar ``null`` is authored
    text and reads back literally. A multi-line value (block scalar, or
    a bare ``title:`` over an indented body) is joined to a single line:
    the H1 renders on one line, and a raw newline would leak lines above
    the link block that every parse folds into user content -- unbounded
    growth (authored frontmatter is user input; this is boundary
    validation).
    """
    # the unset check reads the raw spelling: unquoting first would
    # collapse an authored 'null' into the reset idiom; a bare 'title:'
    # defers to the delegate, which resolves an indented body as a plain
    # multi-line scalar and no body as an absent value
    match = re.search(r'^title:[^\S\n]*(.*)$', frontmatter, re.MULTILINE)
    if (match is None) or (match.group(1).strip() == 'null'):
        return ''
    value = read_frontmatter_field(frontmatter, 'title')
    return join_lines(value or '')


def read_frontmatter_desc(frontmatter: str) -> Optional[str]:
    """Return the ``desc`` field from frontmatter text.

    Handles multi-line YAML values (block scalars ``|``, ``>``, with
    chomping/indentation indicators). Returns ``None`` if no desc
    field is found; an empty block body resolves to an empty string.
    """
    return read_frontmatter_field(frontmatter, 'desc')


def read_frontmatter_category(frontmatter: str) -> str:
    """Return the ``category`` field from frontmatter text.

    Returns an empty string if the field is absent, blank, or the plain
    lowercase ``null`` spelling (absence is the canonical unset form);
    a quoted or block-scalar ``null`` is authored text and reads back
    literally. A multi-line value (block scalar, or a bare ``category:``
    over an indented body) is joined to a single line: the category
    renders inside the parent's ``[category] name`` link label, where a
    raw newline would break the row on every parse (authored frontmatter
    is user input; this is boundary validation).
    """
    # the unset check reads the raw spelling: unquoting first would
    # collapse an authored 'null' into the reset idiom; a bare
    # 'category:' defers to the delegate, which resolves an indented
    # body as a plain multi-line scalar and no body as an absent value
    match = re.search(r'^category:[^\S\n]*(.*)$', frontmatter, re.MULTILINE)
    if (match is None) or (match.group(1).strip() == 'null'):
        return ''
    value = read_frontmatter_field(frontmatter, 'category')
    return join_lines(value or '')


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
        return unquote(line[match.end() :].strip())
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
        match = re.match(r'^([\w-]+):', line)
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
    heading: str,
    frontmatter: str,
    links: list[Link],
    user_content: str,
    *,
    delimiter: str,
) -> str:
    """Render a complete ``_index.md`` file.

    All links are in a single section. One delimiter separates
    links from user content (always present). ``heading`` becomes the
    H1: the authored title when one is set, else the path-derived name.
    """
    # initialize index contents
    parts = [frontmatter, '', f'# {heading}', '']
    # render links
    for target, label, desc in links:
        parts.append(format_link(target, label, desc))
        parts.append('')
    # delimiter + user content: a blank line after the delimiter when
    # content follows, and exactly one trailing newline either way
    parts.append(delimiter)
    if user_content:
        parts.append('')
        parts.append(user_content)
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
    # a desc opening on its own line -- the formatter wraps a link too long
    # to hold the desc after "]]:" -- keeps that break, so the round-trip with
    # the formatter converges instead of re-flowing the row on every run
    if desc.startswith('\n'):
        return f'[[{target}|{label}]]:{desc}'
    return f'[[{target}|{label}]]: {desc}'


def escape_desc(desc: str, *, delimiter: str) -> str:
    r"""Escape desc lines that would parse as index structure.

    A propagated multi-line desc renders its continuation lines at
    column 0 inside the link block, where a line equal to the ``***``
    delimiter would end the block early (every later link re-added as
    new on the next update, growing the index without bound) and a
    link-shaped line would parse as a phantom entry. A delimiter line
    gets a leading backslash; a link-shaped line gets the backslash
    inside its leading brackets (``[\[``), the healthy-escape shape
    :func:`escaped_wikilink_lines` exempts from its ``\[[``
    formatter-damage signature. Link detection uses the same ``repair``
    the reader (:func:`parse_index`) applies, so a line already carrying
    that damage shape is escaped here rather than surviving to be
    promoted to a real link on the next parse. Markdown renders the
    text unchanged either way, and the parser reads both as ordinary
    continuations. The escape is stable, so re-propagation converges.
    The first line never needs it -- it sits on the link line itself.
    """
    first, *rest = desc.split('\n')
    lines = [first]
    for line in rest:
        stripped = line.strip()
        if stripped == delimiter:
            line = line.replace(stripped, f'\\{stripped}', 1)
        elif match_link_row(stripped, repair=True) is not None:
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
    mapping in YAML, one opening with a block-scalar indicator (``|``/``>``)
    reads as a block scalar (:func:`read_frontmatter_field` diverts it there
    and eats the indicator), and one wrapped in matching quote chars would
    lose its quotes to :func:`unquote` on read, so each is written
    single-quoted with embedded single quotes doubled; any other value
    passes through unquoted. Inverse of :func:`unquote` for the values
    the wiki writes.
    """
    mapping_shaped = ': ' in value or value.endswith(':')
    block_shaped = value.startswith(('|', '>'))
    quote_wrapped = unquote(value) != value
    if mapping_shaped or block_shaped or quote_wrapped:
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
    if (len(value) >= 2) and (value[0] == value[-1]) and (value[0] in ('"', "'")):
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
    formatter damage. A ``[`` directly before the sequence is
    :func:`escape_desc`'s healthy desc escape of a damage-shaped
    continuation, so it is exempt. ``masked`` is pre-masked text (per
    :func:`parse_regions`), so a sample documenting the escape never
    trips it.
    """
    result = []
    for lineno, line in enumerate(masked.split('\n'), 1):
        if re.search(r'(?<!\[)\\\[\\?\[', line):
            result.append(lineno)
    return result


def hyphen_dangle_lines(masked: str) -> list[int]:
    r"""Return 1-based line numbers ending in a wrap-dangled hyphen.

    A line break landing inside a hyphenated word leaves its line
    ending ``<word>-`` with the compound's tail opening the next line;
    every folded read joins the pair with a space, so ``twenty-\nclass``
    reads back mangled as ``twenty- class``. A next line opening with
    ``and ``, ``or ``, or ``nor `` is the suspended-hyphen idiom
    (``twenty- and thirty-class`` wrapped at the break) and exempt.
    ``masked`` is pre-masked text (per :func:`parse_regions`).
    """
    result = []
    lines = masked.split('\n')
    for lineno, line in enumerate(lines[:-1], 1):
        # a dangle breaks a word at its hyphen: word char, hyphen, EOL
        if not re.search(r'\w-$', line.rstrip()):
            continue
        # the next line must continue the text, minus the idiom
        following = lines[lineno].lstrip()
        if re.match(r'\w', following) and not re.match(r'(?:and|n?or) ', following):
            result.append(lineno)
    return result


def wrapped_marker_lines(masked: str, text: str) -> list[int]:
    """Return 1-based line numbers where a list marker breaks a sentence.

    A line opening with a list marker (``+ ``/``- ``/``* ``) renders as
    a bullet, so a wrapped continuation starting with one reads back as
    a phantom list item -- and a real list opening directly under a
    paragraph line (no blank line between) renders just as broken. A
    marker line is healthy under a structural line (blank, another list
    item, a heading, a blockquote or table row, a thematic break, a
    comment, or a bare block-scalar header) or inside an open list --
    an item opened at or under its indent since the last blank line --
    where the line above is a wrapped continuation of the item, not a
    paragraph. ``masked`` is pre-masked text (per :func:`parse_regions`)
    and ``text`` the raw text it was masked from: a marker counts only
    when it opens the raw line too, since masking a leading code span
    leaves a marker-shaped remainder that never renders as a bullet.
    """
    result = []
    lines = masked.split('\n')
    raw = text.split('\n')
    # indents of the list items opened since the last blank line
    open_items: list[int] = []
    for lineno, line in enumerate(lines, 1):
        if not line.strip():
            open_items = []
            continue
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        # a marker must open the raw line as well as the masked one
        masked_marker = bool(re.match(r'(?:[-+*]|\d+[.)]) ', stripped))
        raw_marker = bool(re.match(r'(?:[-+*]|\d+[.)]) ', raw[lineno - 1].lstrip()))
        marker = masked_marker and raw_marker
        bullet = bool(re.match(r'[-+*] ', stripped))
        in_open_list = any(item <= indent for item in open_items)
        if marker and bullet and (lineno > 1) and not in_open_list:
            # only a paragraph line above makes the marker line a mangle
            previous = lines[lineno - 2].strip()
            list_item = bool(re.match(r'(?:[-+*]|\d+[.)]) ', previous))
            block_start = previous.startswith(('#', '>', '|', '<!--'))
            thematic_break = bool(re.fullmatch(r'\*{3,}|-{3,}|_{3,}', previous))
            scalar_header = bool(re.fullmatch(r'\w+:\s*[|>][-+0-9]*', previous))
            structural = list_item or block_start or thematic_break or scalar_header
            if previous and not structural:
                result.append(lineno)
        if marker:
            open_items.append(indent)
    return result
