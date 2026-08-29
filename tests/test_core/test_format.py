"""Test the ``wiki.core.format`` module.

Functions over the on-disk page format. The behavior-named core suites
(``test_authoring``, ``test_update``, ``test_lint``) drive them through
the engine; this module holds the differential oracle harness, which
drives the frontmatter reader, repair, and writer directly over an
enumerated grammar of scalar shapes (see ``_oracle``) and judges them
against a strict YAML reader: every value the wiki reads is the value a
strict reader sees, every repair leaves authored values as the strict
reader saw them, and every value the writer quotes reads back verbatim.
"""

from __future__ import annotations

import pathlib
from typing import Optional

import pytest
import yaml

from wiki.core import format

from ._helpers import _make_wiki
from ._oracle import (
    EXTRAS,
    KEYS,
    NAME,
    NOW,
    SEQUENCES,
    block,
    frontmatter,
    grammar,
    normalize,
    oracle_extent,
    oracle_mapping,
    oracle_scalar,
    oracle_valid,
    yaml_body,
)

__all__ = [
    'test_plain_multiline_desc_propagates',
    'test_reader_matches_strict_yaml',
    'test_reader_matches_strict_yaml_on_hostile_shapes',
    'test_repair_keeps_authored_values',
    'test_repair_recognizes_a_spaced_key',
    'test_repair_keeps_column_zero_sequences',
    'test_repair_keeps_the_comments_of_valueless_fields',
    'test_reader_applies_the_documented_policy',
    'test_reader_reads_a_carriage_return_escape_as_a_line_break',
    'test_title_reader_applies_the_null_idiom',
    'test_quote_round_trips_through_yaml',
    'test_quote_escapes_characters_no_stream_may_carry',
    'test_field_ranges_match_yaml_extents',
    'test_field_ranges_match_yaml_extents_on_hostile_shapes',
]

# ------ engine cover


def test_plain_multiline_desc_propagates(tmp_path: pathlib.Path) -> None:
    """A bare ``desc:`` over an indented body reads as a plain scalar.

    YAML folds a plain multi-line scalar the way ``>`` does; the reader
    must resolve it the same way so the authored desc propagates to the
    parent index instead of silently reading as absent.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})

    # author a child page whose desc is a bare key over an indented body
    page = tmp_path / 'core' / 'design.md'
    page.write_text(
        '---\nname: design\ndesc:\n  A plain multi-line\n  scalar value.\n'
        '---\n\n# design\n\nBody.\n',
        encoding='utf-8',
    )

    # the parent index picks up the folded desc and the tree converges
    wiki.update()
    core_index = (tmp_path / 'core' / '_index.md').read_text(encoding='utf-8')
    assert 'A plain multi-line scalar value.' in core_index
    assert wiki.update() == []


# ------ differential oracle

_shapes = pytest.mark.parametrize(
    argnames=('style', 'body', 'deco'),
    argvalues=grammar(),
    ids=['-'.join(shape) for shape in grammar()],
)


@_shapes
def test_reader_matches_strict_yaml(style: str, body: str, deco: str) -> None:
    """Every key reads exactly the value a strict YAML reader sees, for every shape."""
    for key in KEYS:
        text = frontmatter(key, style, body, deco)
        expected = oracle_scalar(yaml_body(text), key)
        actual = format.read_frontmatter_field(text, key)
        assert normalize(actual) == normalize(expected), (key, text)


@pytest.mark.parametrize(
    argnames=('key', 'lines'),
    argvalues=[(key, lines) for _, key, lines in EXTRAS],
    ids=[extra_id for extra_id, _, _ in EXTRAS],
)
def test_reader_matches_strict_yaml_on_hostile_shapes(
    key: str, lines: list[str]
) -> None:
    """Valid shapes off the grammar's axes read as a strict reader reads them.

    Comments on the key line, quoted and block openers under a bare key,
    end-of-line comments, escaped line breaks, a space before the colon,
    and node properties are the shapes hand-derived reading rules miss
    first; each must read as the composed scalar, never as raw line text.
    """
    text = block(key, lines)
    expected = oracle_scalar(yaml_body(text), key)
    assert normalize(format.read_frontmatter_field(text, key)) == normalize(expected)


@_shapes
def test_repair_keeps_authored_values(style: str, body: str, deco: str) -> None:
    """The repair converges, stays valid YAML, and rewrites only the name.

    A stamp continued on an indented line is a value, so ``created`` and
    ``updated`` must survive the repair like every authored field; a
    ``#`` line the strict reader does not fold into a value is a comment
    and must survive too.
    """
    for key in KEYS:
        text = frontmatter(key, style, body, deco)
        # the engine's exact call shape, applied twice
        repaired = format.repair_frontmatter(
            text,
            name=NAME,
            now=NOW,
            title=True,
            category=True,
            order=True,
        )
        again = format.repair_frontmatter(
            repaired,
            name=NAME,
            now=NOW,
            title=True,
            category=True,
            order=True,
        )
        assert again == repaired, (key, text)
        # the output is valid for a strict reader
        assert oracle_valid(yaml_body(repaired)), (key, text, repaired)
        # a strict reader loads exactly the refreshed name -- never a stranded
        # continuation folded into it -- and the same authored mapping
        before = oracle_mapping(yaml_body(text))
        after = oracle_mapping(yaml_body(repaired))
        # a comment line survives the repair (a hash line inside a block
        # scalar's body is content, which the name refresh may replace)
        values = ' '.join(map(str, before.values()))
        for line in text.split('\n'):
            if line.lstrip().startswith('#') and line.strip() not in values:
                assert line in repaired, (key, text, repaired)
        assert after.pop('name') == NAME, (key, text, repaired)
        before.pop('name')
        assert after == before, (key, text, repaired)


def test_repair_recognizes_a_spaced_key() -> None:
    """A key with a space before its colon is the key it names, not a stranger.

    ``desc : x`` is the ``desc`` field to every YAML reader; a repair that
    saw no ``desc:`` would insert the placeholder beside it, writing a
    duplicate key that strict readers reject.
    """
    text = block('desc', ['desc : A page.'])
    repaired = format.repair_frontmatter(
        text,
        name=NAME,
        now=NOW,
        title=True,
        category=True,
        order=True,
    )
    assert oracle_valid(yaml_body(repaired)), repaired
    assert repaired.count('desc') == 1
    assert 'desc : A page.' in repaired


@pytest.mark.parametrize('key', ['desc', 'category', 'created'])
def test_repair_keeps_column_zero_sequences(key: str) -> None:
    """A column-0 sequence under a repaired key is its value, kept whole.

    YAML reads ``key:`` over ``- a`` items as a sequence; a repair that saw
    only indented lines as the value would take the key line for a blank,
    restore a placeholder or stamp over it, and strand the items under the
    field before it -- turning valid YAML into a block no reader accepts.
    """
    text = block(key, [f'{key}:', '- a', '- b'])
    repaired = format.repair_frontmatter(
        text,
        name=NAME,
        now=NOW,
        title=True,
        category=True,
        order=True,
    )
    assert oracle_valid(yaml_body(repaired)), repaired
    assert oracle_mapping(yaml_body(repaired))[key] == ['a', 'b']


def test_repair_keeps_the_comments_of_valueless_fields() -> None:
    """Comments on a field the repair fills or removes survive the edit.

    A comment-only ``desc:`` or stamp reads as empty, so the repair fills
    it -- but the annotation the author wrote is not the repair's to
    delete: a tail comment re-attaches after the written value, an
    indented comment line stays under it, and the comment on an unset
    ``title:`` stands alone once the line goes.
    """
    text = (
        '---\n'
        f'name: {NAME}\n'
        'title: # TODO pick one\n'
        'desc: # to be written\n'
        'created: # stamped at birth\n'
        'updated:\n'
        '  # the last write\n'
        '---'
    )
    repaired = format.repair_frontmatter(
        text,
        name=NAME,
        now=NOW,
        title=True,
        category=True,
        order=True,
    )
    assert oracle_valid(yaml_body(repaired)), repaired
    assert repaired == (
        '---\n'
        f'name: {NAME}\n'
        '# TODO pick one\n'
        'desc: ... # to be written\n'
        f'created: {NOW} # stamped at birth\n'
        f'updated: {NOW}\n'
        '  # the last write\n'
        '---'
    )
    mapping = oracle_mapping(yaml_body(repaired))
    assert mapping['desc'] == '...'
    assert 'title' not in mapping


# ------ documented policy


@pytest.mark.parametrize(
    argnames=('field', 'key', 'expected'),
    argvalues=[
        # the placeholder is read as its spelling, quoted or not
        ('desc: ...', 'desc', '...'),
        ("desc: '...'", 'desc', '...'),
        # a valueless key is absent, an empty block scalar is empty text
        ('desc:', 'desc', None),
        ('desc:   ', 'desc', None),
        ('desc: # to be written', 'desc', None),
        ('desc: |', 'desc', ''),
        ('desc: | # note', 'desc', ''),
        # typed-looking scalars stay strings
        ('title: 2024', 'title', '2024'),
        ('title: 1.10', 'title', '1.10'),
        ('title: yes', 'title', 'yes'),
        ('title: 2026-01-01', 'title', '2026-01-01'),
        ('created: 2026-07-10T02:36:41Z', 'created', '2026-07-10T02:36:41Z'),
        # ' #' starts a comment: the text after it reaches no reader
        ('desc: Use #1 approach.', 'desc', 'Use'),
        # sequences are never resolved: the raw flow text, or absent at column 0
        ('tags: [a, b]', 'tags', '[a, b]'),
        ('tags:\n- a\n- b', 'tags', None),
        # the first occurrence of a duplicated key wins, bare or not
        ('desc:\ndesc: second', 'desc', None),
        ('desc: first\ndesc: second', 'desc', 'first'),
        # tolerated invalid YAML reads through the line grammar as authored
        ('desc: A theorem for X: HR, WR.', 'desc', 'A theorem for X: HR, WR.'),
        ('desc: > one liner.', 'desc', 'one liner.'),
        ('desc: "unterminated\n  continues', 'desc', '"unterminated'),
        (
            '<<<<<<< ours\ndesc: Ours.\n=======\ndesc: Theirs.\n>>>>>>> theirs',
            'desc',
            'Ours.',
        ),
        # one invalid line sends every field of the block through the line grammar
        ('desc: A theorem for X: HR, WR.\ncategory: |\n  Cat', 'category', 'Cat'),
    ],
    ids=[
        'placeholder',
        'quoted-placeholder',
        'bare-key',
        'bare-key-trailing-spaces',
        'keyline-comment-no-body',
        'empty-block',
        'empty-block-header-comment',
        'int-like',
        'float-like',
        'bool-like',
        'date-like',
        'timestamp',
        'hash-in-value',
        'flow-sequence',
        'block-sequence',
        'duplicate-bare-first',
        'duplicate-valued-first',
        'unquoted-colon-space',
        'indicator-inline-text',
        'unterminated-quote',
        'conflict-markers',
        'invalid-neighbor',
    ],
)
def test_reader_applies_the_documented_policy(
    field: str,
    key: str,
    expected: Optional[str],
) -> None:
    """Where the wiki departs from strict YAML on purpose, the departure is pinned."""
    text = f'---\nname: {NAME}\n{field}\n---'
    assert format.read_frontmatter_field(text, key) == expected


def test_reader_reads_a_carriage_return_escape_as_a_line_break() -> None:
    """A double-quoted carriage-return escape reads as a line break, never a bare return.

    Every consumer splits on newlines; a bare carriage return inside a
    title or desc would ride into the H1 and the parent row and split the
    line model on the next read, so update would never converge.
    """
    backslash = chr(92)
    text = f'---\nname: {NAME}\ndesc: "a{backslash}rb"\ntitle: "c{backslash}rd"\n---'
    assert format.read_frontmatter_field(text, 'desc') == 'a\nb'
    assert format.read_frontmatter_title(text) == 'c d'


@pytest.mark.parametrize(
    argnames=('field', 'expected'),
    argvalues=[
        ('title: null', ''),
        ('title: null # why', ''),
        ('title:', ''),
        ('title: ~', '~'),
        ('title: Null', 'Null'),
        ('title: NULL', 'NULL'),
        ("title: 'null'", 'null'),
        ('title: !!str null', 'null'),
        ('title: |\n  null', 'null'),
        ('title: Draft\n  heading', 'Draft heading'),
        ('title: |\n  Two\n  lines', 'Two lines'),
    ],
    ids=[
        'null',
        'null-comment',
        'bare-key',
        'tilde',
        'Null',
        'NULL',
        'quoted-null',
        'tagged-null',
        'block-null',
        'continuation',
        'block-lines',
    ],
)
def test_title_reader_applies_the_null_idiom(field: str, expected: str) -> None:
    """Only the plain lowercase ``null`` unsets a title; every other spelling is text.

    The heading renders on one line, so a multi-line title joins; a
    trailing comment after ``null`` is not part of the spelling.
    """
    text = f'---\nname: {NAME}\n{field}\n---'
    assert format.read_frontmatter_title(text) == expected


# ------ writer twin


@pytest.mark.parametrize(
    argnames='value',
    argvalues=[
        'plain text',
        'A: colon inside',
        'ends with colon:',
        'Notes on issue #3 and the fix.',
        '#hashtag',
        '[Draft] X',
        '{y}',
        '- item',
        '? question',
        '@handle',
        '`cmd`',
        '%percent',
        '!tag',
        '&anchor',
        '*alias',
        "'single-quoted'",
        '"double-quoted"',
        "it's",
        '|pipe',
        '>gt',
        ' padded ',
        '',
    ],
)
def test_quote_round_trips_through_yaml(value: str) -> None:
    """A written value reads back verbatim under a strict reader and under ``unquote``.

    The writer is the reader's twin: every value the tool writes plain
    must be one a strict reader (Obsidian's included) reads back as the
    same text, or an adopted heading is rewritten on the next update.
    """
    node = yaml.compose(f'k: {format.quote(value)}\n', Loader=yaml.SafeLoader)
    assert node.value[0][1].value == value
    assert format.unquote(format.quote(value)) == value


@pytest.mark.parametrize(
    argnames='value',
    argvalues=[
        'line' + chr(0x2028) + 'separator',
        'para' + chr(0x2029) + 'separator',
        'next' + chr(0x85) + 'line',
        'bell' + chr(7) + 'rings',
        'carriage' + chr(13) + 'return',
        'quoted "and' + chr(1) + 'escaped\\',
    ],
    ids=['line-separator', 'paragraph-separator', 'nel', 'bell', 'cr', 'mixed'],
)
def test_quote_escapes_characters_no_stream_may_carry(value: str) -> None:
    """A value holding a character YAML forbids in a stream is written escaped.

    Plain or single-quoted, such a character makes the whole block
    invalid (or folds as a line break); double-quoted with an escape it
    reads back verbatim under a strict reader and under ``unquote``.
    """
    written = format.quote(value)
    assert written[0] == written[-1] == '"'
    node = yaml.compose(f'k: {written}\n', Loader=yaml.SafeLoader)
    assert node.value[0][1].value == value
    assert format.unquote(written) == value


# ------ field extents


@_shapes
def test_field_ranges_match_yaml_extents(style: str, body: str, deco: str) -> None:
    """``field_line_ranges`` attributes lines the way a strict reader's key marks do.

    The line ranges scope ``match --field`` and the wrap-mangle lint, so
    a field that ends early (a continuation misread as a key) or late (a
    key misread as a continuation) hides or misattributes matches.
    """
    for key in KEYS:
        text = frontmatter(key, style, body, deco)
        ranges = format.field_line_ranges(text, text.split('\n'), [key])
        assert ranges == oracle_extent(yaml_body(text), key), (key, text)


@pytest.mark.parametrize(
    argnames=('key', 'lines'),
    argvalues=[(key, lines) for _, key, lines in EXTRAS + SEQUENCES],
    ids=[extra_id for extra_id, _, _ in EXTRAS + SEQUENCES],
)
def test_field_ranges_match_yaml_extents_on_hostile_shapes(
    key: str,
    lines: list[str],
) -> None:
    """Off-grammar shapes -- a column-0 item holding a colon, a spaced key -- scope right."""
    text = block(key, lines)
    ranges = format.field_line_ranges(text, text.split('\n'), [key])
    assert ranges == oracle_extent(yaml_body(text), key)
