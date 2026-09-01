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
import re
from collections.abc import Callable
from typing import Optional

import pytest

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
    oracle_scalars,
    oracle_valid,
    yaml_body,
)

__all__ = [
    'test_plain_multiline_desc_propagates',
    'test_reader_matches_strict_yaml',
    'test_reader_matches_strict_yaml_on_hostile_shapes',
    'test_repair_keeps_authored_values',
    'test_repair_keeps_authored_values_on_hostile_shapes',
    'test_repair_recognizes_a_spaced_key',
    'test_repair_keeps_column_zero_sequences',
    'test_repair_keeps_the_comments_of_valueless_fields',
    'test_repair_leaves_removed_field_comments_at_column_zero',
    'test_repair_removes_every_leading_valueless_copy',
    'test_repair_keeps_an_aliased_block_in_its_order',
    'test_repair_inserts_above_a_document_end_marker',
    'test_repair_fills_a_tagged_empty_value',
    'test_repair_keeps_a_keep_chomping_item_blank',
    'test_repair_keeps_the_name_tail_comment',
    'test_restamp_keeps_a_quoted_stamp_whole',
    'test_frontmatter_issues_word_a_second_document_as_a_sentence',
    'test_frontmatter_issues_locate_an_unclosed_flow_collection',
    'test_frontmatter_issues_name_a_nested_duplicate_key',
    'test_unaddressable_blocks_are_refused_not_crashed',
    'test_strip_blank_lines_keeps_content_blanks_at_any_depth',
    'test_fallback_repair_sees_a_quoted_key',
    'test_colon_error_names_the_line_the_scalar_started_on',
    'test_compose_walks_an_alias_graph_once',
    'test_compose_refuses_nesting_past_the_bound',
    'test_tab_on_the_last_block_line_is_named',
    'test_repair_keeps_an_item_after_a_valued_key_in_place',
    'test_repair_fills_an_empty_block_over_column_zero_comments',
    'test_field_ranges_end_at_a_typo_line_under_the_line_grammar',
    'test_build_frontmatter_escapes_a_multi_line_desc',
    'test_build_frontmatter_pins_a_padded_first_line',
    'test_field_ranges_end_at_a_foreign_key_under_the_line_grammar',
    'test_repair_breaks_frontmatter_names_a_breaking_repair',
    'test_reader_applies_the_documented_policy',
    'test_reader_reads_a_carriage_return_escape_as_a_line_break',
    'test_fallback_reader_agrees_with_the_repair',
    'test_title_reader_applies_the_null_idiom',
    'test_quote_round_trips_through_yaml',
    'test_quote_escapes_characters_no_stream_may_carry',
    'test_unquote_decodes_every_double_quoted_escape',
    'test_field_value_strips_keys_quotes_and_comments',
    'test_line_keys_names_composed_key_lines',
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
    key: str,
    lines: list[str],
) -> None:
    """Valid shapes off the grammar's axes read as a strict reader reads them.

    Comments on the key line, quoted and block openers under a bare key,
    end-of-line comments, escaped line breaks, a space before the colon,
    and node properties are the shapes hand-derived reading rules miss
    first; each must read as the composed scalar, never as raw line text.
    """
    text = block(key, lines)
    expected = oracle_scalar(yaml_body(text), key)
    actual = format.read_frontmatter_field(text, key)
    assert normalize(actual) == normalize(expected)


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
            if line.lstrip().startswith('#') and (line.strip() not in values):
                assert line in repaired, (key, text, repaired)
        assert after.pop('name') == NAME, (key, text, repaired)
        before.pop('name')
        assert after == before, (key, text, repaired)


@pytest.mark.parametrize(
    argnames=('key', 'lines'),
    argvalues=[(key, lines) for _, key, lines in EXTRAS],
    ids=[extra_id for extra_id, _, _ in EXTRAS],
)
def test_repair_keeps_authored_values_on_hostile_shapes(
    key: str,
    lines: list[str],
) -> None:
    """Valid shapes off the grammar's axes survive the repair as a strict reader saw them.

    A quoted key is the field it names, a quoted scalar continued at
    column 0 is one value, an alias keeps its anchor above it, and a
    column-0 comment inside a field stays where it is: the repair rewrites
    only the name, converges, and leaves a block the installed strict
    reader accepts -- with the same authored mapping, minus the valueless
    title or category it drops.
    """
    text = block(key, lines)
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
    assert again == repaired, repaired
    assert oracle_valid(yaml_body(repaired)), repaired
    before = oracle_scalars(yaml_body(text))
    after = oracle_scalars(yaml_body(repaired))
    assert after.pop('name') == NAME
    before.pop('name')
    # a valueless title or category is dropped, never rewritten
    for unset in ('title', 'category'):
        if before.get(unset) in (None, '', 'null'):
            before.pop(unset, None)
            assert unset not in after
    assert after == before, repaired


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
@pytest.mark.parametrize('neighbor', ['', 'zz: A: b\n'], ids=['composed', 'fallback'])
def test_repair_keeps_column_zero_sequences(key: str, neighbor: str) -> None:
    """A column-0 sequence under a repaired key is its value, kept whole.

    YAML reads ``key:`` over ``- a`` items as a sequence; a repair that saw
    only indented lines as the value would take the key line for a blank,
    restore a placeholder or stamp over it, and strand the items under the
    field before it -- turning valid YAML into a block no reader accepts.
    The line grammar's extent carries the items too, so a block one
    invalid neighbor sends through it repairs the same way.
    """
    text = block(key, [f'{key}:', '- a', '- b']).replace('---\n', f'---\n{neighbor}', 1)
    repaired = format.repair_frontmatter(
        text,
        name=NAME,
        now=NOW,
        title=True,
        category=True,
        order=True,
    )
    assert f'{key}:\n- a\n- b\n' in repaired
    if not neighbor:
        assert oracle_valid(yaml_body(repaired)), repaired
        mapping = oracle_mapping(yaml_body(repaired))
        assert mapping[key] == ['a', 'b']


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


def test_repair_leaves_removed_field_comments_at_column_zero() -> None:
    """The comments of a removed field land at column 0, a comment after every neighbor.

    Indented under a block-scalar desc they would be its content, so the
    desc a strict reader loads must not change when the field goes.
    """
    text = (
        '---\n'
        f'name: {NAME}\n'
        'desc: |\n'
        '  Alpha beta.\n'
        'category:\n'
        '  # unset for now\n'
        'title: null # pick one\n'
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
    assert '\n# unset for now\n# pick one\n' in repaired
    mapping = oracle_mapping(yaml_body(repaired))
    assert mapping['desc'] == 'Alpha beta.\n'
    assert 'category' not in mapping
    assert 'title' not in mapping


def test_repair_removes_every_leading_valueless_copy() -> None:
    """Every valueless copy before the first real ``title:`` goes in one pass.

    The first occurrence wins for every reader, so the repair keeps
    removing until the field it leaves in front carries a value; a
    one-copy-per-run removal would take a run per copy to converge.
    """
    text = block('title', ['title:', 'title: null', 'title: Real'])
    repaired = format.repair_frontmatter(
        text,
        name=NAME,
        now=NOW,
        title=True,
        category=True,
        order=True,
    )
    assert oracle_valid(yaml_body(repaired)), repaired
    assert repaired.count('title') == 1
    assert format.read_frontmatter_title(repaired) == 'Real'


def test_repair_keeps_an_aliased_block_in_its_order() -> None:
    """A block carrying an alias is not reordered: the anchor must stay above the alias."""
    text = block('desc', ['zz: &a Alpha beta.', 'desc: *a'])
    repaired = format.repair_frontmatter(
        text,
        name=NAME,
        now=NOW,
        title=True,
        category=True,
        order=True,
    )
    assert oracle_valid(yaml_body(repaired)), repaired
    assert repaired.index('zz: &a') < repaired.index('desc: *a')
    assert format.read_frontmatter_desc(repaired) == 'Alpha beta.'


@pytest.mark.parametrize(
    argnames='epilogue',
    argvalues=['...', '... # end', '...\n# trailing'],
    ids=['marker', 'marker-comment', 'marker-then-comment'],
)
def test_repair_inserts_above_a_document_end_marker(epilogue: str) -> None:
    """A trailing ``...`` marker (a comment with or after it) stays the block's last lines."""
    text = f'---\ndesc: A page.\nzz: 1\n{epilogue}\n---'
    repaired = format.repair_frontmatter(
        text,
        name=NAME,
        now=NOW,
        title=True,
        category=True,
        order=True,
    )
    assert oracle_valid(yaml_body(repaired)), repaired
    assert repaired.endswith(f'updated: {NOW}\n{epilogue}\n---')
    scalars = oracle_scalars(yaml_body(repaired))
    assert scalars['name'] == NAME
    assert scalars['created'] == NOW


@pytest.mark.parametrize(
    argnames=('field', 'expected'),
    argvalues=[
        ('created: !!str', f'created: !!str {NOW}'),
        ('created: &c !!str # when', f'created: &c !!str {NOW} # when'),
        ('desc: !!null', 'desc: !!null ...'),
        ('desc: &d', 'desc: &d ...'),
        ('title: !!str null', 'title: !!str null'),
        ('title: !!str', ''),
    ],
    ids=[
        'tagged-stamp',
        'anchored-tagged-stamp',
        'tagged-desc',
        'anchored-desc',
        'tagged-text',
        'tagged-empty-title',
    ],
)
def test_repair_fills_a_tagged_empty_value(field: str, expected: str) -> None:
    """A node property over nothing is a blank the repair fills, keeping the property.

    ``!!str`` alone tags an empty string and ``&c`` alone anchors a null,
    so the stamp is filled and the placeholder restored behind the
    property; a tag over a spelled value types it as text, so
    ``title: !!str null`` is the word and stays.
    """
    text = block(field.split(':')[0], [field])
    repaired = format.repair_frontmatter(
        text,
        name=NAME,
        now=NOW,
        title=True,
        category=True,
        order=True,
    )
    assert oracle_valid(yaml_body(repaired)), repaired
    if expected:
        assert f'{expected}\n' in repaired
    else:
        assert 'title' not in repaired


def test_repair_keeps_a_keep_chomping_item_blank() -> None:
    """A ``- |+`` item at column 0 keeps its trailing blank line, which is its content."""
    text = block('sources', ['sources:', '- |+', '  a', '', '- b'])
    repaired = format.repair_frontmatter(
        text,
        name=NAME,
        now=NOW,
        title=True,
        category=True,
        order=True,
    )
    assert oracle_valid(yaml_body(repaired)), repaired
    mapping = oracle_mapping(yaml_body(repaired))
    assert mapping['sources'] == ['a\n\n', 'b']


def test_repair_keeps_the_name_tail_comment() -> None:
    """The name refresh keeps a comment after the key line's value, as every other field does."""
    text = block('name', ['name: stale # tail'])
    repaired = format.repair_frontmatter(
        text,
        name=NAME,
        now=NOW,
        title=True,
        category=True,
        order=True,
    )
    assert f'name: {NAME} # tail\n' in repaired
    assert oracle_scalars(yaml_body(repaired))['name'] == NAME


def test_restamp_keeps_a_quoted_stamp_whole() -> None:
    """A ``#`` inside a quoted stamp is text, never a tail comment to re-attach."""
    text = "---\nname: x\nupdated: '2026-08-29 #241'\n---"
    restamped = format.restamp_updated(text, NOW)
    assert restamped == f'---\nname: x\nupdated: {NOW}\n---'
    text = "---\nname: x\nupdated: '2026 #1' # by hand\n---"
    assert (
        format.restamp_updated(text, NOW)
        == f'---\nname: x\nupdated: {NOW} # by hand\n---'
    )
    text = "---\nname: x\nupdated: !!str '2026 #243'\n---"
    assert (
        format.restamp_updated(text, NOW) == f'---\nname: x\nupdated: !!str {NOW}\n---'
    )


def test_frontmatter_issues_word_a_second_document_as_a_sentence() -> None:
    """A parser problem worded as a fragment keeps the context it continues."""
    text = f'---\nname: {NAME}\n...\n---\ndesc: A.\n---'
    issues = format.frontmatter_issues(text)
    assert [reason for _, reason, _ in issues] == [
        'expected a single document in the stream, but found another document'
    ]


@pytest.mark.parametrize(
    argnames='tail',
    argvalues=['', '\ncreated: 2024-01-01T00:00:00Z'],
    ids=['last-line', 'followed-by-a-key'],
)
def test_frontmatter_issues_locate_an_unclosed_flow_collection(tail: str) -> None:
    """An unclosed flow collection is reported where it opens, not where the parser gave up."""
    text = f'---\nname: {NAME}\ndesc: Fine.\nsources: [{tail}\n---'
    issues = format.frontmatter_issues(text)
    assert [line for line, _, _ in issues] == [4]


@pytest.mark.parametrize(
    argnames=('body', 'expected'),
    argvalues=[
        (
            'meta:\n  a: 1\n  a: 2',
            [(5, "duplicate key 'a' in a nested mapping", 'duplicate_key')],
        ),
        (
            'x:\n  a: 1\n  a: 2\ny:\n  b: 1\n  b: 2',
            [
                (5, "duplicate key 'a' in a nested mapping", 'duplicate_key'),
                (8, "duplicate key 'b' in a nested mapping", 'duplicate_key'),
            ],
        ),
        (
            '&k desc: First.\n*k : Second.',
            [(4, "key 'desc' is an alias of the node at line 3", 'duplicate_key')],
        ),
        (
            'tags: [&a x]\n*a : y',
            [(4, "key 'x' is an alias of the node at line 3", 'duplicate_key')],
        ),
        (
            'desc: &x A.\ntitle: &x B.',
            [(4, 'found duplicate anchor (first at line 3)', 'parse')],
        ),
        (
            'title:T\ndesc: A.',
            [(3, "could not find expected ':'", 'parse')],
        ),
    ],
    ids=[
        'nested',
        'two-nested-in-order',
        'alias-key',
        'alias-of-nested',
        'duplicate-anchor',
        'typo-line',
    ],
)
def test_frontmatter_issues_name_a_nested_duplicate_key(
    body: str, expected: list
) -> None:
    """Strict-reader findings name the offending line and word the cause in full.

    A key repeated inside a nested mapping, an alias used as a key (of a
    top-level or nested anchor), a duplicate anchor, and a ``key:value``
    typo the parser only trips over on the next line are each reported
    on their own line, in line order.
    """
    text = f'---\nname: {NAME}\n{body}\n---'
    issues = format.frontmatter_issues(text)
    assert [(line, reason, cause) for line, reason, cause in issues] == expected


@pytest.mark.parametrize(
    argnames='body',
    argvalues=[
        '&k desc: First.\n*k : Second.',
        '<<: {desc: Merged.}\ntags: []',
        'desc: foo' + chr(0x85) + 'title: bar',
        chr(0xFEFF) + 'desc: A page.',
    ],
    ids=['alias-key', 'merge-key', 'two-keys-one-line', 'body-bom'],
)
def test_unaddressable_blocks_are_refused_not_crashed(body: str) -> None:
    """A mapping whose key lines the writers cannot place is refused, never mis-spliced.

    An alias used as a key has no position of its own, a merge key folds
    another mapping in, and two keys on one line (a NEL the parser breaks
    on) share the line: each is read through the parser and refused by the
    repair. A BOM opening the body shifts one loader's marks, so it is
    dropped and both loaders address one stream. None is a crash or an
    empty span.
    """
    if body.startswith(chr(0xFEFF)):
        # the BOM opens the body: both loaders must place name on line 1
        text = f'---\n{body}\nname: {NAME}\n---'
        assert not format.is_unaddressable_frontmatter(text)
        assert format.read_frontmatter_desc(text) == 'A page.'
        assert format.field_text(text, 'name') == f'name: {NAME}\n'
    else:
        text = f'---\nname: {NAME}\n{body}\n---'
        assert format.is_unaddressable_frontmatter(text)
        assert format.read_frontmatter_name(text) == NAME


@pytest.mark.parametrize(
    argnames=('text', 'expected'),
    argvalues=[
        ('sources:\n- k: |+\n    text\n\n- b', 'sources:\n- k: |+\n    text\n\n- b'),
        (
            'meta:\n  inner: |+\n    text\n\ntags: []',
            'meta:\n  inner: |+\n    text\n\ntags: []',
        ),
        (
            'desc: "line one\n\nline two"\ntags: []',
            'desc: "line one\n\nline two"\ntags: []',
        ),
        ("desc: 'one\n\ntwo'\n\ntags: []", "desc: 'one\n\ntwo'\ntags: []"),
        ('desc: |-\n  Alpha.\n    \ntags: []', 'desc: |-\n  Alpha.\n    \ntags: []'),
        ('desc: A.\n\ntags: []', 'desc: A.\ntags: []'),
        ('desc: &a |+\n  text\n\ntags: []', 'desc: &a |+\n  text\n\ntags: []'),
        ('desc: !!str >+\n  text\n\ntags: []', 'desc: !!str >+\n  text\n\ntags: []'),
        ('extra: |2\n   \ntags: []', 'extra: |2\n   \ntags: []'),
        ('tags: ["a\n\nb"]\nx: 1', 'tags: ["a\n\nb"]\nx: 1'),
        ('tags: [a,\n\nb]\n\nx: 1', 'tags: [a,\n\nb]\nx: 1'),
        ('meta: {k: "a\n\nb"}\nx: 1', 'meta: {k: "a\n\nb"}\nx: 1'),
        ('desc: A\n  "b\ntags: []\n\nx: 1', 'desc: A\n  "b\ntags: []\nx: 1'),
        ('desc: ["alpha\n  beta."]\n\nx: 1', 'desc: ["alpha\n  beta."]\nx: 1'),
        ('title: [unclosed\n\ndesc: D.', 'title: [unclosed\ndesc: D.'),
        ('"desc" : |+\n  Text.\n\ntags: []', '"desc" : |+\n  Text.\n\ntags: []'),
    ],
    ids=[
        'item-mapping-keep',
        'nested-keep',
        'dq-column0-blank',
        'sq-column0-blank-then-stray',
        'ws-only-over-indented',
        'stray',
        'anchored-keep',
        'tagged-folded-keep',
        'explicit-indent-ws-body',
        'flow-seq-quoted-blank',
        'flow-seq-plain-blank-then-stray',
        'flow-map-quoted-blank',
        'plain-continuation-quote-does-not-arm',
        'flow-quote-across-lines-then-stray',
        'unclosed-flow-then-key',
        'quoted-key-spaced-colon-keep',
    ],
)
def test_strip_blank_lines_keeps_content_blanks_at_any_depth(
    text: str, expected: str
) -> None:
    """Blank lines inside a value are content wherever the value opens; strays between fields go."""
    assert format.strip_blank_lines(f'---\n{text}\n---') == f'---\n{expected}\n---'


@pytest.mark.parametrize(
    argnames='line',
    argvalues=[
        '"name": stale',
        "'name': stale",
        '!!str name: stale',
        '&a name: stale',
    ],
    ids=['quoted', 'single-quoted', 'tagged', 'anchored'],
)
def test_fallback_repair_sees_a_quoted_key(line: str) -> None:
    """Under the line grammar a quoted, tagged, or anchored key is the field it names.

    The fallback and the parser agree on the key set, so the repair never
    inserts a duplicate beside a key spelled with a property or quotes.
    """
    text = f'---\n{line}\ndesc: D.\ninvalid: A: b\n---'
    repaired = format.repair_frontmatter(
        text,
        name=NAME,
        now=NOW,
        title=True,
        category=True,
        order=True,
    )
    assert repaired.count('name') == 1
    assert format.field_text(repaired, 'name') == f'name: {NAME}\n'


@pytest.mark.parametrize(
    argnames=('body', 'line'),
    argvalues=[
        ('name:core\ntitle: x', 2),
        ('name:core\n\ntitle: x', 2),
        (f'name: {NAME}\ntitle: a: b', 3),
        (f'name: {NAME}\ndesc: >\n  A folded\n  line.\ntitle: a: b', 6),
        (f'name: {NAME}\ndesc: Alpha beta\n  gamma: delta.', 4),
        (
            f'name: {NAME}\ndesc: A long description that\n  continues and\n  mentions X: y',
            5,
        ),
        ('# c\nname:core\ntitle: x', 3),
    ],
    ids=[
        'typo-line',
        'typo-line-blank-between',
        'key-line',
        'key-line-after-block',
        'continuation-line',
        'third-line',
        'typo-line-after-comment',
    ],
)
@pytest.mark.usefixtures('_vary_loader')
def test_colon_error_names_the_line_the_scalar_started_on(body: str, line: int) -> None:
    """A colon the parser trips over is reported where the plain scalar holding it began.

    A ``key:value`` typo opening the block folds into the key line below
    it (across a blank line too), so the typo's own line is named; a key
    on the error line opened the scalar itself, so the error line is
    named even when a multi-line field sits above it.
    """
    text = f'---\n{body}\n---'
    issues = format.frontmatter_issues(text)
    assert [(issue[0], issue[2]) for issue in issues] == [(line, 'parse')]
    assert 'mapping values are not allowed' in issues[0][1]


@pytest.mark.parametrize(
    argnames='body',
    argvalues=[
        'tags: &a [*a]',
        'tags: &a {k: *a}',
        '\n'.join(
            f'k{level}: &a{level} [*a{level - 1}, *a{level - 1}]'
            for level in range(1, 25)
        ),
    ],
    ids=['cyclic-sequence', 'cyclic-mapping', 'doubling-dag'],
)
def test_compose_walks_an_alias_graph_once(body: str) -> None:
    """A node reached twice is walked once, so a cyclic or shared alias graph composes in bounded time."""
    text = f'---\nname: {NAME}\na0: &a0 [x]\n{body}\n---'
    assert format.frontmatter_issues(text) == []
    assert format.read_frontmatter_name(text) == NAME


# one level past the nesting bound, however the collections nest
_DEEP = format._MAX_NESTING * 2


@pytest.mark.parametrize(
    argnames=('body', 'line'),
    argvalues=[
        ('desc: ' + '[' * _DEEP + 'x' + ']' * _DEEP, 3),
        ('desc: ' + '{a: ' * _DEEP + 'x' + '}' * _DEEP, 3),
        ('tags:\n' + '- ' * _DEEP + 'x', 4),
        ('tags: ' + '[' * format._MAX_NESTING + 'x' + ']' * format._MAX_NESTING, None),
        ("desc: rock 'n roll\ntags: " + '[' * _DEEP + 'x' + ']' * _DEEP, 4),
        ('desc: a ' + '[' * _DEEP, None),
        ('desc: |\n' + '\n'.join('  [ x' for _ in range(_DEEP)), None),
        ('desc: |\n  ' + '- ' * _DEEP + 'x', None),
        ('desc: plain\n  ' + '[' * _DEEP, None),
    ],
    ids=[
        'flow-sequence',
        'flow-mapping',
        'item-chain',
        'at-the-bound',
        'apostrophe-before-deep',
        'brackets-in-plain-value',
        'brackets-in-block-body',
        'chain-in-block-body',
        'brackets-in-continuation',
    ],
)
@pytest.mark.usefixtures('_vary_loader')
def test_compose_refuses_nesting_past_the_bound(body: str, line: Optional[int]) -> None:
    """Collections nested past the bound are a strict-reader finding, not a recursion the composer runs.

    The pure loader raises RecursionError a few hundred levels down and the
    C loader recurses unchecked, so both refuse the block the same way,
    naming the line that passes the bound; a block at the bound composes.
    """
    text = f'---\nname: {NAME}\n{body}\n---'
    issues = format.frontmatter_issues(text)
    reason = f'collections nested deeper than {format._MAX_NESTING} levels'
    if line is None:
        assert issues == []
    else:
        assert issues == [(line, reason, 'parse')]


@pytest.mark.usefixtures('_vary_loader')
def test_tab_on_the_last_block_line_is_named() -> None:
    """A tab on a whitespace-only line closing the block is reported on that line, not the one above."""
    issues = format.frontmatter_issues(f'---\nname: {NAME}\ndesc: D.\n\t\n---')
    assert [(line, cause) for line, _, cause in issues] == [(4, 'parse')]


def test_repair_keeps_an_item_after_a_valued_key_in_place() -> None:
    """A column-0 item after a value on the key line is text outside the field, not the name's body.

    YAML makes column-0 items the value of a bare key alone; after
    ``name: x`` they are a stray line the parser rejects, so the refresh
    replaces the key line only and the item stays where the author put it
    for lint to name.
    """
    text = '---\nname: stale\n- draft\ndesc: D.\ninvalid: A: b\n---'
    repaired = format.repair_frontmatter(
        text,
        name=NAME,
        now=NOW,
        title=True,
        category=True,
        order=True,
    )
    assert f'name: {NAME}\n- draft\n' in repaired
    assert repaired.count('- draft') == 1


@pytest.mark.parametrize('key', ['desc', 'created'])
def test_repair_fills_an_empty_block_over_column_zero_comments(key: str) -> None:
    """A block header over column-0 comment lines alone is an empty value the repair fills.

    A column-0 line ends the block, so the comments are no body: the
    placeholder or stamp lands on the header line and the comments stay
    under it.
    """
    text = f'---\nname: {NAME}\n{key}: |\n# c\n\n# d\n---'
    repaired = format.repair_frontmatter(
        text,
        name=NAME,
        now=NOW,
        title=True,
        category=True,
        order=True,
    )
    assert oracle_valid(yaml_body(repaired)), repaired
    value = '...' if key == 'desc' else NOW
    assert f'{key}: {value}\n# c\n# d\n' in repaired
    assert format.read_frontmatter_field(repaired, key) == value


def test_field_ranges_end_at_a_typo_line_under_the_line_grammar() -> None:
    """A dedented ``key:value`` typo ends the open field in a block the parser rejects."""
    text = f'---\nname: {NAME}\ninvalid: A: b\ndesc: x\nurl:http://example.com\n---'
    lines = text.split('\n')
    assert format.field_line_ranges(text, lines, ['desc']) == {4}


@pytest.mark.parametrize(
    argnames='desc',
    argvalues=['Line one.\nctrl' + chr(1) + 'mid.', 'a\nb' + chr(0x85) + 'c'],
    ids=['control', 'nel'],
)
def test_build_frontmatter_escapes_a_multi_line_desc(desc: str) -> None:
    """A multi-line desc holding a character no block may carry is written double-quoted."""
    text = format.build_frontmatter(name='x', created=NOW, updated=NOW, desc=desc)
    assert oracle_valid(yaml_body(text)), text
    assert oracle_scalar(yaml_body(text), 'desc') == desc
    assert 'desc: "' in text


@pytest.mark.parametrize(
    argnames='desc',
    argvalues=['  padded\nplain', '\ttabbed\nplain', '\n  padded\nplain'],
    ids=['space-first', 'tab-first', 'blank-then-padded'],
)
@pytest.mark.usefixtures('_vary_loader')
def test_build_frontmatter_pins_a_padded_first_line(desc: str) -> None:
    """A multi-line desc whose first content line opens with whitespace still writes a block a strict reader accepts."""
    text = format.build_frontmatter(name='x', created=NOW, updated=NOW, desc=desc)
    assert oracle_valid(yaml_body(text)), text
    assert normalize(oracle_scalar(yaml_body(text), 'desc')) == desc
    assert format.read_frontmatter_desc(text) == desc


def test_field_ranges_end_at_a_foreign_key_under_the_line_grammar() -> None:
    """A dedented key outside the grammar still ends the open field in a block the parser rejects."""
    text = f'---\nname: {NAME}\ninvalid: A: b\ndesc: x\nmy key: needle\n---'
    lines = text.split('\n')
    assert format.field_line_ranges(text, lines, ['desc']) == {4}


_REPAIRED = f'name: {NAME}\ndesc: D.\ncreated: {NOW}\nupdated: {NOW}'


@pytest.mark.parametrize(
    argnames=('before', 'after', 'breaks'),
    argvalues=[
        (f'---\n{_REPAIRED}\n---', f'---\n{_REPAIRED}\ntitle: T\n---', False),
        (f'---\n{_REPAIRED}\n---', f'---\n{_REPAIRED}\nname: y\n---', True),
        ('---\nname: A: b\n---', f'---\nname: A: b\n{_REPAIRED}\n---', False),
        (
            '---\ntags: "open\nname: "x\ncustom: y"\n---',
            f'---\ntags: "open\nname: {NAME}\ndesc: ...\ncustom: y"\n'
            f'created: {NOW}\nupdated: {NOW}\n---',
            True,
        ),
        (
            '---\nupdated: "quoted\ncontinued"\ntags: "open\n---',
            f'---\nname: {NAME}\ndesc: ...\ntags: "open\ncreated: {NOW}\n'
            f'updated: {NOW}\ncontinued"\n---',
            True,
        ),
        (
            f'---\nname: {NAME}\nzebra: 27"\ndesc: "open\n---',
            f'---\nname: {NAME}\ndesc: "open\nzebra: 27"\ncreated: {NOW}\nupdated: {NOW}\n---',
            True,
        ),
        (
            f'---\nname: "p\ndesc: D.\nx: y"\ncreated: {NOW}\nupdated: {NOW}\n---',
            f'---\nname: {NAME}\ndesc: ...\ncreated: {NOW}\nupdated: {NOW}\n---',
            True,
        ),
        (
            f'---\nname: "stale\n2024 review: my notes"\ndesc: D.\n'
            f'created: {NOW}\nupdated: {NOW}\n---',
            f'---\nname: {NAME}\ndesc: D.\ncreated: {NOW}\nupdated: {NOW}\n---',
            True,
        ),
        (
            f'---\nname: "just\n  a wrapped name"\ndesc: D.\n'
            f'created: {NOW}\nupdated: {NOW}\n---',
            f'---\nname: {NAME}\ndesc: D.\ncreated: {NOW}\nupdated: {NOW}\n---',
            False,
        ),
        (
            f'---\nname: {NAME}\ndesc: D.\ncreated: {NOW}\nupdated: "{NOW}"\n'
            f'# todo: verify the stamp\n---',
            f'---\nname: {NAME}\ndesc: D.\ncreated: {NOW}\nupdated: "{NOW}"\n'
            f'# todo: verify the stamp\n---',
            False,
        ),
    ],
    ids=[
        'valid-to-valid',
        'valid-to-invalid',
        'invalid-to-invalid',
        'quote-closed-around-name',
        'quote-closed-around-stamps',
        'reorder-closes-around-authored-key',
        'authored-lines-inside-name',
        'spaced-key-inside-name',
        'wrapped-name-is-stale-value',
        'comment-after-quoted-stamp',
    ],
)
def test_repair_breaks_frontmatter_names_a_breaking_repair(
    before: str,
    after: str,
    breaks: bool,
) -> None:
    """A repair breaks a block by rejecting an accepted one or by writing fields a strict reader never sees.

    A refresh or a stamp landing inside a quote the line grammar could not
    see closes it around the written lines, so the block composes as a
    long scalar with no ``name``, ``desc``, or stamp key: such a result is
    refused as the parse error is.
    """
    assert format.repair_breaks_frontmatter(before, after) is breaks


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
        # a block scalar's final breaks are chomping, not content
        ('desc: |\n  x', 'desc', 'x'),
        ('desc: |+\n  x\n', 'desc', 'x'),
        # typed-looking scalars stay strings
        ('title: 2024', 'title', '2024'),
        ('title: 1.10', 'title', '1.10'),
        ('title: yes', 'title', 'yes'),
        ('title: 2026-01-01', 'title', '2026-01-01'),
        ('created: 2026-07-10T02:36:41Z', 'created', '2026-07-10T02:36:41Z'),
        # ' #' starts a comment: the text after it reaches no reader
        ('desc: Use #1 approach.', 'desc', 'Use'),
        # sequences are never resolved: their source text joined, so the search
        # index tokenizes every item, a ' #' inside quotes kept
        ('tags: [a, b]', 'tags', '[a, b]'),
        ('tags:\n- a\n- b', 'tags', '- a - b'),
        ('tags:\n- a\n# c\n- b', 'tags', '- a - b'),
        ('tags: &t [a, b]', 'tags', '[a, b]'),
        ('tags: [a, b] # note', 'tags', '[a, b]'),
        ('tags:\n- a # note\n- b', 'tags', '- a - b'),
        ("tags: ['A long\n  item #x', beta]", 'tags', "['A long item #x', beta]"),
        ('tags: ["issue #42", triage]', 'tags', '["issue #42", triage]'),
        ('tags: [a,\n  b]', 'tags', '[a, b]'),
        # the first occurrence of a duplicated key wins, bare or not
        ('desc:\ndesc: second', 'desc', None),
        ('desc: first\ndesc: second', 'desc', 'first'),
        # tolerated invalid YAML reads through the line grammar as authored
        ('desc: A theorem for X: HR, WR.', 'desc', 'A theorem for X: HR, WR.'),
        ('desc: > one liner.', 'desc', 'one liner.'),
        ('desc: "unterminated\n  continues', 'desc', '"unterminated continues'),
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
        'literal-chomp',
        'keep-chomp',
        'int-like',
        'float-like',
        'bool-like',
        'date-like',
        'timestamp',
        'hash-in-value',
        'flow-sequence',
        'block-sequence',
        'block-sequence-comment',
        'flow-sequence-anchored',
        'flow-sequence-tail-comment',
        'block-sequence-item-comment',
        'flow-sequence-wrapped-item-hash',
        'flow-sequence-quoted-hash',
        'flow-sequence-two-lines',
        'duplicate-bare-first',
        'duplicate-valued-first',
        'unquoted-colon-space',
        'indicator-inline-text',
        'unterminated-quote',
        'conflict-markers',
        'invalid-neighbor',
    ],
)
@pytest.mark.usefixtures('_vary_loader')
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
    # a NUL escape has no place on a markdown surface (git would call the
    # file binary), so it goes
    text = f'---\nname: {NAME}\ndesc: "a{backslash}0b."\n---'
    assert format.read_frontmatter_field(text, 'desc') == 'ab.'


@pytest.mark.parametrize(
    argnames=('field', 'read', 'expected'),
    argvalues=[
        ('category: null # why', format.read_frontmatter_category, ''),
        ('category:\n  null', format.read_frontmatter_category, ''),
        ("category: 'null' # text", format.read_frontmatter_category, 'null'),
        ('title: null # why', format.read_frontmatter_title, ''),
        ('desc: # note', format.read_frontmatter_desc, None),
        ('desc: ... # note', format.read_frontmatter_desc, '...'),
        ("desc: 'kept # inside'", format.read_frontmatter_desc, 'kept # inside'),
        ('desc: # note\n  Alpha beta.', format.read_frontmatter_desc, 'Alpha beta.'),
        ('desc:\n  # note', format.read_frontmatter_desc, None),
        ('desc:\n# c\n  Alpha beta.', format.read_frontmatter_desc, 'Alpha beta.'),
        ('desc: >\n  Alpha\n  beta.', format.read_frontmatter_desc, 'Alpha beta.'),
        ('desc: | # note', format.read_frontmatter_desc, ''),
        ('desc: "a\\"b" # c', format.read_frontmatter_desc, 'a"b'),
        (
            'desc:\n  Alpha\n  # note\n  beta.',
            format.read_frontmatter_desc,
            'Alpha beta.',
        ),
        ('desc:\n  Alpha\n  beta.', format.read_frontmatter_desc, 'Alpha beta.'),
        ('desc:\n  Alpha # tail\n  beta.', format.read_frontmatter_desc, 'Alpha beta.'),
        ('title: ~', format.read_frontmatter_title, '~'),
        ('"desc": Quoted key.', format.read_frontmatter_desc, 'Quoted key.'),
        ('title: &a1 First\n  second.', format.read_frontmatter_title, 'First second.'),
        ('!!str desc: Tagged key.', format.read_frontmatter_desc, 'Tagged key.'),
        (
            'desc: ["issue #42", triage]',
            format.read_frontmatter_desc,
            '["issue #42", triage]',
        ),
        ('desc: [a,\n  b]', format.read_frontmatter_desc, '[a, b]'),
        ('desc: [unclosed\ntitle: T', format.read_frontmatter_desc, '[unclosed'),
        ('desc: ["alpha\n  beta."]', format.read_frontmatter_desc, '["alpha beta."]'),
        ('desc: &a |\n  Hello.', format.read_frontmatter_desc, 'Hello.'),
        ('title: !!str >\n  T', format.read_frontmatter_title, 'T'),
        ('desc: &a   !!str |', format.read_frontmatter_desc, ''),
        (
            'title: |4\n    Deep title.\n  # note',
            format.read_frontmatter_title,
            'Deep title.',
        ),
        (
            'tags:\n- zebra\n- stripes',
            lambda text: format.read_frontmatter_field(text, 'tags'),
            '- zebra - stripes',
        ),
        (
            'desc: "Alpha beta.\n  Gamma delta."',
            format.read_frontmatter_desc,
            'Alpha beta. Gamma delta.',
        ),
        (
            'tags: [alpha, beta] # editorial note',
            lambda text: format.read_frontmatter_field(text, 'tags'),
            '[alpha, beta]',
        ),
        ("desc:\n  'A: colon here.'", format.read_frontmatter_desc, 'A: colon here.'),
        ('desc:\n  "Two\n  lines"', format.read_frontmatter_desc, 'Two lines'),
        (
            "tags:\n- 'notes #draft'\n- review",
            lambda text: format.read_frontmatter_field(text, 'tags'),
            "- 'notes #draft' - review",
        ),
        ("title: 'Bob's Page'", format.read_frontmatter_title, "Bob's Page"),
        (
            "desc: 'It's the team's plan: ship in May.'",
            format.read_frontmatter_desc,
            "It's the team's plan: ship in May.",
        ),
        ("desc:\n  'Bob's page.'", format.read_frontmatter_desc, "Bob's page."),
        (
            "tags: ['Bob's #1 hit', beta]",
            lambda text: format.read_frontmatter_field(text, 'tags'),
            "['Bob's #1 hit', beta]",
        ),
        (
            "desc:\n  'A long\n  scalar # tag\n  ends.'",
            format.read_frontmatter_desc,
            'A long scalar # tag ends.',
        ),
    ],
    ids=[
        'null-comment',
        'null-body',
        'quoted-null',
        'title-null-comment',
        'comment-only',
        'placeholder-comment',
        'quoted-hash',
        'commented-key-with-body',
        'comment-only-body',
        'column0-comment-mid-field',
        'folded',
        'block-header-comment',
        'escaped-quote-then-comment',
        'indented-comment-mid-body',
        'bare-key-two-lines',
        'bare-key-tail-comment',
        'tilde-is-text',
        'quoted-key',
        'anchored-plain-multiline',
        'tagged-key',
        'flow-with-quoted-hash',
        'flow-two-lines',
        'unclosed-flow',
        'flow-quote-across-lines',
        'anchored-block',
        'tagged-folded-title',
        'double-spaced-properties',
        'block-indentation-comment',
        'column0-items',
        'quoted-wrapped',
        'flow-tail-comment',
        'bare-key-quoted-body',
        'bare-key-quoted-two-lines',
        'quoted-item-hash',
        'apostrophe-title',
        'apostrophe-desc',
        'apostrophe-bare-key-body',
        'apostrophe-flow-hash',
        'bare-key-quoted-mid-hash',
    ],
)
def test_fallback_reader_agrees_with_the_repair(
    field: str,
    read: Callable[[str], Optional[str]],
    expected: Optional[str],
) -> None:
    """Under the line grammar a field reads as the repair judges it.

    One invalid line sends the block through the line grammar; a
    ``null`` with a comment, a ``null`` continued on the next line, and a
    comment-only value must still read as unset there, or the repair
    removes or fills a field the reader propagated and the update never
    converges.
    """
    text = f'---\nname: {NAME}\ninvalid: A: b\n{field}\n---'
    assert format.frontmatter_issues(text)
    assert read(text) == expected
    # the repair judges the field the same way: a valued field keeps its
    # lines and reads the same afterwards, a valueless one is filled or gone
    repaired = format.repair_frontmatter(
        text,
        name=NAME,
        now=NOW,
        title=True,
        category=True,
        order=True,
    )
    key = re.sub(r'^(?:[&!]\S*[ \t]+)*', '', field.split(':')[0]).strip('"')
    if expected:
        assert read(repaired) == expected
        assert field.split('\n')[-1] in repaired
    elif key == 'desc':
        assert read(repaired) == '...'
    else:
        assert format.field_text(repaired, key) is None


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
@pytest.mark.usefixtures('_vary_loader')
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
        'Notes on room #12 and the key.',
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
        '-',
        '?',
        '<<',
        '=',
        ',leading comma',
        'a' + chr(9) + 'tab',
        'del' + chr(0x7F) + 'x',
        '2024-02-30',
        '2026-01-01 25:00:00',
        '0x_',
        '0b_',
        '-0x_',
    ],
)
def test_quote_round_trips_through_yaml(value: str) -> None:
    """A written value reads back verbatim under a strict reader and under ``unquote``.

    The writer is the reader's twin: every value the tool writes plain
    must be one a strict reader (Obsidian's included) reads back as the
    same text, or an adopted heading is rewritten on the next update.
    """
    assert oracle_scalar(f'k: {format.quote(value)}\n', 'k') == value
    assert format.unquote(format.quote(value)) == value
    # the block constructs too: a date-shaped value no calendar has is quoted
    # (an empty plain value constructs as null, which the reader reads as absent)
    if value:
        assert oracle_mapping(f'k: {format.quote(value)}\n') == {'k': value}


@pytest.mark.parametrize(
    argnames='value',
    argvalues=[
        'line' + chr(0x2028) + 'separator',
        'para' + chr(0x2029) + 'separator',
        'next' + chr(0x85) + 'line',
        'bell' + chr(7) + 'rings',
        'carriage' + chr(13) + 'return',
        'quoted "and' + chr(1) + 'escaped\\',
        'Caf' + chr(0x80) + ' latin',
        'c1' + chr(0x9F) + 'end',
        'non' + chr(0xFFFE) + 'character',
        'two' + chr(10) + 'lines',
    ],
    ids=[
        'line-separator',
        'paragraph-separator',
        'nel',
        'bell',
        'cr',
        'mixed',
        'c1-first',
        'c1-last',
        'noncharacter',
        'newline',
    ],
)
def test_quote_escapes_characters_no_stream_may_carry(value: str) -> None:
    """A value holding a character YAML forbids in a stream is written escaped.

    Plain or single-quoted, such a character makes the whole block
    invalid (or folds as a line break); double-quoted with an escape it
    reads back verbatim under a strict reader and under ``unquote``, and
    a line break spells as the short newline escape.
    """
    written = format.quote(value)
    assert written[0] == written[-1] == '"'
    assert oracle_scalar(f'k: {written}\n', 'k') == value
    assert format.unquote(written) == value
    assert chr(10) not in written
    if chr(10) in value:
        assert '\\n' in written


@pytest.mark.parametrize(
    argnames=('escaped', 'expected'),
    argvalues=[
        ('"a\\Nb"', 'a' + chr(0x85) + 'b'),
        ('"a\\_b"', 'a' + chr(0xA0) + 'b'),
        ('"a\\Lb\\Pc"', 'a' + chr(0x2028) + 'b' + chr(0x2029) + 'c'),
        ('"a\\U0001F600b"', 'a' + chr(0x1F600) + 'b'),
        ('"a\\x41\\u0042c"', 'aABc'),
        ('"tab\\there\\0end"', 'tab' + chr(9) + 'here' + chr(0) + 'end'),
        ('"slash\\/quote\\"back\\\\"', 'slash/quote"back\\'),
    ],
    ids=['nel', 'nbsp', 'separators', 'wide', 'hex-unicode', 'tab-nul', 'punctuation'],
)
def test_unquote_decodes_every_double_quoted_escape(
    escaped: str, expected: str
) -> None:
    """``unquote`` decodes the escapes a strict reader decodes, so ``match`` sees the same text.

    An escape naming no character -- past U+10FFFF, or a lone surrogate --
    stays verbatim, as libyaml reads it, and never a crash; a comment after
    a quoted value is not the value.
    """
    assert format.unquote(escaped) == expected
    assert oracle_scalar(f'k: {escaped}\n', 'k') == expected
    assert format.unquote('"a\\U00110000b\\uD800c"') == 'a\\U00110000b\\uD800c'
    assert format.field_value("desc: 'a #b' # c") == 'a #b'


def test_field_value_strips_keys_quotes_and_comments() -> None:
    """``field_value`` yields the line's value however its key and quotes are spelled.

    Node properties and comment tails are not the value; a composed key
    (``line_keys``) strips whatever its spelling, ``''`` marks a line the
    composed block says opens no key, and a quote mid-text or one closing
    on a later line is content.
    """
    # the line grammar's key shapes strip; a spaced key stays without help
    assert format.field_value('desc: &d !!str Verbatim.') == 'Verbatim.'
    assert format.field_value('desc: Alpha # note') == 'Alpha'
    assert format.field_value('my key: v') == 'my key: v'
    # a composed key strips whatever its spelling
    assert format.field_value('my key: v', key='my key') == 'v'
    assert format.field_value('"my key": v', key='my key') == 'v'
    assert format.field_value('"a\\"b": v', key='a"b') == 'v'
    assert format.field_value("'it''s': v", key="it's") == 'v'
    # quotes decode: mid-text quotes are content, an open quote is not value text
    assert format.field_value("tags: ['alpha #note', beta]", key='tags') == (
        "['alpha #note', beta]"
    )
    assert format.field_value('two: three"', key='') == 'two: three"'
    assert format.field_value('desc: "Alpha continued', key='desc') == 'Alpha continued'
    assert format.field_value("desc: 'It's here'") == "It's here"


def test_line_keys_names_composed_key_lines() -> None:
    """``line_keys`` maps file lines to composed keys, empty where the line grammar rules."""
    assert format.line_keys(f'---\nname: {NAME}\nmy key: v\n---') == {
        2: 'name',
        3: 'my key',
    }
    assert format.line_keys(f'---\nname: {NAME}\ndesc: A: b\n---') == {}


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
