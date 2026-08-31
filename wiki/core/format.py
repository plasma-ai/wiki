"""Functions for the wiki on-disk format."""

from __future__ import annotations

import bisect
import functools
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

# per-process memo of composed frontmatter blocks: an update reads each block
# a few times and a lint a few more, and a run over a few thousand pages fits
# one cache at about a kilobyte per block; a block past the byte bound is
# composed on every read rather than pinned for the run
_SCALAR_CACHE_SIZE = 4_096
_SCALAR_CACHE_BYTES = 65_536

# the deepest collection nesting handed to the composer, which recurses per
# level: the pure loader raises RecursionError a few hundred levels down, and
# the C loader, its recursion unchecked, overruns the C stack on older
# interpreters -- far past any authored block either way
_MAX_NESTING = 100

# characters that open a YAML indicator (flow collection, comment, node
# property, block scalar, quote, directive, reserved) when they lead a value
_INDICATOR_CHARS = ',[]{}#&*!|>\'"%@`'

# the scalar fields of one block: key -> (value text, style, resolved tag) for
# a scalar value -- the style None for plain, else the quote or block
# indicator -- and None for a sequence or mapping value
_Fields = dict[str, Optional[tuple[str, Optional[str], str]]]

# a strict-reader finding on a block: (1-based file line, reason, cause), the
# cause one of 'parse', 'nonmapping', 'nonscalar_key', 'duplicate_key'
_Issue = tuple[int, str, str]

# the top-level keys of a block in document order, each with the 0-based line
# of its key line within the block (a non-scalar key spells as '')
_Keys = tuple[tuple[str, int], ...]

# characters no YAML stream may carry plain or single-quoted -- the C0 and C1
# controls (a tab excepted), the DEL, the line separators LS and PS, and the
# noncharacters U+FFFE and U+FFFF -- plus lone surrogates; a value holding one
# is written double-quoted with escapes
_ESCAPED_CHARS = re.compile(
    r'[\x00-\x08\x0a-\x1f\x7f-\x9f\u2028\u2029\ud800-\udfff\ufffe\uffff]'
)

# the single-character escapes of a double-quoted scalar, by the letter after
# the backslash (YAML 1.1 section 5.7)
_ESCAPES = {
    '0': '\x00',
    'a': '\x07',
    'b': '\x08',
    't': '\t',
    '\t': '\t',
    'n': '\n',
    'v': '\x0b',
    'f': '\x0c',
    'r': '\r',
    'e': '\x1b',
    ' ': ' ',
    '"': '"',
    '/': '/',
    '\\': '\\',
    'N': '\x85',
    '_': '\xa0',
    'L': '\u2028',
    'P': '\u2029',
}

# the characters the writer spells with a one-letter escape rather than \uXXXX
_SHORT_ESCAPES = {
    char: letter
    for letter, char in _ESCAPES.items()
    if letter not in ('\t', ' ', '"', '/', '\\')
}

# lone surrogates: text no UTF-8 writer can emit
_SURROGATES = re.compile(r'[\ud800-\udfff]')

# the line grammar's key line, the fallback for a block the parser rejects: a
# bare or dotted key at column 0, quoted or not, behind optional node
# properties, its colon followed by a space or the line end; _key_of names the
# key whichever group matched
_KEY_LINE = (
    r'^(?:[&!]\S*[ \t]+)*(?:"([\w.-]+)"|\'([\w.-]+)\'|([\w.-]+))[ \t]*:(?:[ \t]|$)'
)

#: the lines continuing a frontmatter field below its key line under the line
#: grammar: indented lines, blank lines, column-0 sequence items (``- item``,
#: the value of a bare key alone -- see :func:`_field_span`), and column-0
#: comments; mirrored, minus the trailing blanks, the items, and the comments
#: (authored lines it merges as such), by the merge driver's awk extent
FIELD_EXTENT = r'(?:[ \t]+.*\n|[ \t]*\n|-(?:[ \t].*)?\n|#.*\n)*'

# the extent below a key line carrying a value: a column-0 item after a value
# is text to YAML, outside the field
_VALUED_EXTENT = r'(?:[ \t]+.*\n|[ \t]*\n|#.*\n)*'


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
    # extract links (everything between frontmatter and the marker;
    # the marker is the first delimiter, so the range holds none)
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


def build_frontmatter(
    *,
    name: str,
    created: str,
    updated: str,
    desc: str = '...',
) -> str:
    """Build YAML frontmatter string.

    Args:
        name: Display name for the index.
        created: ISO 8601 timestamp.
        updated: ISO 8601 timestamp.
        desc: Description value; the default is the ``...`` placeholder
            lint holds open until a value is authored. A multi-line
            value writes as a literal block scalar.

    Returns:
        Complete frontmatter block including ``---`` delimiters.

    """
    # a multi-line desc writes as a literal block, unless a line holds a
    # character no block may carry, which only a double-quoted scalar escapes
    if ('\n' in desc) and not _ESCAPED_CHARS.search(desc.replace('\n', '')):
        body = '\n'.join(f'  {line}'.rstrip() for line in desc.split('\n'))
        desc_lines = ['desc: |', *body.split('\n')]
    else:
        desc_lines = [f'desc: {quote(desc)}']
    lines = [
        '---',
        f'name: {quote(name)}',
        *desc_lines,
        'tags: []',
        'sources: []',
        f'created: {quote(created)}',
        f'updated: {quote(updated)}',
        '---',
    ]
    return '\n'.join(lines)


def strip_blank_lines(frontmatter: str) -> str:
    """Drop frontmatter blank lines that are not scalar body content.

    A blank line between one-line fields is never structure, so strays
    are repaired away -- but inside a multi-line value it is content: a
    paragraph break in a block (``|``/``>``), plain, or quoted scalar
    that spans lines. A blank run is kept whenever the value continues
    after it (the next line stays indented, or a quoted scalar is still
    open), and kept at the end of a keep-chomping block (``|+``/``>+``),
    whose trailing newlines are themselves the value -- at any nesting
    depth, so a ``- k: |+`` item keeps its blank too. Blanks re-emit
    verbatim, so an over-indented whitespace-only body line keeps its
    content spaces.
    """
    result = []
    pending = []  # blank lines held verbatim until the next line reveals them
    in_block = False
    header_indent = 0
    block_indent = None  # the body indentation of the open block scalar, once set
    keep_chomp = False
    quote_char = None  # the quote opening a scalar not yet closed
    flow_depth = 0  # the brackets of a flow collection not yet closed
    flow_quote = None  # the quote a line of the open flow collection left open
    for line in frontmatter.split('\n'):
        # every line of an open quoted scalar or flow collection is content,
        # blank or not, up to the line that closes it -- or, for a bracket
        # never closed, up to the key line the line grammar reads next
        if quote_char is not None:
            result.extend(pending)
            pending = []
            result.append(line)
            if _quote_close(quote_char + line) != -1:
                quote_char = None
            continue
        if flow_depth and (_key_of(line) is None):
            if not line.strip():
                pending.append(line)
                continue
            result.extend(pending)
            pending = []
            result.append(line)
            flow_depth, flow_quote = _flow_depth(line, flow_depth, flow_quote)
            continue
        flow_depth, flow_quote = 0, None
        # inside a block scalar a whitespace-only line indented past the body
        # is content (its extra spaces), so it re-emits with the blanks it ends
        if not line.strip():
            if in_block and (block_indent is not None) and (len(line) > block_indent):
                result.extend(pending)
                pending = []
                result.append(line)
            else:
                pending.append(line)
            continue
        # an indented line continues a multi-line value, so a preceding blank
        # run is a paragraph break; a keep-chomping block keeps its trailing
        # blanks even where the next line dedents to the following field
        indent = len(line) - len(line.lstrip())
        if indent or keep_chomp:
            result.extend(pending)
        pending = []
        result.append(line)
        # a line inside the open block's body sets the body indentation
        if in_block and (indent > header_indent):
            if block_indent is None:
                block_indent = indent
            continue
        # every other line ends the block; a field or sequence item at any
        # depth may open a block scalar (only a keep-chomping one owns the
        # blank run that trails it), a quoted scalar spanning lines, or a
        # flow collection spanning lines -- a continuation line of a plain
        # value opens nothing
        in_block = False
        keep_chomp = False
        value = re.sub(r'^(?:[&!]\S*\s+)+', '', _line_value(line))
        opens = (indent == 0) or (value != line.strip())
        header = re.fullmatch(r'([|>][-+]?([0-9])?[-+]?)(?:[ \t]+#.*)?', value)
        if not opens:
            continue
        if header is not None:
            in_block = True
            header_indent = indent
            keep_chomp = '+' in header.group(1)
            # an explicit indentation indicator fixes the body indentation up front
            digit = header.group(2)
            block_indent = header_indent + int(digit) if digit else None
        elif (value[:1] in ('"', "'")) and (_quote_close(value) == -1):
            quote_char = value[0]
        elif value[:1] in ('[', '{'):
            flow_depth, flow_quote = _flow_depth(value, 0)
    # blanks past the closing delimiter sit outside the frontmatter block
    result.extend(pending)
    return '\n'.join(result)


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

    The shared frontmatter surgery for index and page planning: stray
    blank-line removal (:func:`strip_blank_lines`; block-scalar bodies
    keep theirs), callable ``name:`` replacement (backslash-digit safe),
    placeholder restore on blank keys, in-place stamps so duplicates are
    never appended, insertions in schema order (desc after name; created
    before updated), removal of an unset ``title:``/``category:`` (per
    their flags), and -- when ``order`` is set -- canonical field ordering
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
    # a BOM at column 0 is no field's text -- the C loader skips it on any
    # line, and the line tests below would read a `# comment` behind one as
    # content -- so it goes, as a file BOM goes on adoption, rather than ride
    # on a key line the order pass may move
    frontmatter = re.sub(r'^\ufeff', '', frontmatter, flags=re.MULTILINE)
    # a blank line is never frontmatter structure -- drop strays before
    # any field surgery (block-scalar bodies keep theirs)
    frontmatter = strip_blank_lines(frontmatter)
    # refresh name: from the path-derived name (added if the field is
    # missing, so frontmatter with no name: does not stay un-named); the
    # whole extent goes with the stale value, so nothing strands as a
    # continuation of the fresh one-liner -- a block scalar's indented body
    # is content, so only a column-0 comment among its lines survives, while
    # a plain value's continuation lines are value text, so every comment
    # line among them re-emits under the fresh name
    span = _field_span(frontmatter, 'name')
    if span:
        start, end = span
        indicator, _, body = frontmatter[start:end].partition('\n')
        value = _uncommented(indicator.split(':', 1)[1])
        block = re.sub(r'^(?:[&!]\S*\s+)+', '', value).startswith(('|', '>'))
        tail, _ = _field_comments(indicator, '')
        fresh = f'name: {quote(name)}{tail}\n' + _comment_lines(
            body, indented=not block
        )
    else:
        start = _block_end(frontmatter)
        end = start
        fresh = f'name: {quote(name)}\n'
    frontmatter = frontmatter[:start] + fresh + frontmatter[end:]
    name_end = start + len(fresh)
    # add desc field if missing (after the name field just written -- whose
    # span is not read back, since a refresh closing a quote left open above
    # it can leave a strict reader no name key to find); restore the
    # placeholder on a present-but-valueless key -- a bare key, a
    # quoted-empty value, or an empty block scalar (mirrors the title
    # branch's valueless check below), all of which read as no description;
    # a real block-scalar body is kept
    span = _field_span(frontmatter, 'desc')
    if span:
        start, end = span
        indicator, _, body = frontmatter[start:end].partition('\n')
        if _is_valueless(indicator, body, nulls=('', "''", '""')):
            # the field's properties and comments ride along with the placeholder
            tail, comments = _field_comments(indicator, body)
            placeholder = f'desc: {_properties(indicator)}...{tail}\n{comments}'
            frontmatter = frontmatter[:start] + placeholder + frontmatter[end:]
    else:
        frontmatter = frontmatter[:name_end] + 'desc: ...\n' + frontmatter[name_end:]
    # add created/updated if missing; stamp a present-but-valueless key in
    # place so a duplicate is never appended (created slots before updated)
    frontmatter = _fill_stamp(frontmatter, 'created', now, before='updated')
    frontmatter = _fill_stamp(frontmatter, 'updated', now, before=None)
    # drop an unset category and title: absence is the canonical unset form,
    # so a provably valueless field -- a blank value, the plain lowercase null
    # spelling, or an empty block scalar -- removes its whole extent while its
    # comments stay behind at column 0, where they are comments after every
    # neighbor (a block-scalar desc included); a quoted or block-scalar 'null'
    # is authored text, kept verbatim, and neither field is ever inserted;
    # every leading valueless copy goes, since the first occurrence wins for
    # every reader and the run stops at the first copy carrying a value
    for key, unset in (('category', category), ('title', title)):
        if not unset:
            continue
        while span := _field_span(frontmatter, key):
            start, end = span
            indicator, _, body = frontmatter[start:end].partition('\n')
            if not _is_valueless(indicator, body, nulls=('', 'null')):
                break
            tail, comments = _field_comments(indicator, body)
            kept = [tail.strip()] if tail else []
            kept.extend(line.lstrip() for line in comments.split('\n') if line)
            frontmatter = (
                frontmatter[:start]
                + ''.join(f'{line}\n' for line in kept)
                + frontmatter[end:]
            )
    # enforce the canonical field order LAST: the insertions above anchor
    # on schema neighbors and authored fields start anywhere, so the full
    # reorder must have the final word -- and a blank an open quote above it
    # kept is a stray once the move puts the quote below it
    if order:
        frontmatter = strip_blank_lines(order_frontmatter(frontmatter))
    return frontmatter


def order_frontmatter(frontmatter: str) -> str:
    """Reorder frontmatter fields into the canonical schema order.

    Fields land as ``name``, ``title``, ``desc``, ``category``,
    ``tags``, ``sources``, then any unrecognized authored keys in their
    original relative order, with the tool-owned ``created``/``updated``
    tail closing the block. Each field moves as its full extent -- its
    key line through the line before the next key (:func:`_field_span`)
    -- byte-verbatim, so a block scalar, a quoted scalar continued at
    column 0, or a flow collection spanning lines never strands its
    continuation lines. Duplicate keys stay adjacent in original order,
    so a first-occurrence read resolves to the same value after the
    move. Non-field lines above the first key stay above it, and a
    document-end ``...`` marker above the closing delimiter stays there.
    A block carrying an alias is returned as written: an alias must
    follow its anchor, so moving either would break the block.
    """
    _, _, _, _, aliased = _compose_fields(frontmatter)
    if aliased:
        return frontmatter
    lines = frontmatter.split('\n')
    end = _end_line(lines)
    # group the body (between the delimiters) into per-field extents: a
    # key line opens an extent, every other line continues the open one,
    # and lines before the first key hold as a preamble
    key_at = _key_at(frontmatter)
    extents: list[tuple[str, list[str]]] = []
    preamble = []
    current = None
    for index in range(1, end):
        line = lines[index]
        if key_at is not None:
            key = key_at.get(index)
            # a key spelled outside the line grammar (one with spaces, the
            # merge driver's repair hint) rides with the field before it
            # rather than moving as a field of its own
            if (key is not None) and not re.fullmatch(r'[\w.-]+', key):
                key = None
        else:
            key = _key_of(line)
        if key is not None:
            current = (key, [line])
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
    result.extend(lines[end:])
    return '\n'.join(result)


def seed_frontmatter_title(frontmatter: str, title: Optional[str] = None) -> str:
    """Seed a ``title:`` line directly under the ``name:`` field when none exists.

    ``title`` is the value to seed -- adopting a bare page preserves its
    authored H1 this way -- and ``None`` seeds the ``title: null``
    placeholder required-titles mode demands. Frontmatter already
    carrying a ``title`` field is returned unchanged: the field is
    authored, so a present line is never overwritten.
    """
    if _field_span(frontmatter, 'title') is not None:
        return frontmatter
    if title is None:
        value = 'null'
    else:
        # quote the reserved lowercase null spelling: an authored H1
        # reading "null" must read back as text, not the placeholder
        value = "'null'" if title == 'null' else quote(title)
    span = _field_span(frontmatter, 'name')
    pos = span[1] if span else _block_end(frontmatter)
    return frontmatter[:pos] + f'title: {value}\n' + frontmatter[pos:]


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


def is_nonmapping_frontmatter(frontmatter: str) -> bool:
    """Return whether the block is valid YAML that is not a ``key: value`` mapping.

    A bare sentence or a list between the fences has no fields to read
    or repair; the planners keep such a page as written and report it
    rather than append fields under the text.
    """
    kind, _, _, _, _ = _compose_fields(frontmatter)
    return kind == 'nonmapping'


def is_unaddressable_frontmatter(frontmatter: str) -> bool:
    """Return whether the block is a mapping whose keys no byte-level writer can reach.

    A flow mapping (``{name: x}``) or a mapping indented as a whole has
    no column-0 ``key:`` lines for the repair to refresh, fill, or
    reorder, so appending fields under it would break the block; the
    planners keep such a page as written and report it, while the
    reader still reads its fields through the parser.
    """
    kind, _, _, keys, _ = _compose_fields(frontmatter)
    return (kind == 'mapping') and not keys


def repair_breaks_frontmatter(before: str, after: str) -> bool:
    """Return whether a repair turned a block every strict reader accepts into one they reject, or wrote its fields where none finds them.

    The byte-level repair edits by line grammar; a block it cannot
    address safely is kept as written by the planners rather than
    rewritten into a parse error -- or, when the block it leaves
    composes, into a scalar that swallowed the ``name``, ``desc``,
    ``created``, or ``updated`` line the repair wrote, or an authored key
    line the line grammar read (a quote the grammar cannot see closing
    around them). A tool-owned field whose composed extent holds a
    key-shaped line is refused too: the rewrite of its extent would
    delete the line a strict reader folded into the value.
    """
    before_kind, _, before_issues, _, _ = _compose_fields(before)
    kind, fields, issues, _, _ = _compose_fields(after)
    if issues and not before_issues:
        return True
    if kind == 'invalid':
        return False
    # the repair writes every one of these keys: a block that composes without
    # one took the write inside a scalar
    required = {'name', 'desc', *_FRONTMATTER_TAIL}
    if before_kind == 'mapping':
        # a key-shaped or item-shaped line folded into a quoted tool-owned
        # value is authored structure the extent rewrite would delete; the
        # scan spans the open quote alone -- a comment line after the closing
        # quote is one the repair re-emits, no reason to refuse
        for key in ('name', *_FRONTMATTER_TAIL):
            field = _compose_fields(before)[1].get(key)
            if (field is None) or (field[1] not in ('"', "'")):
                continue
            text = field_text(before, key) or ''
            span_lines = text.split('\n')
            value = re.sub(
                r'^(?:[&!]\S*(?:\s+|$))+', '', span_lines[0].split(':', 1)[1].strip()
            )
            if (value[:1] not in ('"', "'")) or (_quote_close(value) != -1):
                continue
            for line in span_lines[1:]:
                if re.match(r'^(?:\S.*?)?:(?:[ \t]|$)', line) or re.match(
                    r'-(?:[ \t]|$)', line
                ):
                    return True
                if _quote_close(value[0] + line.strip()) != -1:
                    break
    else:
        # every key the line grammar read must survive, bar the unset title
        # and category the repair removes
        grammar = {key for line in before.split('\n') if (key := _key_of(line))}
        required |= grammar - {'title', 'category'}
    return any(key not in fields for key in required)


def frontmatter_issues(frontmatter: str) -> list[_Issue]:
    """Return the findings a strict YAML reader raises on the block.

    Each is ``(line, reason, cause)``: the 1-based file line, the
    parser's problem or the wiki's description, and the cause -- a
    ``'parse'`` error, a ``'nonmapping'`` body, a ``'nonscalar_key'``,
    or a ``'duplicate_key'``. An empty list is a block every strict
    reader accepts as a mapping with unique scalar keys.
    """
    _, _, issues, _, _ = _compose_fields(frontmatter)
    return list(issues)


def field_text(frontmatter: str, key: str) -> Optional[str]:
    """Return the raw text of ``key``'s field -- its key line and continuation lines -- or ``None``.

    The extent :func:`_field_span` bounds: the composed marks' when the
    block composes, the line grammar's otherwise.
    """
    span = _field_span(frontmatter, key)
    if span is None:
        return None
    start, end = span
    return frontmatter[start:end]


def is_collection_field(frontmatter: str, key: str) -> bool:
    """Return whether ``key`` carries a sequence or mapping in a block the parser accepts."""
    fields = _scalar_fields(frontmatter)
    return bool(fields) and (key in fields) and (fields[key] is None)


def comment_cuts_desc(frontmatter: str) -> bool:
    """Return whether a ``' #'`` comment shortened a plain ``desc`` a strict reader accepts.

    Only a plain scalar loses text to a comment, and only a block the
    parser accepts reads through the parser; the field's raw extent is
    searched for the comment start, key line and continuation lines
    alike.
    """
    fields = _scalar_fields(frontmatter)
    if not fields or (fields.get('desc') is None):
        return False
    _, style, _ = fields['desc']
    if style is not None:
        return False
    text = field_text(frontmatter, 'desc') or ''
    # only a comment start after value text on a value line cuts anything
    lines = [line for line in text.split('\n') if not line.lstrip().startswith('#')]
    return re.search(r'\S[ \t]+#', '\n'.join(lines)) is not None


def restamp_updated(frontmatter: str, now: str) -> str:
    """Rewrite the ``updated`` stamp to ``now``, its comments riding along.

    The field's whole extent is replaced, so a stamp continued on
    indented lines never strands under the fresh one; the key line's
    tail comment re-attaches after the stamp, an anchor on it stays on
    the stamp (an alias of it elsewhere keeps resolving), and the
    comment lines under it stay. A block without the field gains it
    before the closing delimiter.
    """
    span = _field_span(frontmatter, 'updated')
    if span is None:
        return _fill_stamp(frontmatter, 'updated', now, before=None)
    start, end = span
    indicator, _, body = frontmatter[start:end].partition('\n')
    tail, comments = _field_comments(indicator, body)
    stamp = f'updated: {_properties(indicator)}{quote(now)}{tail}\n{comments}'
    return frontmatter[:start] + stamp + frontmatter[end:]


def read_frontmatter_field(frontmatter: str, key: str) -> Optional[str]:
    """Read a scalar frontmatter ``key`` as a strict YAML reader sees it.

    The value is the scalar text a YAML parser resolves -- a plain scalar
    folded across its continuation lines, a quoted scalar decoded, a
    block scalar's body without its final line breaks (every consumer
    wants the text, not the chomping) -- and never a coerced type: a
    stamp stays its source text. The first occurrence of a duplicated
    key wins. Returns ``None`` when the field is absent or a bare
    ``key:`` has no body. A sequence or mapping value reads as its
    source lines joined; a body that is not a mapping has no fields; a
    block the parser rejects falls back to the line grammar
    (:func:`_read_field_lines`), so the wiki's leniencies -- an unquoted
    ``': '`` inside a one-line value, conflict markers -- still read
    through it.
    """
    fields = _scalar_fields(frontmatter)
    if fields is None:
        value = _read_field_lines(frontmatter, key)
    elif key not in fields:
        value = None
    elif fields[key] is None:
        # a sequence or mapping is never resolved: its source lines, comments
        # dropped, join into the text the search index tokenizes (a mapping
        # the line grammar cannot place has no lines to give)
        text = field_text(frontmatter, key)
        value = _collection_text(text) if text is not None else None
    else:
        value, style, _ = fields[key]
        # a block scalar's final breaks are chomping, not content
        if style in ('|', '>'):
            value = value.rstrip('\n')
        # a bare key with no body reads as an absent value
        elif (style is None) and (value == ''):
            value = None
    # a carriage return (a "\r" escape) would split the line model every
    # consumer relies on, so it reads as the line break it is; a NUL (a "\0"
    # escape) would turn every markdown surface it lands on binary to git, so
    # it goes
    if value is not None:
        value = value.replace('\r\n', '\n').replace('\r', '\n').replace('\x00', '')
    return value


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
    # a bare 'title:' defers to the delegate, which resolves an indented
    # body as a plain multi-line scalar and no body as an absent value
    if _is_unset_field(frontmatter, 'title'):
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
    # a bare 'category:' defers to the delegate, which resolves an indented
    # body as a plain multi-line scalar and no body as an absent value
    if _is_unset_field(frontmatter, 'category'):
        return ''
    value = read_frontmatter_field(frontmatter, 'category')
    return join_lines(value or '')


def field_value(line: str, key: Optional[str] = None) -> str:
    """Extract one frontmatter line's value for per-line matching.

    Strips a ``key:`` prefix and surrounding YAML quotes
    (:func:`unquote`), else returns the stripped line -- search's
    per-line field-mode extraction, kept beside the quoting rules it
    inverts. Unlike :func:`read_frontmatter_field`, which resolves the
    joined value of a whole field, this reads a single line so matches
    keep their line numbers. ``key`` is the composed key the line opens
    (:func:`line_keys`), so a key spelled outside the line grammar (one
    with spaces, one whose quotes escape or double a quote) strips too;
    ``''`` says the composed block opens no key on the line (a quoted
    scalar's continuation, whatever its shape), so nothing strips; ``None``
    leaves the line grammar's key shape to strip.
    """
    if key == '':
        return line.strip()
    if key is None:
        opener = r'(?:"[^"]*"|\'[^\']*\'|[\w.-]+)'
    else:
        # the line is known to open the key: any quoted spelling at its
        # start is that key, however the quotes decode
        spelled = re.escape(key)
        opener = (
            rf'(?:"{spelled}"|\'{spelled}\'|{spelled}'
            r'|"(?:[^"\\]|\\.)*"|\'(?:[^\']|\'\')*\')'
        )
    match = re.match(rf'^(?:[&!]\S*[ \t]+)*{opener}[ \t]*:(?:[^\S\n]+|$)', line)
    if not match:
        return line.strip()
    # a node property is not the value, and neither is a comment after it:
    # past the closing quote of a quoted one, from the ' #' of a plain one
    value = re.sub(r'^(?:[&!]\S*[ \t]+)+', '', line[match.end() :].strip())
    if value[:1] in ('"', "'"):
        # a quote closing on a later line: the opening quote is not value
        # text; one mid-text (an apostrophe) is content
        close = _quote_close(value)
        return unquote(value + value[0] if close == -1 else value[: close + 1])
    return unquote(_uncommented(value))


def line_keys(frontmatter: str) -> dict[int, str]:
    """Return the composed key opening each 1-based file line of the block.

    The block opens the file, so its line ``n`` is file line ``n + 1``;
    a block the parser rejects, or one whose keys the line grammar
    cannot place, names no key here and reads through the line grammar.
    """
    key_at = _key_at(frontmatter) or {}
    return {line + 1: key for line, key in key_at.items()}


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
    (multi-line block scalars). The composed marks say which lines
    open a field when the block composes; the line grammar does
    otherwise.

    Args:
        frontmatter: Parsed frontmatter string (including delimiters).
        lines: Full file lines (from ``text.split('\n')``).
        fields: Field names to match.

    """
    # initialize result
    result = set()
    end = _end_line(frontmatter.split('\n'))
    key_at = _key_at(frontmatter)
    current_field = None
    for index in range(1, end):
        line = lines[index]
        lineno = index + 1
        # check for field key
        if key_at is not None:
            key = key_at.get(index)
        else:
            key = _key_of(line)
        if key is not None:
            current_field = key
            if current_field in fields:
                result.add(lineno)
            continue
        # under the line grammar a dedented field line whose key sits outside
        # the key grammar (e.g. one with spaces) still ends the current field
        # -- its line and block body must not attribute to the preceding
        # field -- while a dedented sequence item (`- https://...`) or a flow
        # continuation (`https://b]`) continues it, colon and all: only a
        # colon followed by a space or the line end makes a key
        if key_at is None:
            dedented = line[:1] not in (' ', '\t')
            item = re.match(r'-(?:[ \t]|$)', line) is not None
            if dedented and line.strip() and not line.startswith('#') and not item:
                current_field = None
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
    """YAML-quote a scalar when writing it plain would misread.

    A value a strict reader would not read back verbatim as plain text
    (:func:`_is_plain_safe`) -- one shaped like a mapping, carrying a
    comment start, opening with an indicator, or wrapped in quote chars
    that :func:`unquote` would strip on read -- is written single-quoted
    with embedded single quotes doubled; one carrying a character no
    stream may hold (a control, a line separator) is written
    double-quoted with those characters escaped; any other value passes
    through unquoted. Inverse of :func:`unquote` for the values the wiki
    writes.
    """
    if _ESCAPED_CHARS.search(value):
        escaped = value.replace('\\', '\\\\').replace('"', '\\"')
        escaped = _ESCAPED_CHARS.sub(repl=_escape, string=escaped)
        return f'"{escaped}"'
    if not _is_plain_safe(value):
        escaped = value.replace("'", "''")
        return f"'{escaped}'"
    return value


def unquote(value: str) -> str:
    """Strip one pair of matching surrounding YAML quotes from a scalar.

    A quoted scalar (``"..."`` / ``'...'``) resolves to its body, with the
    YAML escapes undone -- doubled single quotes in a single-quoted value;
    the backslash escapes for quotes, backslashes, line breaks, tabs, and
    hex or unicode code points in a double-quoted one. An unquoted value
    is returned unchanged.
    """
    if (len(value) >= 2) and (value[0] == value[-1]) and (value[0] in ('"', "'")):
        body = value[1:-1]
        if value[0] == '"':
            return re.sub(
                pattern=r'\\(?:U([0-9a-fA-F]{8})|u([0-9a-fA-F]{4})|x([0-9a-fA-F]{2})|(.))',
                repl=_unescape,
                string=body,
            )
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
    leaves a marker-shaped remainder that never renders as a bullet, and
    a list closes only at a raw blank line, since a continuation that is
    nothing but a code span (a bare backticked path) masks to blank while
    the list stays open on the rendered surface.
    """
    result = []
    lines = masked.split('\n')
    raw = text.split('\n')
    # indents of the list items opened since the last blank line
    open_items: list[int] = []
    for lineno, line in enumerate(lines, 1):
        if not line.strip():
            # only a raw blank closes the list: a masked-blank continuation
            # would otherwise phantom-flag the next legal bullet
            if not raw[lineno - 1].strip():
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


# ------ helper functions


def _uncommented(text: str, quote: Optional[str] = None) -> str:
    """Return ``text`` without a ``# comment`` -- one leading it or following whitespace.

    A ``#`` inside a quoted scalar (one opening where a value starts, closed
    or not) is text; a quote inside a plain value (``rock 'n roll``) opens
    nothing. ``quote`` carries a span a previous line left open -- its
    content, ``#`` included, is text up to the closing quote.
    """
    index = 0
    prev = None  # the last non-whitespace character; None where a value starts
    if quote is not None:
        close = _flow_close(quote + text)
        if close == -1:
            return text.strip()
        index = close
        prev = quote
    while index < len(text):
        char = text[index]
        if (char in ('"', "'")) and ((prev is None) or (prev in '[{,:-?')):
            close = _flow_close(text, index)
            if close == -1:
                break
            index = close + 1
            prev = char
            continue
        if (char == '#') and ((index == 0) or text[index - 1].isspace()):
            return text[:index].strip()
        if not char.isspace():
            prev = char
        index += 1
    return text.strip()


def _properties(indicator: str) -> str:
    """Return the ``&anchor``/``!tag`` properties leading a key line's value, trailing space included."""
    match = re.match(r'\s*((?:[&!]\S*\s*)+)', indicator.split(':', 1)[1])
    if match is None:
        return ''
    return ' '.join(match.group(1).split()) + ' '


def _flow_depth(
    text: str,
    depth: int,
    quote: Optional[str] = None,
    *,
    bound: Optional[int] = None,
) -> tuple[int, Optional[str]]:
    """Return the flow-collection nesting depth after ``text`` and the quote it leaves open.

    ``[`` and ``{`` open a level, ``]`` and ``}`` close one; a quoted
    scalar -- one opening where a value starts on this line (the line
    start, or after ``[``, ``{``, ``,``, ``:``, ``-``, ``?``), or
    ``quote`` left open by the line above -- is text up to its closing
    quote, and a ``#`` after whitespace ends the line as a comment. With
    ``bound`` the walk stops at the first bracket nesting past it, depth
    past the bound returned.
    """
    index = 0
    prev = None  # the last non-whitespace character; None where a value starts
    if quote is not None:
        close = _flow_close(quote + text)
        if close == -1:
            return depth, quote
        index = close
        prev = quote
    while index < len(text):
        char = text[index]
        if (char in ('"', "'")) and ((prev is None) or (prev in '[{,:-?')):
            close = _flow_close(text, index)
            if close == -1:
                return depth, char
            index = close + 1
            prev = char
            continue
        if (char == '#') and ((index == 0) or text[index - 1].isspace()):
            return depth, None
        if char in ('[', '{'):
            depth += 1
            if (bound is not None) and (depth > bound):
                return depth, None
        elif char in (']', '}'):
            depth = max(depth - 1, 0)
        if not char.isspace():
            prev = char
        index += 1
    return depth, None


def _nested_too_deep(body: str) -> Optional[int]:
    """Return the body line where collections nest past :data:`_MAX_NESTING`, or ``None``.

    Flow brackets nest from a value opening with one, across the lines
    that continue it (quoted spans and comments skipped); a run of
    ``- ``/``? `` indicators opening a line nests within it. A block
    scalar's body and the continuation lines of a plain or quoted value
    are text, whatever they hold.
    """
    depth = 0
    quote = None
    body_indent = None  # the header indentation of an open block scalar
    value_indent = None  # the indentation of a key whose value continues below
    for lineno, line in enumerate(body.split('\n')):
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if (body_indent is not None) and (indent > body_indent):
            continue
        body_indent = None
        if (value_indent is not None) and (indent > value_indent):
            continue
        value_indent = None
        # inside an open flow collection every line continues it
        if depth:
            depth, quote = _flow_depth(line, depth, quote, bound=_MAX_NESTING)
            if depth > _MAX_NESTING:
                return lineno
            continue
        chain = re.match(r'\s*((?:[-?]\s+)*)', line)
        if len(chain.group(1).split()) > _MAX_NESTING:
            return lineno
        value = re.sub(r'^(?:[&!]\S*(?:\s+|$))+', '', _line_value(line))
        if re.fullmatch(r'[|>][-+0-9]*(?:[ \t]+#.*)?', value):
            body_indent = indent
        elif value[:1] in ('[', '{'):
            depth, quote = _flow_depth(value, 0, bound=_MAX_NESTING)
            if depth > _MAX_NESTING:
                return lineno
        elif value:
            value_indent = indent
    return None


def _collection_text(text: str) -> Optional[str]:
    """Join a collection field's source lines into one text: the key line's value, then the lines under it.

    Comment lines and ``' #'`` tails drop (a ``#`` inside a quoted item
    stays, the quote carried across the lines it wraps over), as do the
    node properties before the collection; the rest joins with single
    spaces, so the search index tokenizes every item wherever the
    collection's lines break.
    """
    key_line, _, body = text.partition('\n')
    value = re.sub(r'^(?:[&!]\S*(?:\s+|$))+', '', key_line.split(':', 1)[1].strip())
    kept = []
    quote = None
    for line in (value, *body.split('\n')):
        if (quote is None) and line.lstrip().startswith('#'):
            continue
        kept.append(_uncommented(line, quote))
        _, quote = _flow_depth(line, 0, quote)
    return ' '.join(line for line in kept if line) or None


def _line_value(line: str) -> str:
    """Return the value text a frontmatter line opens: past its ``- `` markers and its ``key: ``.

    A comment line opens nothing.
    """
    text = line.strip()
    if text.startswith('#'):
        return ''
    text = re.sub(r'^(?:-[ \t]+)+', '', text)
    return re.sub(
        r'^(?:"[^"]*"|\'[^\']*\'|[^\s#"\'][^:]*)[ \t]*:(?:[ \t]+|$)', '', text
    )


def _is_valueless(indicator: str, body: str, *, nulls: tuple[str, ...]) -> bool:
    """Return whether a field's ``indicator`` line and ``body`` carry no value.

    Comments are not a value: ``null # why`` resets like ``null``,
    ``| # note`` is an empty block like ``|``, and a body of comment lines
    is no body; neither is a node property over nothing (``&c`` alone
    anchors a null, ``!!str`` alone tags an empty string), while a tag
    over a spelled value types it as text (``!!str null`` is the word).
    The field is valueless when what remains -- on the indicator line, or
    continued in the body -- is one of the ``nulls`` spellings, or a bare
    block header over no body at all (a block's indented body is content,
    comment-shaped lines included; a column-0 comment ends the block).
    """
    properties = _properties(indicator)
    value = _uncommented(indicator.split(':', 1)[1])
    value = re.sub(r'^(?:[&!]\S*(?:\s+|$))+', '', value)
    if re.fullmatch(r'[|>][-+0-9]*', value):
        content = [line for line in body.split('\n') if not line.startswith('#')]
        return not ''.join(content).strip()
    if ('!' in properties) and (value not in ('', "''", '""')):
        return False
    # the indicator's text and the body fold into one plain scalar, so
    # `null` over `null` is the text "null null", not the idiom
    content = ' '.join(_uncommented(line) for line in body.split('\n')).strip()
    folded = ' '.join(part for part in (value, content) if part)
    return folded in nulls


def _comment_lines(body: str, *, indented: bool = True) -> str:
    """Return the comment lines of a field body, newline-terminated and in order.

    Beside a plain or quoted value every line opening with ``#`` after
    optional indentation is a comment; inside a block scalar an indented
    one is content, so ``indented=False`` keeps the column-0 ones alone.
    Lines split on newlines only: a Unicode line separator inside a
    comment is comment text.
    """
    lines = body.split('\n')
    comments = [line for line in lines if line.lstrip().startswith('#')]
    if not indented:
        comments = [line for line in comments if line.startswith('#')]
    return ''.join(f'{line}\n' for line in comments)


def _field_comments(indicator: str, body: str) -> tuple[str, str]:
    """Return the comments a valueless field carries: the indicator's tail, the body's lines.

    The tail keeps a leading space so it re-attaches after a written value;
    the lines keep their indentation and breaks so they re-emit under it.
    A ``#`` inside a quoted value is text: the tail starts past the
    closing quote.
    """
    value = indicator.split(':', 1)[1]
    # the quote test looks past the node properties leading the value
    stripped = re.sub(r'^(?:[&!]\S*\s+)+', '', value.lstrip())
    start = 0
    if stripped[:1] in ('"', "'"):
        close = _quote_close(stripped)
        if close != -1:
            start = len(value) - len(stripped) + close + 1
    match = re.search(r'(?:^|\s)(#.*)$', value[start:])
    tail = f' {match.group(1)}' if match else ''
    return tail, _comment_lines(body)


def _fill_stamp(frontmatter: str, key: str, now: str, *, before: Optional[str]) -> str:
    """Stamp a missing or valueless ``key`` with ``now``.

    A present key is stamped in place -- so a duplicate is never appended
    -- when its value is blank, quoted-empty, or comments only, and the
    comments ride along; a key over a real value (a stamp continued on an
    indented line, a sequence) stays for lint to judge. A missing key is
    inserted before ``before`` when that key is present, else at the
    block's end. ``now`` is quoted whenever a plain scalar would misread
    it, and spliced rather than substituted so a backslash in a user
    ``timestamp.format`` is emitted verbatim; an anchor on the key line
    stays on the stamp, so an alias of it elsewhere keeps resolving.
    """
    # stamp a present-but-valueless key in place
    span = _field_span(frontmatter, key)
    if span:
        start, end = span
        indicator, _, body = frontmatter[start:end].partition('\n')
        if _is_valueless(indicator, body, nulls=('', "''", '""')):
            tail, comments = _field_comments(indicator, body)
            stamp = f'{key}: {_properties(indicator)}{quote(now)}{tail}\n{comments}'
            frontmatter = frontmatter[:start] + stamp + frontmatter[end:]
        return frontmatter
    # insert a missing key before its schema neighbor, else at the block's end
    anchor = _field_span(frontmatter, before) if before is not None else None
    pos = anchor[0] if anchor else _block_end(frontmatter)
    return frontmatter[:pos] + f'{key}: {quote(now)}\n' + frontmatter[pos:]


def _key_at(frontmatter: str) -> Optional[dict[int, str]]:
    """Map each key line's 0-based block line to its key, or ``None`` to defer to the line grammar.

    The composed marks partition a block that composes as a mapping the
    writers can address; a block the parser rejects and a mapping with no
    column-0 keys read through the line grammar, while a body that is not
    a mapping has no key lines at all.
    """
    kind, _, _, keys, _ = _compose_fields(frontmatter)
    if kind == 'nonmapping':
        return {}
    if (kind != 'mapping') or not keys:
        return None
    return {line: key for key, line in keys}


def _end_line(lines: list[str]) -> int:
    """Return the block line where appended fields go: the closing fence, or the epilogue above it.

    A ``...`` document-end marker (a comment after it allowed) and the
    comment lines below it stay the block's last lines, outside every
    field.
    """
    end = len(lines) - 1
    index = end - 1
    while (index > 0) and re.match(r'[ \t]*#', lines[index]):
        index -= 1
    if (index > 0) and re.fullmatch(r'\.\.\.(?:[ \t]+#.*)?[ \t]*', lines[index]):
        return index
    return end


def _block_end(frontmatter: str) -> int:
    """Return the character offset of :func:`_end_line`."""
    lines = frontmatter.split('\n')
    return sum(len(line) + 1 for line in lines[: _end_line(lines)])


def _field_span(frontmatter: str, key: str) -> Optional[tuple[int, int]]:
    """Return the character span of ``key``'s first field, or ``None`` when absent.

    A field is its key line through the line before the next key. The
    composed marks bound it when the block composes as a mapping the
    writers can address, so a quoted key, a quoted scalar continued at
    column 0, and a flow collection spanning lines each stay one field;
    otherwise the line grammar does -- the key line plus its
    :data:`FIELD_EXTENT`. The last field runs to the block's end, a
    trailing document-end marker excluded.
    """
    kind, _, _, keys, _ = _compose_fields(frontmatter)
    if (kind == 'mapping') and keys:
        lines = frontmatter.split('\n')
        for position, (name, line) in enumerate(keys):
            if name != key:
                continue
            end = (
                keys[position + 1][1] if position + 1 < len(keys) else _end_line(lines)
            )
            start_offset = sum(len(text) + 1 for text in lines[:line])
            end_offset = sum(len(text) + 1 for text in lines[:end])
            return start_offset, end_offset
        return None
    match = re.search(
        pattern=rf'{_key_pattern(key)}(.*)\n{FIELD_EXTENT}',
        string=frontmatter,
        flags=re.MULTILINE,
    )
    if match is None:
        return None
    # a column-0 item is the value of a bare key alone (properties and a
    # comment aside): after a value on the key line it is text, outside the field
    value = re.sub(r'^(?:[&!]\S*(?:\s+|$))+', '', _uncommented(match.group(1)))
    if value:
        match = re.search(
            pattern=rf'{_key_pattern(key)}.*\n{_VALUED_EXTENT}',
            string=frontmatter,
            flags=re.MULTILINE,
        )
    return match.span()


def _key_pattern(key: str) -> str:
    """Return the line grammar's regex for ``key``'s key line, quoted or not, up to its colon."""
    spelled = re.escape(key)
    return (
        rf'^(?:[&!]\S*[ \t]+)*(?:"{spelled}"|\'{spelled}\'|{spelled})[ \t]*:(?=[ \t]|$)'
    )


def _key_of(line: str) -> Optional[str]:
    """Return the key a line opens under the line grammar, or ``None``."""
    match = re.match(_KEY_LINE, line)
    if match is None:
        return None
    return next(group for group in match.groups() if group is not None)


def _compose_fields(
    frontmatter: str,
) -> tuple[str, _Fields, tuple[_Issue, ...], _Keys, bool]:
    """Compose a frontmatter block, through the memo for a block of ordinary size.

    See :func:`_compose_cached` for the result; a block past
    :data:`_SCALAR_CACHE_BYTES` composes on every read rather than pin
    its text in the memo for the run.
    """
    if len(frontmatter) > _SCALAR_CACHE_BYTES:
        return _compose_cached.__wrapped__(frontmatter)
    return _compose_cached(frontmatter)


@functools.lru_cache(maxsize=_SCALAR_CACHE_SIZE)
def _compose_cached(
    frontmatter: str,
) -> tuple[str, _Fields, tuple[_Issue, ...], _Keys, bool]:
    """Compose a frontmatter block into its top-level scalar fields.

    Returns ``(kind, fields, issues, keys, aliased)``. ``kind`` is
    ``'mapping'`` when the body is a YAML mapping -- ``fields`` then
    maps each key, first occurrence winning, to its scalar text, style
    (``None`` for plain, else the quote or block indicator), and
    resolved tag, or to ``None`` for a sequence or mapping value --
    ``'empty'`` for a body with no content, ``'nonmapping'`` for valid
    YAML that is not a mapping, and ``'invalid'`` when the parser
    rejects the body or a key is not a scalar. ``issues`` are the
    findings a strict reader raises -- the parse error, the non-mapping
    body, each non-scalar key, each duplicate key -- as ``(line, reason,
    cause)`` with 1-based file lines. ``keys`` are the top-level keys in
    document order with the block line of each key line, empty unless
    the root is a block mapping whose keys all open at column 0 -- the
    lines the byte-level writers can address. ``aliased`` is whether the
    graph reaches a node twice (an alias), which pins its lines' order.
    Only the body between the fences reaches the parser: the fences are
    the wiki's grammar and document markers to YAML. The node graph is
    composed, never constructed, so a stamp stays its source text and a
    typed-looking title stays a string.
    """
    import yaml

    # feed the parser the body between the fences (a fenceless body reads as is)
    lines = frontmatter.split('\n')
    offset = 0
    if lines and (lines[0].lstrip('\ufeff').strip() == '---'):
        lines = lines[1:]
        offset = 1
    if lines and (lines[-1].rstrip() == '---'):
        lines = lines[:-1]
    # a BOM at column 0 is skipped by the C loader on any line but by the pure
    # reader only at the stream start, so they go before the parse and both
    # loaders mark the same stream (a line count the marks index by)
    body = re.sub(r'^\ufeff', '', '\n'.join(lines) + '\n', flags=re.MULTILINE)
    # nesting past the bound is refused before it reaches either loader's
    # recursion (see _MAX_NESTING), on the line that passes it
    deep = _nested_too_deep(body)
    if deep is not None:
        reason = f'collections nested deeper than {_MAX_NESTING} levels'
        return 'invalid', {}, ((deep + offset + 1, reason, 'parse'),), (), False
    # the C loader is a build-time option of the PyYAML wheel; values and
    # styles match the pure loader's, which raises RecursionError on nesting
    # the C loader composes, while the C loader raises UnicodeEncodeError,
    # not a YAML error, encoding a lone surrogate for libyaml, and an escape
    # past U+10FFFF raises ValueError from the pure loader's chr()
    loader = getattr(yaml, 'CSafeLoader', yaml.SafeLoader)
    try:
        root = yaml.compose(body, Loader=loader)
    except (yaml.YAMLError, RecursionError, UnicodeEncodeError, ValueError) as e:
        return 'invalid', {}, (_parse_issue(e, body, offset),), (), False
    if root is None:
        return 'empty', {}, (), (), False
    if not isinstance(root, yaml.MappingNode):
        issue = (1, 'frontmatter is not a mapping', 'nonmapping')
        return 'nonmapping', {}, (issue,), (), False
    # walk the pairs in order: the first occurrence of a key wins, and a
    # repeat or a non-scalar key is a strict-reader finding; a key's line
    # comes from its mark's character offset, since a mark's own line count
    # treats NEL, LS, and PS as line breaks
    kind = 'mapping'
    fields: _Fields = {}
    issues: list[_Issue] = []
    keys: list[tuple[str, int]] = []
    seen: dict[str, int] = {}
    addressable = not root.flow_style
    starts = [0, *(match.end() for match in re.finditer('\n', body))]
    reached: set[int] = set()
    for key_node, value_node in root.value:
        scalar_key = isinstance(key_node, yaml.ScalarNode)
        key = key_node.value if scalar_key else ''
        # an alias key is the anchored node reached again: its own position is
        # not the composer's to give, so no writer can address the block, and
        # the value's mark names its line
        if id(key_node) in reached:
            alias_line = (
                bisect.bisect_right(starts, value_node.start_mark.index) + offset
            )
            anchor_line = (
                bisect.bisect_right(starts, key_node.start_mark.index) + offset
            )
            reason = f'key {key!r} is an alias of the node at line {anchor_line}'
            issues.append((alias_line, reason, 'duplicate_key'))
            addressable = False
            continue
        # every node of the pair, nested ones included, may be aliased later;
        # a node reached twice within the pair is an alias, walked once
        subtree = [key_node, value_node]
        while subtree:
            node = subtree.pop()
            if id(node) in reached:
                continue
            reached.add(id(node))
            if isinstance(node, yaml.SequenceNode):
                subtree.extend(node.value)
            elif isinstance(node, yaml.MappingNode):
                for nested_key, nested_value in node.value:
                    subtree.extend((nested_key, nested_value))
        line = bisect.bisect_right(starts, key_node.start_mark.index) - 1 + offset
        file_line = line + 1
        # a merge key folds another mapping in, and two keys on one line (a
        # NEL, LS, or PS the parser breaks on) share the line: neither leaves
        # a key line of its own for the writers to address
        merge_key = scalar_key and (key_node.tag == 'tag:yaml.org,2002:merge')
        shared_line = bool(keys) and (keys[-1][1] == line)
        if merge_key or shared_line or (key_node.start_mark.column != 0):
            addressable = False
        keys.append((key, line))
        if not scalar_key:
            issues.append((file_line, 'a key is not a scalar', 'nonscalar_key'))
            kind = 'invalid'
            continue
        if key in seen:
            reason = f'duplicate key {key!r} (first at line {seen[key]})'
            issues.append((file_line, reason, 'duplicate_key'))
            continue
        seen[key] = file_line
        if isinstance(value_node, yaml.ScalarNode):
            # the C loader reports a plain style as '', the pure loader as None
            fields[key] = (value_node.value, value_node.style or None, value_node.tag)
            # a lone surrogate the pure loader lets through an escape is text no
            # writer can emit, so the block reads as the C loader rejects it
            if _SURROGATES.search(value_node.value):
                reason = 'found invalid Unicode character escape code'
                return 'invalid', {}, ((file_line, reason, 'parse'),), (), False
        else:
            fields[key] = None
    # an alias composes as the anchored node object reached a second time; a
    # duplicate key inside a nested mapping is a strict-reader finding too
    visited: set[int] = set()
    pending = [root]
    aliased = False
    while pending:
        node = pending.pop()
        if id(node) in visited:
            aliased = True
            continue
        visited.add(id(node))
        if isinstance(node, yaml.SequenceNode):
            pending.extend(node.value)
        elif isinstance(node, yaml.MappingNode):
            nested: set[str] = set()
            for key_node, value_node in node.value:
                pending.extend((key_node, value_node))
                if (node is root) or not isinstance(key_node, yaml.ScalarNode):
                    continue
                if key_node.value in nested:
                    nested_line = (
                        bisect.bisect_right(starts, key_node.start_mark.index) + offset
                    )
                    reason = f'duplicate key {key_node.value!r} in a nested mapping'
                    issues.append((nested_line, reason, 'duplicate_key'))
                nested.add(key_node.value)
    issues.sort(key=lambda issue: issue[0])
    return kind, fields, tuple(issues), tuple(keys) if addressable else (), aliased


def _parse_issue(error: Exception, body: str, offset: int) -> _Issue:
    """Locate and word a parser error as a strict-reader finding on the file's lines.

    A mark's character offset locates its line (a mark's own line count
    treats NEL, LS, and PS as line breaks); an error at the end of the
    stream, or one raised while scanning a key, points past the construct
    at fault, so its context mark names the offending line instead; an
    error without a mark (a character YAML forbids, the recursion limit)
    locates by the first forbidden character, else names the block's
    first line. A problem worded as a fragment ("but found another
    document") keeps the context it continues.
    """
    import yaml

    mark = getattr(error, 'problem_mark', None)
    context = getattr(error, 'context', None)
    context_mark = getattr(error, 'context_mark', None)
    if (mark is not None) and (context_mark is not None):
        past_end = mark.index >= len(body.rstrip('\n'))
        opened = str(context).startswith(
            ('while scanning a simple key', 'while parsing a flow')
        )
        if past_end or opened:
            mark = context_mark
    forbidden = yaml.reader.Reader.NON_PRINTABLE.search(body)
    reason = getattr(error, 'problem', None)
    if mark is not None:
        # a mark still past the end (an unclosed flow collection with no
        # context) names the last line holding content; one on a trailing
        # whitespace-only line (a tab there) stays on that line
        if mark.index < len(body.rstrip('\n')):
            index = mark.index
        else:
            index = len(body.rstrip())
        body_line = body.count('\n', 0, index)
        # a colon the parser finds inside a plain scalar it is still reading
        # points at the line where the scalar started: a `key:value` typo
        # above, whose lines are neither key lines, items, nor comments (the
        # blank lines between fold in) -- unless a key on the error line
        # itself opened the scalar
        if str(reason).startswith('mapping values are not allowed'):
            lines = body.split('\n')
            before = body[body.rfind('\n', 0, index) + 1 : index]
            if re.match(r'^(?:\S.*?)?:(?:[ \t]|$)', before) is None:
                above = body_line
                first = body_line
                while (above > 0) and _continues_a_scalar(lines[above - 1]):
                    above -= 1
                    if lines[above].strip():
                        first = above
                # a scalar under a key line is that key's value, so the colon
                # on the error line is the fault; one opening the block, or
                # following a comment or item, is a typo named on its first line
                keyed = (above > 0) and re.match(
                    r'^(?:\S.*?)?:(?:[ \t]|$)', lines[above - 1]
                )
                if not keyed:
                    body_line = first
        line = body_line + offset + 1
    elif forbidden is not None:
        line = body.count('\n', 0, forbidden.start()) + offset + 1
    else:
        line = 1
    if reason is None:
        reason = getattr(error, 'reason', None)
    if isinstance(error, RecursionError):
        reason = 'collections nested too deep to read'
    elif reason is None:
        reason = type(error).__name__
    elif context and str(reason).startswith('but '):
        reason = f'{context}, {reason}'
    elif (
        context and (str(reason) == 'second occurrence') and (context_mark is not None)
    ):
        # a duplicate anchor is worded as its context plus where the first is
        first = body.count('\n', 0, context_mark.index) + offset + 1
        reason = f'{str(context).split(";")[0]} (first at line {first})'
    return line, str(reason), 'parse'


def _continues_a_scalar(line: str) -> bool:
    """Return whether a body line can only be the inside of a plain scalar: a blank, or no key, item, comment, or marker."""
    if not line.strip():
        return True
    if line.startswith(('#', '-', '---', '...')):
        return False
    return re.match(r'^(?:\S.*?)?:(?:[ \t]|$)', line) is None


def _scalar_fields(frontmatter: str) -> Optional[_Fields]:
    """Return the block's scalar fields, or ``None`` to defer to the line grammar.

    A block the parser rejects and a non-scalar key read through the line
    grammar, so the reader never fails on input the writer tolerates; a
    body with no content, or one that is not a mapping, has no fields at
    all -- a ``key:`` spelled inside a list is list text.
    """
    kind, fields, _, _, _ = _compose_fields(frontmatter)
    if kind == 'mapping':
        return fields
    if kind in ('empty', 'nonmapping'):
        return {}
    return None


def _read_field_lines(frontmatter: str, key: str) -> Optional[str]:
    """Read a scalar frontmatter ``key`` with the line grammar.

    The fallback behind :func:`read_frontmatter_field` for a block a
    strict parser rejects. A plain ``key: value`` returns the stripped
    value, with one pair of matching surrounding YAML quotes stripped. A
    block scalar (``|``/``>`` with optional chomping/indentation
    indicators, e.g. ``|-``, ``>+``, ``|2``) resolves to its body: a
    literal ``|`` keeps line breaks, a folded ``>`` joins consecutive
    non-empty lines with a single space (a blank line is a paragraph
    break). Inline text on the indicator line (``key: > one liner.``) is
    taken as the value when no indented body follows. A bare ``key:``
    over an indented body is a plain multi-line scalar and folds the
    same way ``>`` does. Returns ``None`` if the field is absent; an
    empty block body resolves to an empty string.
    """
    # the key line: its colon followed by a space or the line end, as the
    # whole line grammar has it
    key_line = _key_pattern(key)
    match = re.search(rf'{key_line}[^\S\n]*(.*)$', frontmatter, re.MULTILINE)
    if match is None:
        return None
    # node properties are not the value
    value = re.sub(r'^(?:[&!]\S*(?:[ \t]+|$))+', '', match.group(1).strip())
    lines = frontmatter.split('\n')
    first = frontmatter.count('\n', 0, match.start()) + 1
    # a quoted value: a comment after it starts past its closing quote (a
    # quote mid-text, an apostrophe, is content); a quote closing on a later
    # line folds the lines up to it like a plain value's, stopping at the
    # next key line or the block's end
    if value.startswith(("'", '"')):
        close = _quote_close(value)
        if close != -1:
            return unquote(value[: close + 1])
        parts = [value]
        for line in lines[first : _end_line(lines)]:
            if _key_of(line) is not None:
                break
            text = line.strip()
            close = _quote_close(value[0] + text)
            if close != -1:
                parts.append(text[:close])
                break
            parts.append(text)
        return unquote(fold_lines('\n'.join(parts)))
    # a flow collection: its source text up to the closing bracket, a ' #'
    # inside its quotes included and the comment tails outside them dropped;
    # a bracket never closed reads to the next key line or the block's end,
    # where the line grammar places the lines
    if value[:1] in ('[', '{'):
        depth, quote = _flow_depth(value, 0)
        parts = [_uncommented(value)]
        for line in lines[first : _end_line(lines)]:
            if (depth == 0) or (_key_of(line) is not None):
                break
            parts.append(_uncommented(line, quote))
            depth, quote = _flow_depth(line, depth, quote)
        return ' '.join(part for part in parts if part)
    if not value.startswith(('|', '>')):
        # a plain value, on the key line, continued on indented lines, or under
        # a bare key -- whose column-0 items are its value, as the composed
        # path reads them: the lines fold per the YAML plain-scalar rule,
        # comment lines and ' #' tails (outside quotes) dropped; nothing left
        # reads as an absent value, and a bare key over a quoted body reads
        # the quoted scalar, as the key-line spelling does
        items = r'|-(?:[ \t].*)?\n' if not value else ''
        body_match = re.search(
            pattern=rf'{key_line}[^\S\n]*(.*)\n((?:[ \t]+.*\n|[ \t]*\n|#.*\n{items})*)',
            string=frontmatter,
            flags=re.MULTILINE,
        )
        body = body_match.group(2) if body_match else ''
        kept = []
        span = None  # the quote a line leaves open: its lines are content
        for line in (value, *body.split('\n')):
            if (span is None) and line.lstrip().startswith('#'):
                continue
            kept.append(_uncommented(line, span))
            _, span = _flow_depth(line, 0, span)
        text = '\n'.join(kept)
        if not text.strip():
            return None
        if not value and (text.lstrip()[:1] in ('"', "'")):
            # the close counts only at a line's end (a quote mid-text, an
            # apostrophe, is content), walked line by line as the key-line
            # quoted branch walks its continuations
            quote_lines = text.lstrip().split('\n')
            parts = []
            for index, quoted in enumerate(quote_lines):
                opened = quoted if index == 0 else quote_lines[0][0] + quoted
                close = _quote_close(opened)
                if close != -1:
                    parts.append(opened[: close + 1] if index == 0 else quoted[:close])
                    break
                parts.append(quoted)
            return unquote(fold_lines('\n'.join(parts)))
        return fold_lines(text)
    # block scalar: tolerate any header (node properties, chomping/indentation
    # indicators |- |+ >- |2 ...) plus trailing inline text, then capture the
    # indented body (blank lines inside the block are kept so a folded break
    # survives)
    match = re.search(
        pattern=rf'{key_line}[^\S\n]*(?:[&!]\S*[ \t]+)*([|>])([-+0-9]*)[^\S\n]*(.*)\n((?:[ \t]+.*\n|[ \t]*\n)*)',
        string=frontmatter,
        flags=re.MULTILINE,
    )
    if not match:
        return None
    indicator, header, inline, body = match.groups()
    # no indented body: the inline text on the header line is the value,
    # unless it is a comment
    if not body:
        return '' if inline.strip().startswith('#') else inline.strip()
    # the block ends at the first line indented less than its body -- the
    # explicit indentation indicator's depth, else the first content line's
    lines = body.split('\n')
    digit = re.search(r'[0-9]', header)
    content = [line for line in lines if line.strip()]
    if digit is not None:
        indent = int(digit.group())
    else:
        indent = len(content[0]) - len(content[0].lstrip()) if content else 0
    kept = []
    for line in lines:
        if line.strip() and (len(line) - len(line.lstrip()) < indent):
            break
        kept.append(line)
    body = textwrap.dedent('\n'.join(kept))
    # folded scalar (>): join non-empty lines with a space, blank line breaks
    if indicator == '>':
        return fold_lines(body)
    return body.strip()


def _closing_quote(value: str, start: int = 0) -> int:
    """Return the index of the quote closing the quoted scalar opening at ``start``, or ``-1`` when it never closes.

    A doubled ``''`` inside a single-quoted scalar and a backslash-escaped
    character inside a double-quoted one are content, not the close.
    """
    quote_char = value[start]
    index = start + 1
    while index < len(value):
        char = value[index]
        if (quote_char == '"') and (char == '\\'):
            index += 2
            continue
        if char == quote_char:
            if (quote_char == "'") and value[index + 1 : index + 2] == "'":
                index += 2
                continue
            return index
        index += 1
    return -1


def _flow_close(value: str, start: int = 0) -> int:
    """Return the index closing the quoted span opening at ``start`` of flow text, or ``-1``.

    Inside a flow collection a close is followed by the end, whitespace,
    ``,``, ``]``, ``}``, or ``:``; a quote followed by more text (an
    apostrophe in ``'Bob's #1 hit'``) is content, so the ``#`` after it
    never starts a comment.
    """
    index = start
    while True:
        close = _closing_quote(value, index)
        if close == -1:
            return -1
        if value[close + 1 : close + 2] in ('', ' ', '\t', ',', ']', '}', ':'):
            return close
        index = close


def _quote_close(value: str) -> int:
    """Return the index of the quote closing the scalar opening ``value``, or ``-1``.

    A candidate close counts only when nothing but whitespace or a
    ``' #'`` comment follows it on the line -- no reading closes a quoted
    scalar mid-text, so the apostrophe in ``'Bob's Page'`` is content and
    the final quote is the close. Doubled ``''`` and backslash escapes
    are content, as in :func:`_closing_quote`.
    """
    quote_char = value[0]
    index = 1
    close = -1
    while index < len(value):
        char = value[index]
        if (quote_char == '"') and (char == '\\'):
            index += 2
            continue
        if char == quote_char:
            if (quote_char == "'") and value[index + 1 : index + 2] == "'":
                index += 2
                continue
            if re.fullmatch(r'[ \t]*(?:#.*)?', value[index + 1 :]):
                close = index
        index += 1
    return close


def _is_unset_field(frontmatter: str, key: str) -> bool:
    """Return whether ``key`` is absent or carries the plain ``null`` reset idiom.

    A plain lowercase ``null`` -- with or without a trailing comment --
    is the one reset spelling; a quoted or block-scalar ``null`` is
    authored text. Under the line grammar the check is the repair's own
    valueless test on the raw field, since unquoting first would
    collapse an authored ``'null'`` into the idiom.
    """
    fields = _scalar_fields(frontmatter)
    if fields is not None:
        if key not in fields:
            return True
        entry = fields[key]
        if entry is None:
            return False
        value, style, tag = entry
        # plain and resolved as null: an explicit !!str tag makes it text
        return (value == 'null') and (style is None) and tag.endswith(':null')
    text = field_text(frontmatter, key)
    if text is None:
        return True
    indicator, _, body = text.partition('\n')
    return _is_valueless(indicator, body, nulls=('', 'null'))


def _is_plain_safe(value: str) -> bool:
    """Return whether a strict YAML reader reads ``value`` back verbatim when plain.

    A value containing ``': '`` (or ending with ``:``) reads as a nested
    mapping, one containing ``' #'`` loses everything from the hash (a
    comment), one opening with an indicator character or with ``'- '``,
    ``'? '``, or ``': '`` reads as structure, a node property, or a quoted
    scalar, and leading or trailing whitespace (a tab anywhere) is dropped
    or rejected -- so none of them may be written plain.
    """
    if not value:
        return True
    if value != value.strip():
        return False
    if (': ' in value) or value.endswith(':') or (' #' in value) or ('\t' in value):
        return False
    if _ESCAPED_CHARS.search(value):
        return False
    if value[0] in _INDICATOR_CHARS:
        return False
    if (value in ('-', '?', ':', '<<', '=')) or (value[:2] in ('- ', '? ', ': ')):
        return False
    # a value a strict reader resolves as a typed scalar and then cannot
    # construct -- a date no calendar has (2024-02-30), a numeral of
    # underscores alone (0x_) -- is one it rejects
    import yaml

    resolved = yaml.resolver.Resolver().resolve(yaml.ScalarNode, value, (True, False))
    if resolved != 'tag:yaml.org,2002:str':
        try:
            yaml.safe_load(f'k: {value}\n')
        except (yaml.YAMLError, ValueError):
            return False
    return True


def _escape(match: re.Match[str]) -> str:
    """Spell one character no stream may carry as its double-quoted escape."""
    char = match.group(0)
    if char in _SHORT_ESCAPES:
        return f'\\{_SHORT_ESCAPES[char]}'
    return f'\\u{ord(char):04x}'


def _unescape(match: re.Match[str]) -> str:
    """Resolve one double-quoted YAML escape; an unknown one stays verbatim.

    A code point past U+10FFFF or inside the surrogate range is no
    character, so its escape stays verbatim too, as libyaml reads it.
    """
    wide, code, byte, char = match.groups()
    if wide or code or byte:
        code_point = int(wide or code or byte, 16)
        if (code_point > 0x10FFFF) or (0xD800 <= code_point <= 0xDFFF):
            return match.group(0)
        return chr(code_point)
    return _ESCAPES.get(char, f'\\{char}')
