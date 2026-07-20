"""Behavioral tests for ``Wiki.update``.

Convergence and idempotence, authored-content preservation, the
repair-vs-refuse damage taxonomy, category and desc propagation,
prune, and escape stability. The plan/apply engine mechanics
(concurrency, atomicity, timestamps) live in ``test_plan``.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import unicodedata
from typing import Optional

import pytest

from wiki.core import format
from wiki.core.wiki import Wiki

from ._helpers import (
    CategorizedWiki,
    _capture_notices,
    _make_category_folder,
    _make_wiki,
    page_index,
)

__all__ = [
    'test_update_full_workflow',
    'test_update_normalizes_delimiter_region_spacing',
    'test_update_preserves_content',
    'test_update_preserves_content_with_thematic_break',
    'test_update_preserves_frontmatter_with_dashes',
    'test_update_no_delimiter_keeps_content',
    'test_update_repairs_formatter_mangled_index',
    'test_update_reports_page_with_unclosed_frontmatter',
    'test_update_does_not_restamp_a_crlf_passthrough_page',
    'test_update_refuses_truncated_index',
    'test_update_refuses_nested_wiki',
    'test_update_ignores_wikis_above_the_root',
    'test_update_refuses_conflict_markers',
    'test_update_survives_backslash_digit_name',
    'test_repair_frontmatter_survives_backslash_timestamp',
    'test_update_accepts_block_scalar_desc',
    'test_update_restores_valueless_desc_but_keeps_a_real_block_scalar',
    'test_update_accepts_block_scalar_name',
    'test_update_joins_multiline_block_scalar_name',
    'test_update_folds_and_preserves_inline_desc',
    'test_update_preserves_prose_above_delimiter',
    'test_update_preserves_prose_below_delimiter_above_h1',
    'test_update_broken_links',
    'test_update_emits_every_notice',
    'test_update_announces_created_index',
    'test_update_announces_adoption',
    'test_update_announces_desc_overwrite',
    'test_update_trailing_whitespace_desc_converges_quietly',
    'test_update_rewrapped_desc_converges_quietly',
    'test_update_blank_line_in_desc_row_converges_quietly',
    'test_update_converges_on_wrap_mangled_desc',
    'test_update_scoped',
    'test_reclaimed_index_keeps_link_shaped_continuation',
    'test_body_edits_never_dirty_the_tree',
    'test_update_fills_blank_frontmatter_values',
    'test_update_inserts_timestamps_in_canonical_order',
    'test_update_inserts_desc_in_schema_order',
    'test_update_enforces_canonical_field_order',
    'test_read_frontmatter_category',
    'test_quoted_desc_propagates_and_lints_clean',
    'test_update_category_labels',
    'test_category_propagates_and_clears',
    'test_sort_unlisted_category',
    'test_page_category',
    'test_fresh_frontmatter_carries_no_category_line',
    'test_update_removes_unset_category',
    'test_update_skips_invalid_name',
    'test_update_matches_decomposed_names',
    'test_sidecar_page_links_by_full_name',
    'test_undecodable_page_error_names_the_file',
    'test_update_converges_on_structural_desc_lines',
    'test_update_restores_missing_index_name',
    'test_update_adds_missing_name',
    'test_update_detects_bom_prefixed_frontmatter',
    'test_names_with_colon_write_quoted_yaml',
    'test_titles_with_leading_block_indicator_write_quoted_yaml',
    'test_title_wins_heading_and_null_reverts',
    'test_update_repositions_title_under_name',
    'test_update_inserts_desc_under_title',
    'test_multi_line_title_moves_as_one_unit',
    'test_update_removes_valueless_title',
    'test_quoted_colon_title_renders_unquoted_heading',
    'test_update_adopts_bare_page_seeding_title',
    'test_required_titles_seed_lint_and_flip_off',
    'test_required_titles_adopts_no_h1_page',
    'test_update_materializes_missing_settings',
    'test_update_skips_symlinked_page',
    'test_update_names_symlinked_link_target',
    'test_update_skips_out_of_root_desc_propagation',
    'test_update_escapes_damage_shaped_child_desc',
]


# ------ generation and preservation


def test_update_full_workflow(tmp_path: pathlib.Path) -> None:
    """Update adds frontmatter, links, word counts, and parent links."""
    # build a populated wiki with one folder and two pages
    wiki = _make_wiki(tmp_path, folders={'core': ['design', 'api']})

    # read root index
    root_index = (tmp_path / '_index.md').read_text(encoding='utf-8')

    # links to child folder generated
    assert '[[core]]' in root_index or '[[core/' in root_index

    # child folder has links to pages
    core_index = (tmp_path / 'core' / '_index.md').read_text(encoding='utf-8')
    assert '[[design]]' in core_index or '[[core/design' in core_index
    assert '[[api]]' in core_index or '[[core/api' in core_index

    # pages have frontmatter (name includes path prefix)
    design = (tmp_path / 'core' / 'design.md').read_text(encoding='utf-8')
    assert 'name:' in design
    assert 'design' in design

    # word counts computed into the cache, never into frontmatter
    counts = json.loads(
        (tmp_path / '.wiki' / 'cache' / 'word_counts.json').read_text(encoding='utf-8')
    )
    assert counts['core/design.md']['words'] > 0

    # child folder index has parent link
    assert '|..]]' in core_index

    # update is idempotent (second pass changes nothing)
    second_pass = wiki.update()
    assert len(second_pass) == 0


def test_update_normalizes_delimiter_region_spacing(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """User content sits one blank below '***'; files end with one newline.

    The generated region already enforces a blank after the H1 and
    before the delimiter; the user region converges to the same shape
    however it was hand-authored -- content jammed against the
    delimiter, blank pile-ups, or a missing trailing newline. An empty
    user region keeps the bare delimiter tail.
    """
    wiki = _make_wiki(tmp_path, folders={'notes': ['readme']})
    # freeze the production clock so the three update passes cannot
    # straddle a second boundary and skew the updated: stamps
    frozen_now = wiki._utc_now()
    monkeypatch.setattr(Wiki, '_utc_now', lambda self: frozen_now)
    index_path = tmp_path / 'notes' / '_index.md'
    # the fixture index renders canonically: one blank below the
    # delimiter, one trailing newline
    original = index_path.read_text(encoding='utf-8')
    assert original.endswith('***\n\nOverview of notes.\n')
    # each hand-authored variant converges to the same canonical bytes
    head = original[: original.index('***')]
    canonical = None
    for tail in ('***\nMy notes.', '***\n\n\n\nMy notes.\n\n\n', '***\n\nMy notes.'):
        index_path.write_text(head + tail, encoding='utf-8')
        wiki.update()
        result = index_path.read_text(encoding='utf-8')
        assert result.endswith('***\n\nMy notes.\n')
        canonical = canonical or result
        assert result == canonical
    # the canonical shape is stable (second pass changes nothing)
    assert len(wiki.update()) == 0


def test_update_preserves_content(tmp_path: pathlib.Path) -> None:
    """Update preserves user content below delimiter and link descriptions."""
    # build a populated wiki with one folder and page
    wiki = _make_wiki(tmp_path, folders={'notes': ['readme']})

    # add user content below delimiter in folder index
    index_path = tmp_path / 'notes' / '_index.md'
    content = index_path.read_text(encoding='utf-8')
    content += '\nMy custom notes here.\n'
    index_path.write_text(content, encoding='utf-8')

    # update preserves user content
    wiki.update()
    updated = index_path.read_text(encoding='utf-8')
    assert 'My custom notes here.' in updated

    # remove desc from child page's frontmatter
    readme_path = tmp_path / 'notes' / 'readme.md'
    readme_content = readme_path.read_text(encoding='utf-8')
    readme_content = readme_content.replace('desc: The readme page.\n', '')
    readme_path.write_text(readme_content, encoding='utf-8')

    # manually set a link description in parent
    updated = index_path.read_text(encoding='utf-8')
    updated = updated.replace(
        'The readme page.',
        'Custom description.',
    )
    index_path.write_text(updated, encoding='utf-8')

    # update preserves custom description (child has no desc to override)
    wiki.update()
    final = index_path.read_text(encoding='utf-8')
    assert 'Custom description.' in final


def test_update_preserves_content_with_thematic_break(tmp_path: pathlib.Path) -> None:
    """A '***' thematic break in user content is not treated as the delimiter."""
    # build a populated wiki with one folder and page
    wiki = _make_wiki(tmp_path, folders={'notes': ['readme']})

    # add content with a '***' horizontal rule embedded in it
    index_path = tmp_path / 'notes' / '_index.md'
    content = index_path.read_text(encoding='utf-8')
    content += '\nAbove the rule.\n\n***\n\nBelow the rule.\n'
    index_path.write_text(content, encoding='utf-8')

    # both halves survive (the first '***' is the structural delimiter)
    wiki.update()
    updated = index_path.read_text(encoding='utf-8')
    assert 'Above the rule.' in updated
    assert 'Below the rule.' in updated


def test_update_preserves_frontmatter_with_dashes(tmp_path: pathlib.Path) -> None:
    """An indented '---' inside a block scalar does not close the frontmatter."""
    # build a bare populated wiki
    wiki = _make_wiki(tmp_path)

    # page whose desc block scalar contains a '---' line
    page = tmp_path / 'Topic.md'
    page.write_text(
        '---\n'
        'name: Topic\n'
        'desc: |\n'
        '  first line.\n'
        '  ---\n'
        '  third line.\n'
        'category: node\n'
        'tags: []\n'
        '---\n'
        '# Topic\n\n'
        'Body content here.\n',
        encoding='utf-8',
    )

    # frontmatter fields and body survive; exactly one frontmatter block remains
    wiki.update()
    result = page.read_text(encoding='utf-8')
    assert 'category: node' in result
    assert 'third line.' in result
    assert 'Body content here.' in result
    delimiters = sum(1 for line in result.split('\n') if line.rstrip() == '---')
    assert delimiters == 2


def test_update_no_delimiter_keeps_content(tmp_path: pathlib.Path) -> None:
    """An index with no '***' delimiter does not lose user content on update."""
    # build a populated wiki with one folder and page
    wiki = _make_wiki(tmp_path, folders={'notes': ['readme']})

    # strip the structural delimiter, leaving bare prose
    index_path = tmp_path / 'notes' / '_index.md'
    head, *_ = index_path.read_text(encoding='utf-8').split('***')
    index_path.write_text(head + 'Orphaned prose, no delimiter.\n', encoding='utf-8')

    # prose is preserved rather than silently dropped
    wiki.update()
    updated = index_path.read_text(encoding='utf-8')
    assert 'Orphaned prose, no delimiter.' in updated


# ------ damage repair and refusal


@pytest.mark.parametrize(
    argnames='mangle',
    argvalues=['rewritten_marker', 'escaped_links', 'escaped_links_only'],
    ids=['marker-to-dashes', 'escaped-wikilinks', 'escaped-links-intact-marker'],
)
def test_update_repairs_formatter_mangled_index(
    tmp_path: pathlib.Path,
    mangle: str,
) -> None:
    """A formatter-mangled index is repaired in one update, never duplicated.

    A markdown formatter may rewrite the ``***`` delimiter to ``---`` (and
    backslash-escape the wikilinks), demoting the generated link block to
    user content. Update must reclaim the demoted links rather than render
    a fresh block above the stale one -- including when only the links are
    escaped and the delimiter survives, where a fresh block would demote
    the escaped originals below ``***`` and wedge lint red forever.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['api', 'design']})

    # mangle the core index the way a formatter hook would
    index_path = tmp_path / 'core' / '_index.md'
    text = index_path.read_text(encoding='utf-8')
    if mangle != 'rewritten_marker':
        text = text.replace('[[', '\\[\\[').replace(']]', '\\]\\]')
        text = text.replace('_index', '\\_index')
    if mangle != 'escaped_links_only':
        text = text.replace('***', '---')
    index_path.write_text(text, encoding='utf-8')

    # one update repairs the index; a second changes nothing
    wiki.update()
    assert wiki.update() == []
    repaired = index_path.read_text(encoding='utf-8')

    # exactly one link line per target, descriptions intact, no escaped residue
    for target in ('_index|..', 'core/api|api', 'core/design|design'):
        assert repaired.count(f'[[{target}]]') == 1
    assert 'The api page.' in repaired
    assert '\\[' not in repaired

    # user content survives below a single restored delimiter
    assert 'Overview of core.' in repaired
    delimiters = sum(1 for line in repaired.split('\n') if line.rstrip() == '***')
    assert delimiters == 1

    # the repair clears lint rather than leaving the signature flagged
    assert wiki.lint() == []


def test_update_reports_page_with_unclosed_frontmatter(
    tmp_path: pathlib.Path,
) -> None:
    """A page whose frontmatter never closes is left unwritten and named.

    A naive parse would consume unclosed frontmatter to EOF, letting the
    frontmatter insertion (``rfind('---')`` at the opener) wipe the whole
    body; prepending a fresh block instead would demote the authored
    ``---`` and fields to body text. Update must leave the file
    byte-identical and warn so the user closes the frontmatter rather
    than inheriting a nested block.
    """
    wiki = _make_wiki(tmp_path, folders={'notes': ['readme']})
    # an opening '---' with fields but no closing '---'
    page = tmp_path / 'notes' / 'readme.md'
    authored = '---\nname: readme\ndesc: Important.\n\nBody that must survive.\n'
    page.write_text(authored, encoding='utf-8')

    # update names the malformed frontmatter and never rewrites the page
    notices = _capture_notices(wiki)
    wiki.update()
    err = '\n'.join(event.description for event in notices)
    assert 'Malformed frontmatter' in err
    assert 'notes/readme.md' in err
    assert page.read_text(encoding='utf-8') == authored


def test_update_does_not_restamp_a_crlf_passthrough_page(
    tmp_path: pathlib.Path,
) -> None:
    """A CRLF passthrough page normalizes to LF without re-stamping its body.

    An unclosed-frontmatter page plans verbatim; its CRLF endings force a
    normalizing write, but the ``updated:`` re-stamp must not fire -- the
    parse left the body's ``updated:`` line as authored text, so rewriting it
    would corrupt the authored value.
    """
    wiki = _make_wiki(tmp_path, folders={'notes': ['readme']})
    page = tmp_path / 'notes' / 'readme.md'
    # unclosed frontmatter (passthrough) + a body line the parse reads as body,
    # written with CRLF so the byte probe forces a normalizing write
    authored = '---\nname: readme\ndesc: Important.\n\nupdated: KEEP-ME\n'
    page.write_bytes(authored.replace('\n', '\r\n').encode('utf-8'))

    wiki.update()

    text = page.read_text(encoding='utf-8')
    # the write normalized CRLF->LF, but the authored body line survives intact
    assert 'updated: KEEP-ME' in text
    assert '\r\n' not in text


@pytest.mark.parametrize(
    argnames='damage',
    argvalues=['', '---\nname: core\ndesc: Authored.\n'],
    ids=['emptied', 'unclosed-frontmatter'],
)
def test_update_refuses_truncated_index(
    tmp_path: pathlib.Path,
    damage: str,
) -> None:
    """An emptied/truncated index is skipped loudly, never silently rebuilt.

    Rebuilding an index emptied by a crash or torn write fresh on the next
    update would permanently discard the authored desc/created/category and
    the user-content body with exit 0. Update must leave the file
    byte-identical and name it with the recovery paths.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    index = tmp_path / 'core' / '_index.md'
    index.write_text(damage, encoding='utf-8')

    # update names the damaged index with the recovery paths and skips it
    notices = _capture_notices(wiki)
    wiki.update()
    err = '\n'.join(event.description for event in notices)
    assert 'core/_index.md' in err
    assert 'restore' in err
    assert 'delete' in err
    assert index.read_text(encoding='utf-8') == damage

    # deleting the file opts back into the rebuild
    index.unlink()
    wiki.update()
    rebuilt = index.read_text(encoding='utf-8')
    assert 'name: core' in rebuilt
    assert '[[core/design|design]]' in rebuilt


def test_update_refuses_nested_wiki(tmp_path: pathlib.Path) -> None:
    """A nested declared wiki refuses the sweep before anything mutates.

    A stray copy of a wiki inside itself (a backup, a vendored
    snapshot) would otherwise be absorbed -- every nested ``name:``
    rewritten against the outer root. Update refuses, naming the
    nested root, and the dry run refuses alike rather than previewing
    the absorption.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    # drop a full copy of the wiki (marker included) inside itself
    snapshot = tmp_path.parent / f'{tmp_path.name}_snapshot'
    shutil.copytree(tmp_path, snapshot)
    shutil.move(str(snapshot), str(tmp_path / 'backup'))
    before = {path: path.read_text(encoding='utf-8') for path in tmp_path.rglob('*.md')}

    # the write and the dry run both refuse, naming the nested root
    with pytest.raises(ValueError, match=r'encloses the wiki at: .*backup'):
        wiki.update()
    with pytest.raises(ValueError, match=r'encloses the wiki at: .*backup'):
        wiki.update(check=True)

    # nothing was rewritten
    after = {path: path.read_text(encoding='utf-8') for path in tmp_path.rglob('*.md')}
    assert after == before


def test_update_ignores_wikis_above_the_root(tmp_path: pathlib.Path) -> None:
    """An unscoped update/lint never refuses over a marker above its own root.

    The enclosing-wiki guard covers ancestors strictly between a scope and
    the root; a declared wiki whose parent directory happens to carry its
    own ``.wiki/settings.json`` is a proper root, and the unscoped sweep
    must not probe above it.
    """
    # declare a wiki, then declare another directly above it
    outer = tmp_path / 'outer'
    (outer / '.wiki').mkdir(parents=True)
    (outer / '.wiki' / 'settings.json').write_text('{}\n', encoding='utf-8')
    wiki = _make_wiki(outer / 'mywiki', folders={'core': ['design']})

    # the unscoped sweep, the dry run, and lint all run clean
    assert wiki.update() == []
    assert wiki.update(check=True) == []
    assert wiki.lint() == []


def test_update_refuses_conflict_markers(tmp_path: pathlib.Path) -> None:
    """Conflict-marked files refuse the sweep before anything mutates.

    A half-resolved merge would otherwise ride the rewrite -- the plan
    reads the markers as authored content and bakes them into the
    regenerated files. Update refuses, naming every marked file, and
    the dry run refuses alike rather than previewing the damage; a
    well-formed ``no-lint`` region sanctions marker-shaped lines (e.g.
    a git tutorial).
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design', 'api']})
    # plant a real conflict in a page and an index
    conflict = '\n<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> branch\n'
    marked = {}
    for rel in ('core/design.md', 'core/_index.md'):
        path = tmp_path / rel
        marked[path] = path.read_text(encoding='utf-8')
        path.write_text(marked[path] + conflict, encoding='utf-8')
    before = {path: path.read_text(encoding='utf-8') for path in tmp_path.rglob('*.md')}

    # the write and the dry run both refuse, naming every marked file
    message = r'Merge conflict markers in: core/_index\.md, core/design\.md'
    with pytest.raises(ValueError, match=message):
        wiki.update()
    with pytest.raises(ValueError, match=message):
        wiki.update(check=True)

    # nothing was rewritten
    after = {path: path.read_text(encoding='utf-8') for path in tmp_path.rglob('*.md')}
    assert after == before

    # resolving the conflicts unblocks the sweep; a no-lint region keeps
    # deliberate marker lines writable, and update never strips them
    for path, text in marked.items():
        path.write_text(text, encoding='utf-8')
    page = tmp_path / 'core' / 'api.md'
    page.write_text(
        page.read_text(encoding='utf-8')
        + '\n<!-- start: no-lint -->\n<<<<<<< HEAD\n<!-- end: no-lint -->\n',
        encoding='utf-8',
    )
    wiki.update()
    assert '<<<<<<< HEAD' in page.read_text(encoding='utf-8')


@pytest.mark.parametrize('kind', ['folder', 'page'])
def test_update_survives_backslash_digit_name(
    tmp_path: pathlib.Path,
    kind: str,
) -> None:
    r"""A backslash-digit in a folder/page name does not crash update or lint.

    The name flows from the path into the ``name:`` refresh; a literal
    ``\1`` must not be read as a regex group reference (which would otherwise
    abort the whole run on a single oddly-named file).
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})

    # author an entry whose name contains a backslash followed by a digit
    if kind == 'folder':
        name = 'a\\1b'
        folder = tmp_path / name
        folder.mkdir()
        (folder / '_index.md').write_text(
            '---\nname: a\\1b\ndesc: A section.\n---\n\n# a\\1b\n\n***\n\nText.\n',
            encoding='utf-8',
        )
        target = folder / '_index.md'
    else:
        name = 'pg\\1'
        target = tmp_path / 'core' / 'pg\\1.md'
        target.write_text(
            '---\nname: pg\\1\ndesc: A page.\n---\n\n# pg\\1\n\nBody.\n',
            encoding='utf-8',
        )

    # update and lint both complete (no group-reference crash) and the
    # backslash-digit name survives verbatim
    wiki.update()
    assert name in target.read_text(encoding='utf-8')
    wiki.lint()
    assert wiki.update() == []


def test_repair_frontmatter_survives_backslash_timestamp() -> None:
    r"""A backslash in the rendered timestamp stamps verbatim, not a group ref.

    The created/updated stamps mirror the ``name:`` callable-repl safety: a
    user ``timestamp.format`` may render a literal ``\1`` that would otherwise
    abort update on any node.
    """
    fm = '---\nname: pg\ndesc: A page.\ncreated:\nupdated:\n---\n'
    out = format.repair_frontmatter(fm, name='pg', now='2026\\1-ts')
    assert 'created: 2026\\1-ts' in out
    assert 'updated: 2026\\1-ts' in out


# ------ desc parsing and prose placement


@pytest.mark.parametrize(
    argnames='header',
    argvalues=['|', '|-', '|+', '>-', '>+', '|2'],
    ids=['pipe', 'pipe-strip', 'pipe-keep', 'fold-strip', 'fold-keep', 'pipe-indent'],
)
def test_update_accepts_block_scalar_desc(
    tmp_path: pathlib.Path,
    header: str,
) -> None:
    """Idiomatic YAML block-scalar desc headers parse without aborting update.

    ``desc: |-`` and friends are the documented multi-line form; they must
    not raise and abort the whole run on a single page.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})

    # author a child page whose desc uses a block-scalar header
    page = tmp_path / 'core' / 'design.md'
    page.write_text(
        f'---\nname: design\ndesc: {header}\n  A multi-line summary.\n'
        '---\n\n# design\n\nBody.\n',
        encoding='utf-8',
    )

    # update completes and the parent index picks up the block desc
    wiki.update()
    core_index = (tmp_path / 'core' / '_index.md').read_text(encoding='utf-8')
    assert 'A multi-line summary.' in core_index


def test_update_restores_valueless_desc_but_keeps_a_real_block_scalar(
    tmp_path: pathlib.Path,
) -> None:
    """A present-but-valueless desc is restored; a real block-scalar body stays.

    A bare ``desc:``, a quoted-empty ``desc: ''``, and an empty ``desc: |``
    all read as no description; update restores the ``desc: ...`` placeholder
    so the page is legibly undescribed -- while a genuine block-scalar desc is
    preserved intact.
    """
    wiki = Wiki(tmp_path)
    wiki.init(name='Root')
    valueless = {'bare': 'desc:', 'quoted': "desc: ''", 'empty_block': 'desc: |'}
    for stem, desc_line in valueless.items():
        page = tmp_path / f'{stem}.md'
        page.write_text(
            f'---\nname: {stem}\n{desc_line}\n---\n\n# {stem}\n\nBody.\n',
            encoding='utf-8',
        )
    # a real block-scalar desc must survive the repair untouched
    real = tmp_path / 'real.md'
    real.write_text(
        '---\nname: real\ndesc: |\n  A real multi-line\n  description.\n'
        '---\n\n# real\n\nBody.\n',
        encoding='utf-8',
    )
    wiki.update()
    for stem in valueless:
        assert 'desc: ...' in (tmp_path / f'{stem}.md').read_text(encoding='utf-8')
    real_text = real.read_text(encoding='utf-8')
    assert 'A real multi-line' in real_text
    assert 'description.' in real_text


@pytest.mark.parametrize(
    argnames='header',
    argvalues=['|', '|-', '>', '>-'],
    ids=['pipe', 'pipe-strip', 'fold', 'fold-strip'],
)
def test_update_accepts_block_scalar_name(
    tmp_path: pathlib.Path,
    header: str,
) -> None:
    """A block-scalar ``name:`` resolves to its body, not the ``|``/``>`` token.

    The reader must yield the actual name; a bare ``|-``/``>`` indicator would
    otherwise corrupt the rendered H1 to ``# |-`` and trip the invalid-name
    check, so update keeps the wiki name intact.
    """
    wiki = Wiki(tmp_path)
    wiki.init(name='root')

    # author the root index with a block-scalar name
    root_index = tmp_path / '_index.md'
    body = root_index.read_text(encoding='utf-8')
    body = re.sub(r'^name:.*$', f'name: {header}\n  KeptName', body, flags=re.MULTILINE)
    root_index.write_text(body, encoding='utf-8')
    # a fresh instance reads the authored name (instances are one-shot:
    # init cached the pre-edit root name)
    wiki = Wiki(tmp_path)

    # the resolved name is the body text, not the indicator
    frontmatter, _, _ = format.parse_index(
        root_index.read_text(encoding='utf-8'),
        delimiter=wiki.index_delimiter,
    )
    assert format.read_frontmatter_name(frontmatter) == 'KeptName'

    # lint does not flag a bogus '|-' wiki name, and update keeps the real name
    assert not any('Invalid wiki name' in issue for issue in wiki.lint())
    wiki.update()
    assert wiki._root_name == 'KeptName'


def test_update_joins_multiline_block_scalar_name(tmp_path: pathlib.Path) -> None:
    """A multi-line block-scalar ``name:`` joins to a single-line name.

    Repair writes the resolved name back as a plain ``name:`` scalar and
    the H1 renders on one line, so a raw newline would land a stray
    unindented frontmatter line and a second H1 line that the next parse
    folds into user content; the joined name keeps both surfaces intact.
    """
    wiki = Wiki(tmp_path)
    wiki.init(name='root')

    # author the root index with a multi-line block-scalar name
    root_index = tmp_path / '_index.md'
    body = root_index.read_text(encoding='utf-8')
    scalar = 'name: |\n  Line One\n  Line Two'
    body = re.sub(r'^name:.*$', scalar, body, flags=re.MULTILINE)
    root_index.write_text(body, encoding='utf-8')
    # a fresh instance reads the authored name (instances are one-shot:
    # init cached the pre-edit root name)
    wiki = Wiki(tmp_path)

    # the name folds onto the header line and renders one H1 line
    wiki.update()
    updated = root_index.read_text(encoding='utf-8')
    assert 'name: Line One Line Two\n' in updated
    assert '# Line One Line Two\n' in updated
    assert Wiki(tmp_path).update() == []


def test_update_folds_and_preserves_inline_desc(tmp_path: pathlib.Path) -> None:
    """A folded ``desc: >`` joins lines with a space; inline text is not dropped.

    ``>`` is the YAML folded scalar (newlines collapse to spaces), and inline
    text on a ``|``/``>`` header line is the value when no indented body
    follows -- both must propagate to the parent index intact.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['folded', 'inline']})

    # a folded desc spread across two lines
    (tmp_path / 'core' / 'folded.md').write_text(
        '---\nname: folded\ndesc: >\n  alpha beta\n  gamma delta.\n'
        '---\n\n# folded\n\nBody.\n',
        encoding='utf-8',
    )
    # inline text on the indicator line, no indented body
    (tmp_path / 'core' / 'inline.md').write_text(
        '---\nname: inline\ndesc: | inline summary here.\n---\n\n# inline\n\nBody.\n',
        encoding='utf-8',
    )

    # both descriptions propagate to the parent index, folded onto one line
    wiki.update()
    core_index = (tmp_path / 'core' / '_index.md').read_text(encoding='utf-8')
    assert 'alpha beta gamma delta.' in core_index
    assert 'inline summary here.' in core_index


def test_update_preserves_prose_above_delimiter(tmp_path: pathlib.Path) -> None:
    """Prose placed above the '***' delimiter is preserved, not silently dropped.

    Everything between the H1 and the first link/delimiter must survive
    re-render even though it matches neither a link nor a continuation line.
    """
    wiki = _make_wiki(tmp_path, folders={'notes': ['readme']})

    # insert prose between the H1 and the first generated link
    index_path = tmp_path / 'notes' / '_index.md'
    head, sep, tail = index_path.read_text(encoding='utf-8').partition('# notes\n')
    rewritten = f'{head}{sep}\nPreamble prose above the delimiter.\n{tail}'
    index_path.write_text(rewritten, encoding='utf-8')

    # the prose survives the update round-trip
    wiki.update()
    assert 'Preamble prose above the delimiter.' in index_path.read_text(
        encoding='utf-8'
    )


def test_update_preserves_prose_below_delimiter_above_h1(
    tmp_path: pathlib.Path,
) -> None:
    """Prose placed *before* the H1 is folded in once, not duplicated as an H1.

    A lead paragraph above the title leaves the regenerated H1 mid-preamble;
    update must drop that single H1 (wherever it sits) and keep exactly one,
    preserving the prose below ``***`` and staying idempotent.
    """
    wiki = _make_wiki(tmp_path, folders={'notes': ['readme']})

    # place prose between the closing frontmatter '---' and the H1
    index_path = tmp_path / 'notes' / '_index.md'
    head, sep, tail = index_path.read_text(encoding='utf-8').partition('# notes\n')
    rewritten = f'{head}Lead paragraph before the title.\n\n{sep}{tail}'
    index_path.write_text(rewritten, encoding='utf-8')

    # update keeps exactly one H1, preserves the prose, and is idempotent
    wiki.update()
    first = index_path.read_text(encoding='utf-8')
    assert first.count('# notes') == 1
    assert 'Lead paragraph before the title.' in first
    wiki.update()
    assert index_path.read_text(encoding='utf-8') == first


# ------ broken links and notices


def test_update_broken_links(tmp_path: pathlib.Path) -> None:
    """Update preserves broken links by default, prunes when asked."""
    # build a populated wiki with one folder and page
    wiki = _make_wiki(tmp_path, folders={'data': ['report']})

    # delete the page to create a broken link
    (tmp_path / 'data' / 'report.md').unlink()

    # update preserves broken link
    wiki.update()
    index = (tmp_path / 'data' / '_index.md').read_text(encoding='utf-8')
    assert 'report' in index

    # update with prune removes broken link
    wiki.update(prune=True)
    index = (tmp_path / 'data' / '_index.md').read_text(encoding='utf-8')
    assert 'report' not in index


def test_update_emits_every_notice(tmp_path: pathlib.Path) -> None:
    """Core update emits every notice line; output modes are the CLI's job.

    The library layer stays pure detail -- no caps, no thresholds, no
    memory across runs -- so the CLI's condensed default (one count line
    per category) always aggregates from complete information.
    """
    pages = [f'page{i}' for i in range(8)]
    wiki = _make_wiki(tmp_path, folders={'core': pages})
    for page in pages:
        (tmp_path / 'core' / f'{page}.md').unlink()
    notices = _capture_notices(wiki)

    # every preserved broken link is warned, run over run (stateless)
    for _ in range(2):
        notices.clear()
        wiki.update()
        detailed = [
            event.description
            for event in notices
            if event.description.startswith('Broken link:')
        ]
        assert len(detailed) == 8


def test_update_announces_created_index(tmp_path: pathlib.Path) -> None:
    """An auto-created index is announced with a fill-in-desc hint.

    Every new directory otherwise costs a silent placeholder that the next
    lint nags about; naming the created index at creation time (alongside
    the ``New link:`` notices) puts the fill step where the work happened.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    orphan = tmp_path / 'orphan'
    orphan.mkdir()
    (orphan / 'note.md').write_text(
        '---\nname: note\ndesc: A note.\n---\n\n# note\n\nSome text here.\n',
        encoding='utf-8',
    )
    notices = _capture_notices(wiki)

    # the created index is named with the fill hint; a re-run stays quiet
    wiki.update()
    err = '\n'.join(event.description for event in notices)
    assert 'New index: orphan/_index.md (fill in its desc)' in err
    notices.clear()
    wiki.update()
    assert 'New index:' not in '\n'.join(event.description for event in notices)


def test_update_announces_adoption(tmp_path: pathlib.Path) -> None:
    """Adopting a bare page is announced, naming the page and seeded title.

    Adoption rewrites the file wholesale -- frontmatter added, H1
    rewritten -- so the act is named when it happens, saying whether an
    authored H1 was preserved through a seeded ``title:``; a converged
    re-run stays quiet.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    (tmp_path / 'core' / 'titled.md').write_text(
        '# The L25 Wall\n\nBody prose.\n',
        encoding='utf-8',
    )
    (tmp_path / 'core' / 'plain.md').write_text('Body prose only.\n', encoding='utf-8')
    notices = _capture_notices(wiki)

    # each adoption is announced once, saying whether a title was seeded
    wiki.update()
    err = '\n'.join(event.description for event in notices)
    assert (
        'Adopted bare page: core/titled.md'
        ' (frontmatter added; title: seeded from its H1)'
    ) in err
    assert 'Adopted bare page: core/plain.md (frontmatter added)' in err

    # a converged re-run stays quiet
    notices.clear()
    wiki.update()
    assert 'Adopted' not in '\n'.join(event.description for event in notices)


def test_update_announces_desc_overwrite(tmp_path: pathlib.Path) -> None:
    """A diverged index-side desc is reverted with a notice, never silently.

    The child's frontmatter ``desc`` is the source of truth: update
    regenerates the link line from it, so an index-side hand-edit is
    overwritten on the next run -- announced, naming the entry and the
    place to edit.
    """
    wiki = _make_wiki(tmp_path, folders={'notes': ['readme']})
    index_path = tmp_path / 'notes' / '_index.md'

    # first-time propagation (a new link, seeded '...') stays quiet
    (tmp_path / 'notes' / 'fresh.md').write_text(
        '---\nname: fresh\ndesc: A fresh page.\n---\n\n# fresh\n\nText.\n',
        encoding='utf-8',
    )
    notices = _capture_notices(wiki)
    wiki.update()
    assert 'Overwrote desc:' not in '\n'.join(event.description for event in notices)

    # hand-edit the index-side desc away from the page frontmatter desc
    content = index_path.read_text(encoding='utf-8')
    index_path.write_text(
        content.replace('The readme page.', 'Hand-edited description.'),
        encoding='utf-8',
    )

    # the revert is announced, naming the entry and the place to edit
    notices.clear()
    wiki.update()
    err = '\n'.join(event.description for event in notices)
    assert (
        'Overwrote desc: [[notes/readme|readme]] in notes/_index.md'
        ' (the page frontmatter desc wins; edit it in notes/readme.md)'
    ) in err
    final = index_path.read_text(encoding='utf-8')
    assert 'The readme page.' in final
    assert 'Hand-edited description.' not in final

    # a converged re-run stays quiet
    notices.clear()
    wiki.update()
    assert 'Overwrote desc:' not in '\n'.join(event.description for event in notices)


# ------ convergence and scoping


def test_update_trailing_whitespace_desc_converges_quietly(
    tmp_path: pathlib.Path,
) -> None:
    """A block-scalar desc with trailing spaces converges without notices.

    ``format.parse_index`` never preserves trailing spaces, so the
    propagated desc is normalized on the write side -- otherwise every
    converged run would re-announce a phantom overwrite.
    """
    wiki = _make_wiki(tmp_path, folders={'notes': ['readme']})
    # author a desc whose first continuation line carries trailing spaces
    (tmp_path / 'notes' / 'padded.md').write_text(
        '---\nname: padded\ndesc: |\n  First line   \n  second line.\n'
        '---\n\n# padded\n\nText.\n',
        encoding='utf-8',
    )
    wiki.update()
    notices = _capture_notices(wiki)

    # the desc propagates rstripped -- no trailing spaces reach the index
    text = (tmp_path / 'notes' / '_index.md').read_text(encoding='utf-8')
    assert '[[notes/padded|padded]]: First line\nsecond line.' in text

    # a converged re-run stays quiet and writes nothing
    assert wiki.update() == []
    assert 'Overwrote desc:' not in '\n'.join(event.description for event in notices)


def test_update_rewrapped_desc_converges_quietly(
    tmp_path: pathlib.Path,
) -> None:
    """A link desc rewrapped in the index converges without changes.

    Line breaks inside a link desc are formatter-owned: a row whose
    desc matches the page frontmatter up to newlines is already
    converged, so update keeps the index's own wrapping and reverts
    only content changes.
    """
    wiki = _make_wiki(tmp_path, folders={'notes': ['readme']})
    index_path = tmp_path / 'notes' / '_index.md'
    (tmp_path / 'notes' / 'wrapped.md').write_text(
        '---\nname: wrapped\ndesc: A deliberately long description that a'
        ' formatter would wrap onto two lines.\n---\n\n# wrapped\n\nText.\n',
        encoding='utf-8',
    )
    wiki.update()

    # rewrap the row in the index the way a 72-column formatter would
    content = index_path.read_text(encoding='utf-8')
    single = (
        '[[notes/wrapped|wrapped]]: A deliberately long description that a'
        ' formatter would wrap onto two lines.'
    )
    wrapped = (
        '[[notes/wrapped|wrapped]]: A deliberately long description that a\n'
        'formatter would wrap onto two lines.'
    )
    assert single in content
    index_path.write_text(content.replace(single, wrapped), encoding='utf-8')
    notices = _capture_notices(wiki)

    # the rewrapped row is converged: no notice, no write, breaks kept
    assert wiki.update() == []
    assert 'Overwrote desc:' not in '\n'.join(event.description for event in notices)
    assert wrapped in index_path.read_text(encoding='utf-8')


def test_update_blank_line_in_desc_row_converges_quietly(
    tmp_path: pathlib.Path,
) -> None:
    """A formatter-inserted blank line inside a link desc converges quietly.

    Blank lines are formatter-owned the same as line breaks: mdformat inserts
    a blank line before a block in a propagated desc, so the fold-compare must
    collapse whitespace runs -- otherwise the double break reads as a content
    change and update churns a spurious overwrite every run.
    """
    wiki = _make_wiki(tmp_path, folders={'notes': ['readme']})
    index_path = tmp_path / 'notes' / '_index.md'
    (tmp_path / 'notes' / 'multi.md').write_text(
        '---\nname: multi\ndesc: A first clause and a second clause.\n---\n'
        '\n# multi\n\nText.\n',
        encoding='utf-8',
    )
    wiki.update()

    # a formatter inserts a blank line inside the row's desc continuation
    content = index_path.read_text(encoding='utf-8')
    single = '[[notes/multi|multi]]: A first clause and a second clause.'
    with_blank = '[[notes/multi|multi]]: A first clause and\n\na second clause.'
    assert single in content
    index_path.write_text(content.replace(single, with_blank), encoding='utf-8')
    notices = _capture_notices(wiki)

    # the blank-line row is converged: no overwrite notice, no churn
    assert wiki.update() == []
    assert 'Overwrote' not in '\n'.join(event.description for event in notices)


def test_update_converges_on_wrap_mangled_desc(tmp_path: pathlib.Path) -> None:
    """A wrap-mangled desc propagates verbatim, converges, and stays flagged.

    Update owns propagation, not mending: the dangling line break flows
    into the index link row as-is, a second update is a byte no-op, and
    the artifact stays lint's to flag in page and index alike.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    page = tmp_path / 'core' / 'design.md'
    index = tmp_path / 'core' / '_index.md'
    page.write_text(
        page.read_text(encoding='utf-8').replace(
            'desc: The design page.',
            'desc: |\n  supports twenty-\n  class workloads.',
        ),
        encoding='utf-8',
    )
    wiki.update()

    # the mangled break lands in the link row and the run converges
    text = index.read_text(encoding='utf-8')
    assert '[[core/design|design]]: supports twenty-\nclass workloads.' in text
    assert wiki.update() == []
    assert index.read_text(encoding='utf-8') == text

    # lint flags the artifact in both files
    issues = wiki.lint()
    assert any(issue.startswith('core/design.md: Hyphen dangle') for issue in issues)
    assert any(issue.startswith('core/_index.md: Hyphen dangle') for issue in issues)


def test_update_scoped(tmp_path: pathlib.Path) -> None:
    """Scoped update only modifies the specified subtree."""
    # build a populated wiki with two sibling folders
    folders = {
        'core': ['design'],
        'api': ['endpoints'],
    }
    wiki = _make_wiki(tmp_path, folders=folders)

    # capture the out-of-scope sibling's bytes to prove scoping skips it
    api_files = [tmp_path / 'api' / '_index.md', tmp_path / 'api' / 'endpoints.md']
    before = [path.read_bytes() for path in api_files]

    # add a new page to core only
    (tmp_path / 'core' / 'new_page.md').write_text(
        '---\nname: new_page\ndesc: New.\n---\n\n# new_page\n\nNew content.\n',
        encoding='utf-8',
    )

    # scoped update to core reports the new page, and only core files
    updated = wiki.update(name='core')
    assert 'core/new_page.md' in updated
    for path in updated:
        assert 'core' in path

    # the out-of-scope sibling files are byte-identical
    assert [path.read_bytes() for path in api_files] == before


def test_reclaimed_index_keeps_link_shaped_continuation(
    tmp_path: pathlib.Path,
) -> None:
    r"""A mangled-delimiter reclaim never promotes an escaped continuation.

    The reclaim undoes formatter escapes only for the leading-escape
    damage shapes (``\[\[`` / ``\[[``); a healthy continuation escapes
    inside its brackets (``[\[``), so unescaping it too would invent a
    link the folder never expected -- a phantom broken-link entry that
    survives every later update until pruned by hand.
    """
    wiki = _make_wiki(tmp_path, folders={'sub': ['child']})
    # a root page the continuation names, and a child desc continuing
    # onto a link-shaped second line
    (tmp_path / 'other.md').write_text(
        '---\nname: other\ndesc: Another page.\n---\n\n# other\n\nText.\n',
        encoding='utf-8',
    )
    (tmp_path / 'sub' / 'child.md').write_text(
        '---\nname: child\ndesc: |\n  A child page.\n'
        '  [[other|link]]: looks like a link.\n'
        '---\n\n# child\n\nBody.\n',
        encoding='utf-8',
    )
    wiki.update()
    index_path = tmp_path / 'sub' / '_index.md'
    text = index_path.read_text(encoding='utf-8')
    assert '[\\[other|link]]: looks like a link.' in text

    # rewrite the delimiter the way a formatter hook would
    index_path.write_text(text.replace('***', '---'), encoding='utf-8')

    # the reclaim keeps the escape as desc text: no phantom [[other|link]]
    wiki.update()
    repaired = index_path.read_text(encoding='utf-8')
    assert '[\\[other|link]]: looks like a link.' in repaired
    assert '[[other|link]]' not in repaired
    assert wiki.update() == []
    assert wiki.lint() == []


def test_body_edits_never_dirty_the_tree(tmp_path: pathlib.Path) -> None:
    """A body edit rewrites nothing: no count churn, no ancestor cascade.

    Derived counts live in the cache, not frontmatter, so growing a deep
    page leaves the page and every ancestor index byte-identical
    (``updated:`` included) while map reads the new count through the
    cache's lazy mtime-based recompute.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design'], 'core/store': ['db']})
    assert wiki.lint() == []
    indexes = [
        tmp_path / '_index.md',
        tmp_path / 'core' / '_index.md',
        tmp_path / 'core' / 'store' / '_index.md',
    ]
    before = [path.read_bytes() for path in indexes]
    # grow a deep page's body (an ordinary hand edit)
    db = tmp_path / 'core' / 'store' / 'db.md'
    db.write_text(
        db.read_text(encoding='utf-8').replace('Content for db.', 'word ' * 40),
        encoding='utf-8',
    )

    # the tree stays converged -- nothing flagged, nothing rewritten
    assert wiki.lint() == []
    assert wiki.update() == []
    assert [path.read_bytes() for path in indexes] == before
    # map reflects the new count anyway (H1 = 2 words, plus the 40)
    assert 'db (42)' in wiki.map()


# ------ frontmatter repair


@page_index
def test_update_fills_blank_frontmatter_values(
    tmp_path: pathlib.Path,
    kind: str,
) -> None:
    """Blank ``desc:``/``created:``/``updated:`` values are filled, not skipped.

    A present-but-empty key satisfies a naive add-if-missing guard, so a
    blank ``created:`` would never be stamped (and would pass lint) while a
    blank ``desc:`` would silently skip both lint branches. Update must fill
    each in place -- exactly once, never appending a duplicate key.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})

    # author frontmatter whose keys are present but blank
    if kind == 'page':
        target = tmp_path / 'core' / 'design.md'
        target.write_text(
            '---\nname: design\ndesc:\ncreated:\nupdated:\n---\n\n# design\n\nBody.\n',
            encoding='utf-8',
        )
    else:
        target = tmp_path / 'core' / '_index.md'
        target.write_text(
            '---\nname: core\ndesc:\ncreated:\nupdated:\n---\n\n'
            '# core\n\n***\n\nOverview of core.\n',
            encoding='utf-8',
        )
    wiki.update()
    text = target.read_text(encoding='utf-8')

    # timestamps are stamped with the run's clock, the desc placeholder restored
    assert re.search(r'^created: \S', text, re.M)
    assert re.search(r'^updated: \S', text, re.M)
    assert 'desc: ...' in text

    # each key appears exactly once (filled in place, never appended again)
    for key in ('desc', 'created', 'updated'):
        assert len(re.findall(rf'^{key}:', text, re.M)) == 1

    # the fill is idempotent
    assert wiki.update() == []


@pytest.mark.parametrize(
    argnames='frontmatter',
    argvalues=[
        '---\nname: design\ndesc: The design page.\n---',
        '---\nname: design\ndesc: The design page.\nupdated: 2026-01-01T00:00:00Z\n---',
    ],
    ids=['fresh-fields', 'missing-created-only'],
)
def test_update_inserts_timestamps_in_canonical_order(
    tmp_path: pathlib.Path,
    frontmatter: str,
) -> None:
    """A missing ``created:`` is inserted before ``updated:``.

    The canonical frontmatter order ends ``created, updated``; a plain
    append at the closing ``---`` would land a seeded ``created`` after
    ``updated``, so insertion must slot it ahead of the later key.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    page = tmp_path / 'core' / 'design.md'
    page.write_text(f'{frontmatter}\n\n# design\n\nBody.\n', encoding='utf-8')
    wiki.update()

    # the seeded keys land in canonical order
    fields = re.findall(
        pattern=r'^(name|desc|created|updated):',
        string=page.read_text(encoding='utf-8'),
        flags=re.M,
    )
    assert fields == ['name', 'desc', 'created', 'updated']


@page_index
def test_update_inserts_desc_in_schema_order(
    tmp_path: pathlib.Path,
    kind: str,
) -> None:
    """A missing ``desc:`` is inserted directly after ``name:``.

    The canonical frontmatter order opens ``name, desc``; the repair
    seeds the placeholder at its schema slot rather than appending it at
    the closing ``---``, so pages and indexes converge on the same
    documented key order.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})

    # author frontmatter that never carried a desc key
    if kind == 'page':
        target = tmp_path / 'core' / 'design.md'
        target.write_text(
            '---\nname: design\ncreated: 2026-01-01T00:00:00Z\n'
            'updated: 2026-01-01T00:00:00Z\n---\n\n# design\n\nBody.\n',
            encoding='utf-8',
        )
    else:
        target = tmp_path / 'core' / '_index.md'
        target.write_text(
            '---\nname: core\ncreated: 2026-01-01T00:00:00Z\n'
            'updated: 2026-01-01T00:00:00Z\n---\n\n'
            '# core\n\n***\n\nOverview of core.\n',
            encoding='utf-8',
        )
    wiki.update()

    # the seeded desc lands in schema position, directly after name
    fields = re.findall(
        pattern=r'^(name|desc|created|updated):',
        string=target.read_text(encoding='utf-8'),
        flags=re.M,
    )
    assert fields == ['name', 'desc', 'created', 'updated']


def test_update_enforces_canonical_field_order(tmp_path: pathlib.Path) -> None:
    """A scrambled frontmatter block lands in canonical order in one pass.

    Known keys sort into schema order; unrecognized authored keys sink
    below them, keeping their relative order, above the tool-owned
    ``created``/``updated`` tail. Extents move byte-verbatim (the block
    scalar keeps its body), and the result is converged.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    page = tmp_path / 'core' / 'design.md'
    page.write_text(
        '---\n'
        'updated: 2026-01-02T00:00:00Z\n'
        'alpha: first extra\n'
        'tags: [a]\n'
        'desc: |\n'
        '  A multi-line\n'
        '  description.\n'
        'created: 2026-01-01T00:00:00Z\n'
        'name: core/design\n'
        'zeta: second extra\n'
        'title: Fancy\n'
        'sources: []\n'
        'category: node\n'
        '---\n'
        '\n'
        '# Fancy\n'
        '\n'
        'Body.\n',
        encoding='utf-8',
    )
    wiki.update()
    text = page.read_text(encoding='utf-8')
    fields = re.findall(r'^(\w+):', text, re.M)
    assert fields == [
        'name',
        'title',
        'desc',
        'category',
        'tags',
        'sources',
        'alpha',
        'zeta',
        'created',
        'updated',
    ]
    # the block-scalar desc moved as one unit
    assert 'desc: |\n  A multi-line\n  description.\n' in text
    assert wiki.update() == []


# ------ category and desc propagation


@pytest.mark.parametrize(
    argnames=('frontmatter', 'expected'),
    argvalues=[
        ('category: store', 'store'),
        ('category:  node ', 'node'),
        ('category: null', ''),
        ("category: 'null'", 'null'),
        ('category: |\n  multi\n  line\n', 'multi line'),
        ('category:\n  multi\n  line\n', 'multi line'),
        ('category:\n', ''),
        ('tags: []\ncategory: input', 'input'),
        ('tags: [foo]', ''),
        ('name: test', ''),
    ],
    ids=[
        'simple',
        'whitespace',
        'null',
        'quoted-null',
        'block-joins',
        'plain-joins',
        'bare-no-body',
        'after-tags',
        'no-category',
        'absent',
    ],
)
def test_read_frontmatter_category(
    frontmatter: str,
    expected: str,
) -> None:
    """``format.read_frontmatter_category`` extracts the category field."""
    assert format.read_frontmatter_category(frontmatter) == expected


@pytest.mark.parametrize('quote', ['"', "'"], ids=['double', 'single'])
def test_quoted_desc_propagates_and_lints_clean(
    tmp_path: pathlib.Path,
    quote: str,
) -> None:
    """A quoted desc lints clean and propagates to the parent unquoted.

    YAML requires quoting a desc that contains ``: ``, so the frontmatter
    readers must strip one pair of matching surrounding quotes.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})

    # author a quoted desc whose ': ' is what forces the quoting
    page = tmp_path / 'core' / 'design.md'
    desc = 'Design notes: the full story.'
    page.write_text(
        page.read_text(encoding='utf-8').replace(
            'desc: The design page.',
            f'desc: {quote}{desc}{quote}',
        ),
        encoding='utf-8',
    )

    # the quoted, period-terminated desc is not flagged for a missing period
    wiki.update()
    assert not any('Missing period' in issue for issue in wiki.lint())

    # the parent link line carries the desc without the quotes
    core_index = (tmp_path / 'core' / '_index.md').read_text(encoding='utf-8')
    assert f'[[core/design|design]]: {desc}' in core_index


def test_update_category_labels(tmp_path: pathlib.Path) -> None:
    """Update generates bracketed categorized labels from child category fields."""
    # init a category-ordered wiki
    wiki = CategorizedWiki(tmp_path)
    wiki.init()
    # create child folders, some carrying a category field
    for name, category in [('cache', 'node'), ('db', 'store'), ('util', '')]:
        _make_category_folder(tmp_path, name, category, f'The {name} section.')
    # update generates bracketed, categorized links
    wiki.update()
    root_index = (tmp_path / '_index.md').read_text(encoding='utf-8')
    assert '[[cache/_index|[node] cache/]]' in root_index
    assert '[[db/_index|[store] db/]]' in root_index
    # uncategorized entry keeps a plain label
    assert '[[util/_index|util/]]' in root_index


def test_category_propagates_and_clears(tmp_path: pathlib.Path) -> None:
    """An authored category propagates on update; ``category: null`` resets.

    Fresh frontmatter carries no ``category:`` line; authoring one
    prefixes the parent label, and the null reset reverts the label and
    removes the line itself -- absence is the canonical unset form.
    """
    # init a category-ordered wiki
    wiki = CategorizedWiki(tmp_path)
    wiki.init()
    # create a folder with no category; first update -> plain parent label
    (tmp_path / 'cache').mkdir()
    wiki.update()
    root_index = tmp_path / '_index.md'
    index_path = tmp_path / 'cache' / '_index.md'
    assert '[[cache/_index|cache/]]' in root_index.read_text(encoding='utf-8')
    # author the child's category, then a single update propagates the prefix
    text = index_path.read_text(encoding='utf-8')
    index_path.write_text(
        text.replace('desc: ...\n', 'desc: ...\ncategory: node\n'),
        encoding='utf-8',
    )
    wiki.update()
    assert '[[cache/_index|[node] cache/]]' in root_index.read_text(encoding='utf-8')
    # the null reset reverts the parent label and removes the line itself
    text = index_path.read_text(encoding='utf-8')
    index_path.write_text(
        text.replace('category: node\n', 'category: null\n'),
        encoding='utf-8',
    )
    wiki.update()
    assert '[[cache/_index|cache/]]' in root_index.read_text(encoding='utf-8')
    assert 'category:' not in index_path.read_text(encoding='utf-8')
    assert wiki.update() == []


def test_sort_unlisted_category(tmp_path: pathlib.Path) -> None:
    """Categories not in category_order sort after listed ones, before uncategorized."""
    # init a category-ordered wiki
    wiki = CategorizedWiki(tmp_path)
    wiki.init()
    # listed category, unlisted category, and uncategorized
    _make_category_folder(tmp_path, 'a_listed', 'node')
    _make_category_folder(tmp_path, 'z_unlisted', 'widget')
    _make_category_folder(tmp_path, 'plain', '')
    wiki.update()
    root_index = (tmp_path / '_index.md').read_text(encoding='utf-8')
    listed = root_index.find('[node] a_listed/')
    unlisted = root_index.find('[widget] z_unlisted/')
    plain = root_index.find('|plain/]]')
    assert listed < unlisted < plain


def test_page_category(tmp_path: pathlib.Path) -> None:
    """A categorized .md page gets a bracketed prefix; a non-md file never does."""
    # init a category-ordered wiki
    wiki = CategorizedWiki(tmp_path)
    wiki.init()
    # a markdown page with a category field
    (tmp_path / 'design.md').write_text(
        '---\nname: design\ndesc: A design doc.\ncategory: node\n---\n\n# design\n\nBody.\n',
        encoding='utf-8',
    )
    # a non-markdown file has no frontmatter, so never a category
    (tmp_path / 'data.csv').write_text('a,b,c\n', encoding='utf-8')
    wiki.update()
    root_index = (tmp_path / '_index.md').read_text(encoding='utf-8')
    assert '[[design|[node] design]]' in root_index
    assert '[[data.csv|data.csv]]' in root_index


def test_fresh_frontmatter_carries_no_category_line(
    tmp_path: pathlib.Path,
) -> None:
    """Fresh frontmatter -- root, created index, adopted page -- has no category.

    Absence is the canonical unset form, so nothing seeds the field:
    categories exist only where authored.
    """
    wiki = Wiki(tmp_path)
    wiki.init(name='Root')
    (tmp_path / 'core').mkdir()
    (tmp_path / 'core' / 'design.md').write_text(
        '# design\n\nBody.\n',
        encoding='utf-8',
    )
    wiki.update()
    for relpath in ('_index.md', 'core/_index.md', 'core/design.md'):
        assert 'category:' not in (tmp_path / relpath).read_text(encoding='utf-8')


@pytest.mark.parametrize(
    argnames=('value', 'label'),
    argvalues=[
        ('', None),
        (' null', None),
        (' ~', '~'),
        (" 'null'", 'null'),
    ],
    ids=['blank', 'null', 'tilde', 'quoted-null'],
)
def test_update_removes_unset_category(
    tmp_path: pathlib.Path,
    value: str,
    label: Optional[str],
) -> None:
    """A blank or plain lowercase ``null`` category is removed; the rest stay.

    Absence is the canonical unset form, so update deletes only provably
    valueless lines -- the same reset idiom as titles. YAML's other null
    spellings (``~``) and a quoted ``'null'`` read as authored text and
    render literally in the parent label.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    index = tmp_path / 'core' / '_index.md'
    text = index.read_text(encoding='utf-8')
    index.write_text(
        text.replace('name: core\n', f'name: core\ncategory:{value}\n'),
        encoding='utf-8',
    )
    wiki.update()
    updated = index.read_text(encoding='utf-8')
    root_index = (tmp_path / '_index.md').read_text(encoding='utf-8')
    if label is None:
        assert 'category:' not in updated
        assert '[[core/_index|core/]]' in root_index
    else:
        assert f'category:{value}\n' in updated
        assert f'[[core/_index|[{label}] core/]]' in root_index
    assert wiki.update() == []


# ------ naming and escape stability


@pytest.mark.parametrize(
    argnames=('filename', 'fragment'),
    argvalues=[
        ('a|b.md', 'a|b'),
        ('report.v|1', 'report.v'),
    ],
    ids=['page-stem', 'file-suffix'],
)
def test_update_skips_invalid_name(
    tmp_path: pathlib.Path,
    filename: str,
    fragment: str,
) -> None:
    """A file whose name breaks the policy is skipped, warned, and never indexed.

    A denied char like ``|`` would otherwise emit a malformed ``[[a|b|a|b]]``
    link that grows the index every run -- and a non-markdown file links by its
    full name, so a denied char hiding in its suffix is caught too; update
    skips it (non-fatal) so the rest of the tree still updates and a second
    run is a no-op.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    # author a file whose linkable name holds a denied character
    (tmp_path / filename).write_text(
        '---\nname: x\ndesc: A page.\n---\n\n# x\n\nPipe page.\n',
        encoding='utf-8',
    )

    # update warns about the bad name but still processes the valid tree
    notices = _capture_notices(wiki)
    wiki.update()
    err = '\n'.join(event.description for event in notices)
    assert filename in err
    assert 'invalid name' in err
    root_index = (tmp_path / '_index.md').read_text(encoding='utf-8')
    assert fragment not in root_index
    assert '[[core/_index|core/]]' in root_index

    # a second update changes nothing: the skip never grows the index
    assert wiki.update() == []


def test_update_matches_decomposed_names(tmp_path: pathlib.Path) -> None:
    """A decomposed on-disk name matches the composed row the index holds.

    A checkout on an NFD-producing filesystem (HFS+, some network mounts)
    walks decomposed names while a sibling platform wrote the index rows
    composed: the link must match by normalized identity -- keeping its
    form and desc -- rather than break and re-add a duplicate row on
    every run.
    """
    nfc = 'café'
    nfd = unicodedata.normalize('NFD', nfc)
    wiki = _make_wiki(tmp_path)
    (tmp_path / f'{nfd}.md').write_text(
        f'---\nname: {nfc}\ndesc: The cafe page.\n---\n\n# {nfc}\n\nBody.\n',
        encoding='utf-8',
    )
    wiki.update()
    # rewrite the generated row to the composed form another platform wrote
    root_index = tmp_path / '_index.md'
    content = root_index.read_text(encoding='utf-8')
    root_index.write_text(content.replace(nfd, nfc), encoding='utf-8')

    # the composed row matches the decomposed file: kept verbatim, no notices
    notices = _capture_notices(wiki)
    assert wiki.update() == []
    err = '\n'.join(event.description for event in notices)
    assert 'Broken link' not in err
    assert 'New link' not in err
    updated = root_index.read_text(encoding='utf-8')
    assert f'[[{nfc}|{nfc}]]: The cafe page.' in updated


def test_sidecar_page_links_by_full_name(tmp_path: pathlib.Path) -> None:
    """A page named after a sibling file links by its full name, suffix included.

    Stripping ``Makefile.md`` beside ``Makefile`` would collide both
    entries on the raw file's target: duplicate index rows, the page's
    desc and word count rendered on the raw file's row, and the page
    unreachable by its own link (read resolves the literal file first).
    Each entry keeps its own row, desc, count, and read key instead.
    """
    wiki = _make_wiki(tmp_path, folders={'tools': []})
    (tmp_path / 'tools' / 'Makefile').write_text(
        'all:\n\techo hi\n',
        encoding='utf-8',
    )
    (tmp_path / 'tools' / 'Makefile.md').write_text(
        '---\nname: tools/Makefile\ndesc: Documentation for the build.\n---\n\n'
        '# tools/Makefile\n\nBuild docs.\n',
        encoding='utf-8',
    )
    wiki.update()
    # one row per entry: the raw file keeps the bare target and its own
    # (placeholder) desc; the page's row carries its full name and desc
    tools_index = (tmp_path / 'tools' / '_index.md').read_text(encoding='utf-8')
    assert tools_index.count('[[tools/Makefile|Makefile]]: ...') == 1
    page_row = '[[tools/Makefile.md|Makefile.md]]: Documentation for the build.'
    assert page_row in tools_index
    # each read key resolves its own entry
    assert wiki.read('tools/Makefile').startswith('all:')
    assert 'Build docs.' in wiki.read('tools/Makefile.md')
    # the page's word count renders on its own row only; the raw row stays bare
    out = wiki.map('tools')
    assert re.search(r'^Makefile \(0\)$', out, flags=re.MULTILINE)
    assert re.search(r'Makefile\.md \(4\): Documentation', out)
    # the layout is supported: converged and clean
    assert wiki.update() == []
    assert wiki.lint() == []


@pytest.mark.parametrize('command', ['update', 'lint'])
def test_undecodable_page_error_names_the_file(
    tmp_path: pathlib.Path,
    command: str,
) -> None:
    """A non-UTF-8 ``.md`` page aborts update/lint naming the culprit.

    The decode error alone carries only a byte offset -- unactionable on
    a tree of thousands of files -- so the re-raise appends the offending
    file's relative path.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    (tmp_path / 'core' / 'badpage.md').write_bytes(b'# bad\n\nbody \xff\xfe\n')
    with pytest.raises(UnicodeDecodeError, match=r'badpage\.md'):
        getattr(wiki, command)()


@pytest.mark.parametrize(
    argnames='hazard',
    argvalues=['***', '[[y|y]]: the other page.'],
    ids=['delimiter-line', 'link-shaped-line'],
)
def test_update_converges_on_structural_desc_lines(
    tmp_path: pathlib.Path,
    hazard: str,
) -> None:
    """A desc line shaped like index structure never corrupts the parent.

    A propagated multi-line desc renders its continuation lines at column
    0 inside the link block; a bare ``***`` line there would win the
    delimiter parse (every later link re-added as new, the index growing
    on every run) and a link-shaped line would render a phantom entry.
    """
    wiki = _make_wiki(tmp_path)
    for stem in ('x', 'y'):
        (tmp_path / f'{stem}.md').write_text(
            f'---\nname: {stem}\ndesc: The {stem} page.\n---\n\n# {stem}\n\nBody.\n',
            encoding='utf-8',
        )
    wiki.update()

    # author a block-scalar desc on x whose body holds the structural line
    page = tmp_path / 'x.md'
    page.write_text(
        f'---\nname: x\ndesc: |\n  Line one of desc.\n  {hazard}\n'
        '  Line after break.\n---\n\n# x\n\nBody.\n',
        encoding='utf-8',
    )
    wiki.update()

    # update converges (update-twice == update-once) ...
    assert wiki.update() == []
    # ... with a single real [[y|y]] link line and the desc text intact
    root_index = (tmp_path / '_index.md').read_text(encoding='utf-8')
    link_lines = [line for line in root_index.split('\n') if line.startswith('[[y|y]]')]
    assert len(link_lines) == 1
    assert 'Line after break.' in root_index

    # map renders y exactly once (no phantom entry from the desc line)
    output = wiki.map()
    assert len(re.findall(r'^y \(', output, re.M)) == 1


@pytest.mark.parametrize('kind', ['sub', 'root'])
def test_update_restores_missing_index_name(
    tmp_path: pathlib.Path,
    kind: str,
) -> None:
    """An index whose frontmatter lost ``name:`` (and ``desc:``) heals.

    If ``_plan_index`` only rewrote an existing name line (the desc add
    anchored on it), an index missing both would stay that way forever
    while update reported nothing to do and lint no issues -- for the
    root index, the display name would silently revert to the folder name.
    """
    wiki = _make_wiki(tmp_path, folders={'sub': ['page']})
    if kind == 'sub':
        index_path = tmp_path / 'sub' / '_index.md'
        expected = 'sub'
    else:
        index_path = tmp_path / '_index.md'
        expected = tmp_path.name
    # drop the name: and desc: lines from the frontmatter
    lines = index_path.read_text(encoding='utf-8').split('\n')
    lines = [line for line in lines if not line.startswith(('name:', 'desc:'))]
    index_path.write_text('\n'.join(lines), encoding='utf-8')
    # a fresh instance reads the damaged index (instances are one-shot:
    # the builder's init cached the pre-edit root name)
    wiki = Wiki(tmp_path)

    # one update restores both fields and converges
    wiki.update()
    healed = index_path.read_text(encoding='utf-8')
    assert re.search(rf'^name: {re.escape(expected)}$', healed, re.M)
    assert re.search(r'^desc:', healed, re.M)
    assert wiki.update() == []


def test_update_adds_missing_name(tmp_path: pathlib.Path) -> None:
    """Update adds a ``name:`` field to a page that lacks one."""
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    page = tmp_path / 'core' / 'design.md'
    page.write_text(
        '---\ndesc: A design doc.\n---\n# Design\n\nBody text here.\n',
        encoding='utf-8',
    )
    wiki.update()
    assert 'name:' in page.read_text(encoding='utf-8')


def test_update_detects_bom_prefixed_frontmatter(tmp_path: pathlib.Path) -> None:
    """A UTF-8 BOM before ``---`` does not defeat frontmatter detection.

    Common Windows editors write a BOM; treating the page as
    frontmatter-less would prepend a second generated block and demote
    the authored one -- with its real desc -- to body text.
    """
    wiki = _make_wiki(tmp_path)
    page = tmp_path / 'bommy.md'
    page.write_text(
        '\ufeff---\nname: bommy\ndesc: Authored desc.\n---\n\n# bommy\n\nBody.\n',
        encoding='utf-8',
    )
    wiki.update()

    # the authored block stays the (only) frontmatter, not body text
    text = page.read_text(encoding='utf-8')
    assert 'desc: ...' not in text
    # the authored desc propagates to the parent index link
    root_index = (tmp_path / '_index.md').read_text(encoding='utf-8')
    assert '[[bommy|bommy]]: Authored desc.' in root_index
    assert wiki.update() == []


def test_names_with_colon_write_quoted_yaml(tmp_path: pathlib.Path) -> None:
    """A name containing ``': '`` is written as valid, quoted YAML.

    ``name: Fractal: Notes`` is not YAML (a mapping value inside a plain
    scalar), breaking the Obsidian front-matter-title plugin the tool
    installs to render ``name:``. The writer must quote such values --
    the readers already unquote -- and the round trip must converge.
    """
    wiki = Wiki(tmp_path)
    wiki.init(name='Fractal: Notes')
    text = (tmp_path / '_index.md').read_text(encoding='utf-8')
    assert "name: 'Fractal: Notes'" in text
    assert '# Fractal: Notes' in text
    # the reader round-trips the unquoted value and update converges
    assert wiki._root_name == 'Fractal: Notes'
    assert wiki.update() == []

    # a folder named with ': ' quotes the path-joined child names too
    section = tmp_path / 'a: b'
    section.mkdir()
    (section / 'child.md').write_text('# child\n\nBody.\n', encoding='utf-8')
    wiki.update()
    child = (section / 'child.md').read_text(encoding='utf-8')
    assert "name: 'a: b/child'" in child
    index_text = (section / '_index.md').read_text(encoding='utf-8')
    assert "name: 'a: b'" in index_text
    assert wiki.update() == []


def test_titles_with_leading_block_indicator_write_quoted_yaml(
    tmp_path: pathlib.Path,
) -> None:
    """A title opening with a block-scalar indicator round-trips quoted.

    An authored H1 like ``# >50% Quorum`` seeds ``title: >50% Quorum``;
    written plain, the reader diverts the leading ``>`` into a block scalar
    and eats the indicator (reading ``% Quorum``), then rewrites the H1 from
    the mangled title -- silently destroying the heading. The writer must
    quote it, the reader already unquotes, and the round trip must converge.
    """
    wiki = Wiki(tmp_path)
    wiki.init(name='Root')
    page = tmp_path / 'quorum.md'
    page.write_text('# >50% Quorum\n\nBody text here.\n', encoding='utf-8')
    wiki.update()
    text = page.read_text(encoding='utf-8')
    assert "title: '>50% Quorum'" in text
    # the authored heading survives verbatim (not rewritten to '# % Quorum')
    assert '# >50% Quorum' in text
    assert wiki.update() == []


# ------ authored titles


@pytest.mark.parametrize('kind', ['index', 'root', 'page'])
def test_title_wins_heading_and_null_reverts(
    tmp_path: pathlib.Path,
    kind: str,
) -> None:
    """An authored ``title:`` wins the H1; ``title: null`` unsets it.

    ``name:`` stays path-derived throughout -- in particular the root's,
    which is read back from its own frontmatter, so a title-aware name
    resolution would rewrite ``name:`` to the title. A hand-mangled H1
    on a titled file is restored from the title, and ``title: null``
    removes the line and reverts the H1 to the name.
    """
    _make_wiki(tmp_path, folders={'core': ['design']})
    if kind == 'index':
        target, name = tmp_path / 'core' / '_index.md', 'core'
    elif kind == 'root':
        target, name = tmp_path / '_index.md', 'root'
    else:
        target, name = tmp_path / 'core' / 'design.md', 'core/design'

    # author a title directly under name; a fresh instance reads the
    # titled file (the builder's caches predate the edit)
    text = target.read_text(encoding='utf-8')
    target.write_text(
        text.replace(f'name: {name}\n', f'name: {name}\ntitle: Fancy\n'),
        encoding='utf-8',
    )
    wiki = Wiki(tmp_path)

    # one update rewrites the H1; name: keeps the path-derived value
    wiki.update()
    titled = target.read_text(encoding='utf-8')
    assert f'name: {name}\ntitle: Fancy\n' in titled
    assert '# Fancy\n' in titled
    assert f'# {name}\n' not in titled
    assert wiki.update() == []

    # a hand-mangled H1 is restored from the title in one update
    target.write_text(
        titled.replace('# Fancy\n', '# Mangled\n'),
        encoding='utf-8',
    )
    wiki.update()
    assert '# Fancy\n' in target.read_text(encoding='utf-8')

    # title: null unsets: the line is removed and the H1 reverts
    text = target.read_text(encoding='utf-8')
    target.write_text(
        text.replace('title: Fancy\n', 'title: null\n'),
        encoding='utf-8',
    )
    wiki.update()
    reverted = target.read_text(encoding='utf-8')
    assert 'title:' not in reverted
    assert f'# {name}\n' in reverted
    assert wiki.update() == []


@pytest.mark.parametrize('position', ['tail', 'mid-block', 'under-name'])
def test_update_repositions_title_under_name(
    tmp_path: pathlib.Path,
    position: str,
) -> None:
    """A title authored anywhere in the block lands directly under ``name:``.

    The canonical slot is directly under ``name`` -- one update moves the
    authored line there byte-verbatim, wherever it starts, and the
    result is converged.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    index = tmp_path / 'core' / '_index.md'
    text = index.read_text(encoding='utf-8')
    if position == 'tail':
        text = text.replace('\n---\n', '\ntitle: Fancy\n---\n', 1)
    elif position == 'mid-block':
        text = text.replace('\nupdated:', '\ntitle: Fancy\nupdated:', 1)
    else:
        text = text.replace('name: core\n', 'name: core\ntitle: Fancy\n')
    index.write_text(text, encoding='utf-8')

    # one update lands the verbatim line in its slot and converges
    wiki.update()
    updated = index.read_text(encoding='utf-8')
    assert 'name: core\ntitle: Fancy\ndesc: ' in updated
    assert wiki.update() == []


def test_update_inserts_desc_under_title(tmp_path: pathlib.Path) -> None:
    """A missing ``desc:`` slots below an under-name title.

    The desc insertion anchors on the ``name:`` line, which would push
    an under-name title down to name/desc/title order; title
    normalization runs last, so one update ends in name/title/desc
    schema order.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    index = tmp_path / 'core' / '_index.md'
    text = index.read_text(encoding='utf-8')
    text = text.replace('name: core\n', 'name: core\ntitle: Fancy\n')
    text = text.replace('desc: The core section.\n', '')
    index.write_text(text, encoding='utf-8')

    # one update restores the placeholder in schema order and converges
    wiki.update()
    fields = re.findall(
        pattern=r'^(name|title|desc|created|updated):',
        string=index.read_text(encoding='utf-8'),
        flags=re.M,
    )
    assert fields == ['name', 'title', 'desc', 'created', 'updated']
    assert wiki.update() == []


@pytest.mark.parametrize(
    argnames='scalar',
    argvalues=[
        'title: >-\n  A folded\n  headline\n',
        'title:\n  A folded\n  headline\n',
    ],
    ids=['block', 'plain'],
)
def test_multi_line_title_moves_as_one_unit(
    tmp_path: pathlib.Path,
    scalar: str,
) -> None:
    """A multi-line title moves with its body and folds to one H1 line.

    The field extent is the key line plus its indented body -- a block
    scalar, or a bare ``title:`` over a plain multi-line scalar -- so a
    reposition never strands continuation lines; the H1 folds the value
    to a single line (a raw newline would leak lines above the link
    block), and the tree is byte-converged afterwards.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    index = tmp_path / 'core' / '_index.md'
    text = index.read_text(encoding='utf-8')
    index.write_text(text.replace('\n---\n', f'\n{scalar}---\n', 1), encoding='utf-8')

    # the verbatim block lands under name and renders one folded H1 line
    wiki.update()
    updated = index.read_text(encoding='utf-8')
    assert f'name: core\n{scalar}' in updated
    assert '# A folded headline\n' in updated
    assert wiki.update() == []


@pytest.mark.parametrize(
    argnames=('value', 'heading'),
    argvalues=[
        ('', None),
        (' null', None),
        (' ~', '~'),
        (" 'null'", 'null'),
        (' "null"', 'null'),
    ],
    ids=['blank', 'null', 'tilde', 'single-quoted-null', 'double-quoted-null'],
)
def test_update_removes_valueless_title(
    tmp_path: pathlib.Path,
    value: str,
    heading: Optional[str],
) -> None:
    """A blank or plain lowercase ``null`` title is removed; the rest stay.

    Absence is the canonical unset form, so update deletes only provably
    valueless lines. YAML's other null spellings (``~``/``Null``/
    ``NULL``) and a quoted ``'null'`` are not the documented reset
    idiom -- they read as authored text and render literally as the H1.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    index = tmp_path / 'core' / '_index.md'
    text = index.read_text(encoding='utf-8')
    index.write_text(
        text.replace('name: core\n', f'name: core\ntitle:{value}\n'),
        encoding='utf-8',
    )
    wiki.update()
    updated = index.read_text(encoding='utf-8')
    if heading is None:
        assert 'title:' not in updated
        assert '# core\n' in updated
    else:
        assert f'title:{value}\n' in updated
        assert f'# {heading}\n' in updated
    assert wiki.update() == []


def test_quoted_colon_title_renders_unquoted_heading(
    tmp_path: pathlib.Path,
) -> None:
    """A ``title: 'A: B'`` value renders its H1 without the YAML quotes.

    A value containing ``': '`` must be quoted to stay valid YAML; the
    reader strips one pair of matching quotes, so the heading shows the
    authored text.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    index = tmp_path / 'core' / '_index.md'
    text = index.read_text(encoding='utf-8')
    index.write_text(
        text.replace('name: core\n', "name: core\ntitle: 'Core: Internals'\n"),
        encoding='utf-8',
    )
    wiki.update()
    updated = index.read_text(encoding='utf-8')
    assert "title: 'Core: Internals'\n" in updated
    assert '# Core: Internals\n' in updated
    assert wiki.update() == []


@pytest.mark.parametrize(
    argnames=('body', 'seeded'),
    argvalues=[
        ('# The L25 Wall\n\nBody prose.\n', 'title: The L25 Wall'),
        ('# null\n\nBody prose.\n', "title: 'null'"),
        ('# "Quoted Title"\n\nBody prose.\n', 'title: \'"Quoted Title"\''),
        ("# 'null'\n\nBody prose.\n", "title: '''null'''"),
        ('﻿# The L25 Wall\n\nBody prose.\n', 'title: The L25 Wall'),
        ('Body prose only.\n', None),
        ('﻿Body prose only.\n', None),
    ],
    ids=[
        'h1',
        'null-h1',
        'quoted-h1',
        'quoted-null-h1',
        'bom-h1',
        'no-h1',
        'bom-no-h1',
    ],
)
def test_update_adopts_bare_page_seeding_title(
    tmp_path: pathlib.Path,
    body: str,
    seeded: Optional[str],
) -> None:
    """Adopting a bare page seeds ``title:`` from its authored H1.

    Adding frontmatter to a frontmatterless page preserves the heading
    the author wrote -- the seeded title wins the H1 rewrite, a heading
    reading ``null`` is seeded quoted so it survives as text, and a
    heading wrapped in quote chars is seeded re-quoted so the read does
    not strip the authored quotes -- while a page with no H1 gains the
    path-joined heading, title-less. A UTF-8 BOM is dropped, so it
    neither hides the authored H1 nor lands mid-file under the fresh
    frontmatter.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    page = tmp_path / 'core' / 'notes.md'
    page.write_text(body, encoding='utf-8')
    wiki.update()

    adopted = page.read_text(encoding='utf-8')
    assert 'name: core/notes\n' in adopted
    if seeded:
        # the authored heading survives, preserved through the title
        assert f'name: core/notes\n{seeded}\n' in adopted
        heading = body.split('\n', 1)[0].lstrip('﻿')
        assert f'{heading}\n' in adopted
        assert '# core/notes' not in adopted
    else:
        # the invented heading is not authored, so it seeds no title
        assert '# core/notes\n' in adopted
        assert 'title:' not in adopted
    assert '﻿' not in adopted
    assert wiki.update() == []


def test_required_titles_seed_lint_and_flip_off(tmp_path: pathlib.Path) -> None:
    """``titles.required`` seeds placeholders, fails lint, and inverts null.

    With the setting on, update seeds ``title: null`` directly under
    ``name:`` on every index and page missing a title -- the placeholder
    is kept, never read as an unset request -- a second update is a byte
    no-op, and lint fails each placeholder until a value is authored.
    Flipping the setting off restores null-removal on the next update.
    """
    _make_wiki(tmp_path, folders={'core': ['design']})
    settings = tmp_path / '.wiki' / 'settings.json'
    settings.write_text(
        json.dumps({'titles': {'required': True}}) + '\n',
        encoding='utf-8',
    )
    # a fresh instance reads the new policy (settings cache per instance)
    wiki = Wiki(tmp_path)
    files = [
        tmp_path / '_index.md',
        tmp_path / 'core' / '_index.md',
        tmp_path / 'core' / 'design.md',
    ]

    # one update seeds every placeholder in schema position, then converges
    wiki.update()
    for path in files:
        text = path.read_text(encoding='utf-8')
        assert re.search(r'^name: .*\ntitle: null\n', text, re.M)
    assert wiki.update() == []

    # every placeholder is a hard lint issue until a value is authored
    issues = wiki.lint()
    assert len([issue for issue in issues if 'Missing title' in issue]) == len(files)
    for path in files[:-1]:
        path.write_text(
            path.read_text(encoding='utf-8').replace('title: null', 'title: Authored'),
            encoding='utf-8',
        )
    wiki.update()
    issues = wiki.lint()
    assert [issue for issue in issues if 'Missing title' in issue] == [
        'core/design.md: Missing title (author a value)'
    ]

    # flipping the setting off makes the leftover placeholder removable
    settings.write_text('{}\n', encoding='utf-8')
    wiki = Wiki(tmp_path)
    wiki.update()
    assert 'title:' not in files[-1].read_text(encoding='utf-8')
    assert wiki.lint() == []
    assert wiki.update() == []


def test_required_titles_adopts_no_h1_page(tmp_path: pathlib.Path) -> None:
    """Adopting a no-H1 page under ``titles.required`` stays lint-red.

    Adoption invents the path-joined H1 and the placeholder seed lands
    ``title: null`` beside it -- the invented heading is not authored,
    so it never satisfies the requirement -- and lint fails the page
    until a value is authored.
    """
    _make_wiki(tmp_path, folders={'core': ['design']})
    settings = tmp_path / '.wiki' / 'settings.json'
    settings.write_text(
        json.dumps({'titles': {'required': True}}) + '\n',
        encoding='utf-8',
    )
    # a fresh instance reads the new policy (settings cache per instance)
    wiki = Wiki(tmp_path)
    page = tmp_path / 'core' / 'notes.md'
    page.write_text('Body prose only.\n', encoding='utf-8')
    wiki.update()

    # the adopted page carries the invented H1 and the null placeholder
    adopted = page.read_text(encoding='utf-8')
    assert re.search(r'^name: core/notes\ntitle: null\n', adopted, re.M)
    assert '# core/notes\n' in adopted
    assert wiki.update() == []

    # the placeholder is a hard lint issue until a value is authored
    issue = 'core/notes.md: Missing title (author a value)'
    assert issue in wiki.lint()
    page.write_text(
        adopted.replace('title: null', 'title: Authored'),
        encoding='utf-8',
    )
    wiki.update()
    assert issue not in wiki.lint()


# ------ settings restoration


def test_update_materializes_missing_settings(tmp_path: pathlib.Path) -> None:
    """``update`` restores a missing root marker; a dry run never writes it.

    The wiki root is declared by ``.wiki/settings.json``: init writes it
    and update restores a lost one as ``{}`` (all defaults, never invented
    policy) with a notice. ``check=True`` leaves the missing marker
    untouched (naming it is the CLI resolver's job). A root
    ``_config/settings.json`` is never read, so update -- dry or writing
    -- and lint all refuse to proceed until it migrates.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    settings = tmp_path / '.wiki' / 'settings.json'
    settings.unlink()
    notices = _capture_notices(wiki)

    # a dry run tolerates the missing marker without restoring it
    assert wiki.update(check=True) == []
    assert not settings.exists()

    # a real update restores {} and announces the restoration, alone
    wiki.update()
    err = '\n'.join(event.description for event in notices)
    assert 'Restored missing' in err
    assert '_config/settings.json' not in err
    assert json.loads(settings.read_text(encoding='utf-8')) == {}

    # a legacy _config/settings.json refuses the write with migration
    # steps, whether or not the marker survived
    (tmp_path / '_config').mkdir()
    (tmp_path / '_config' / 'settings.json').write_text('{}\n', encoding='utf-8')
    with pytest.raises(ValueError, match=r'(?s)Legacy wiki layout.*wiki update'):
        wiki.update()
    settings.unlink()
    with pytest.raises(ValueError, match=r'move `_config/`'):
        wiki.update()
    assert not settings.exists()

    # the dry paths refuse alike: previewing the sweep would advertise
    # indexing _config/ as content, a plan the write then refuses to apply
    with pytest.raises(ValueError, match=r'(?s)Legacy wiki layout.*wiki update'):
        wiki.update(check=True)
    with pytest.raises(ValueError, match=r'(?s)Legacy wiki layout.*wiki update'):
        wiki.lint()


def test_update_skips_symlinked_page(tmp_path: pathlib.Path) -> None:
    """A symlinked ``.md`` page is never walked, read, or rewritten.

    ``write_atomic`` replaces its target via ``os.replace``, so reading a
    page through a symlink and writing it back would overwrite the symlink
    with a regular file holding the (possibly out-of-root) target's
    content -- exfiltrating an arbitrary local file into a tracked page.
    """
    root = tmp_path / 'wiki'
    root.mkdir()
    wiki = _make_wiki(root, folders={'sub': ['child']})
    # a sensitive file outside the wiki root
    secret = tmp_path / 'secret.md'
    secret.write_text(
        '---\nname: secret\ndesc: TOP SECRET\n---\n\n# secret\n\nsensitive.\n',
        encoding='utf-8',
    )
    # a symlinked page inside the wiki pointing at it
    link = root / 'sub' / 'leak.md'
    link.symlink_to(secret)

    wiki.update()
    # the symlink is left intact (not read, not replaced by a regular file)
    assert link.is_symlink()
    # its target's content never reaches a tracked page or the index
    sub_index = (root / 'sub' / '_index.md').read_text(encoding='utf-8')
    assert 'TOP SECRET' not in sub_index
    assert 'leak' not in sub_index


@pytest.mark.parametrize('filename', ['report.md', 'report.png'])
def test_update_names_symlinked_link_target(
    tmp_path: pathlib.Path,
    filename: str,
) -> None:
    """An index link whose target became a symlink names the exclusion.

    Symlinked files are dropped from the walk, so the link is no longer
    backed by an indexed entry -- but its target is still on disk, and a
    generic broken-link warning would send the user hunting for a deleted
    file. Update and lint name the symlink as the cause instead, and
    prune names the removal alongside it.
    """
    root = tmp_path / 'wiki'
    root.mkdir()
    wiki = _make_wiki(root, folders={'data': ['child']})
    # index a real file, then swap it for a symlink to an out-of-root file
    page = root / 'data' / filename
    if filename.endswith('.md'):
        content = '---\nname: report\ndesc: A page.\n---\n\n# report\n\nText.\n'
    else:
        content = 'raw bytes\n'
    page.write_text(content, encoding='utf-8')
    wiki.update()
    secret = tmp_path / 'secret'
    secret.write_text('outside\n', encoding='utf-8')
    page.unlink()
    page.symlink_to(secret)
    notices = _capture_notices(wiki)

    # the symlink is named as the cause, not a generic broken link
    wiki.update()
    err = '\n'.join(event.description for event in notices)
    assert 'Link targets a symlink:' in err
    assert 'symlinked files are not indexed' in err
    assert 'Broken link:' not in err
    # lint names the same cause, still as a hard issue
    issues = wiki.lint()
    joined = '\n'.join(issues)
    assert 'targets a symlink; symlinked files are not indexed' in joined
    assert 'Broken link' not in joined
    # prune still removes the row, naming both the removal and the cause
    notices.clear()
    wiki.update(prune=True)
    err = '\n'.join(event.description for event in notices)
    assert 'Pruned link:' in err
    assert 'Link targets a symlink:' in err


def test_update_skips_out_of_root_desc_propagation(tmp_path: pathlib.Path) -> None:
    """Desc propagation never dereferences a link target outside the root.

    A hand-authored (or merged) index link whose target escapes the wiki
    via ``..`` is preserved as a broken link, but the desc-propagation
    pass must not resolve it and copy the out-of-root file's ``desc:``
    into the generated link block -- that would exfiltrate a foreign
    file's content into a tracked wiki artifact.
    """
    root = tmp_path / 'wiki'
    root.mkdir()
    wiki = _make_wiki(root, folders={'sub': ['child']})
    # a file outside the wiki root carrying a desc an attacker wants leaked
    (tmp_path / 'secret.md').write_text(
        '---\nname: secret\ndesc: LEAKED SECRET\n---\n\n# secret\n\nx.\n',
        encoding='utf-8',
    )
    # inject a traversal link into the generated region of sub/_index.md
    # (index targets are root-relative, e.g. sub/child)
    sub_index = root / 'sub' / '_index.md'
    text = sub_index.read_text(encoding='utf-8')
    text = text.replace(
        '[[sub/child|child]]',
        '[[../secret|leaked]]: ...\n[[sub/child|child]]',
        1,
    )
    sub_index.write_text(text, encoding='utf-8')

    wiki.update()
    updated = sub_index.read_text(encoding='utf-8')
    # the link is preserved (propagation ran) but the out-of-root desc is
    # never read, so the secret never lands in the index
    assert '../secret' in updated
    assert 'LEAKED SECRET' not in updated


def test_update_escapes_damage_shaped_child_desc(tmp_path: pathlib.Path) -> None:
    r"""A child desc already carrying the ``\[\[`` damage shape is escaped.

    ``escape_desc`` neutralizes exactly the lines the reader
    (:func:`format.parse_index`) would promote to a link, and the reader
    repairs the leading-escape damage shape. So a child ``desc:`` whose
    continuation line is ``\[\[target|label\]\]`` must be escaped on
    propagation -- otherwise it survives verbatim into the parent index
    and is promoted to a phantom link on the next parse.
    """
    wiki = _make_wiki(tmp_path, folders={'sub': ['child']})
    # a child desc whose second line is a pre-damaged link (a block scalar
    # keeps the backslashes literal, as synced/merged frontmatter would)
    (tmp_path / 'sub' / 'child.md').write_text(
        '---\nname: child\ndesc: |\n  A child page.\n'
        r'  \[\[evil|../secret\]\]' + '\n'
        '---\n\n# child\n\nBody.\n',
        encoding='utf-8',
    )
    wiki.update()
    index = (tmp_path / 'sub' / '_index.md').read_text(encoding='utf-8')
    # escaped as a continuation, never promoted to a link
    assert r'[\[\[evil|../secret\]\]' in index
    assert '[[evil|../secret]]' not in index
    # a phantom link would resurface on the next parse; it must not, and
    # the escape is stable so the tree converges
    wiki.update()
    reparsed = (tmp_path / 'sub' / '_index.md').read_text(encoding='utf-8')
    assert '[[evil|../secret]]' not in reparsed
    assert wiki.update() == []
