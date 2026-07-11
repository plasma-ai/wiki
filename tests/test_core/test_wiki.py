"""Tests for ``Wiki`` init, config, update, lint, read, map, and validation."""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import threading
from typing import Any, Optional

import pytest

from wiki.core.wiki import _OFFLINE_MODE, Wiki, _format_words

__all__ = [
    'test_init_creates_structure',
    'test_update_full_workflow',
    'test_update_preserves_content',
    'test_update_preserves_content_with_thematic_break',
    'test_update_preserves_frontmatter_with_dashes',
    'test_update_no_delimiter_keeps_content',
    'test_update_repairs_formatter_mangled_index',
    'test_update_reports_page_with_unclosed_frontmatter',
    'test_update_refuses_truncated_index',
    'test_lint_reports_missing_root_name_without_crashing',
    'test_update_survives_backslash_digit_name',
    'test_update_accepts_block_scalar_desc',
    'test_update_accepts_block_scalar_name',
    'test_update_folds_and_preserves_inline_desc',
    'test_update_preserves_prose_above_delimiter',
    'test_update_preserves_prose_below_delimiter_above_h1',
    'test_validate_name_strict_via_settings',
    'test_naming_policy_knobs',
    'test_init_scaffolds_settings',
    'test_init_seeds_custom_settings',
    'test_init_rejects_bad_settings_before_writing',
    'test_init_rejects_invalid_wiki_name',
    'test_settings_reject_malformed_values',
    'test_timestamp_format_configurable',
    'test_timestamp_timezone_configurable',
    'test_map_presentation_configurable',
    'test_read_slice_units',
    'test_lint_flags_invalid_name',
    'test_update_broken_links',
    'test_update_emits_every_notice',
    'test_update_announces_created_index',
    'test_update_announces_desc_overwrite',
    'test_update_trailing_whitespace_desc_converges_quietly',
    'test_update_rewrapped_desc_converges_quietly',
    'test_update_scoped',
    'test_lint_flags_what_update_fixes',
    'test_lint_flags_human_only_issues',
    'test_lint_names_formatter_damage',
    'test_lint_names_formatter_damage_with_multiline_desc',
    'test_lint_truncated_index_is_not_formatter_damage',
    'test_link_shaped_desc_continuation_lints_clean',
    'test_reclaimed_index_keeps_link_shaped_continuation',
    'test_lint_allows_thematic_break_in_body',
    'test_lint_missing_index',
    'test_update_creates_self_ignoring_cache',
    'test_update_announces_recreated_cache',
    'test_body_edits_never_dirty_the_tree',
    'test_noop_update_leaves_updated_alone',
    'test_map_survives_cache_damage',
    'test_update_check_reports_without_writing',
    'test_lint_diff_set_matches_update',
    'test_lint_conflict_markers_suppress_diff',
    'test_lint_link_desc_period',
    'test_lint_scoped',
    'test_new_file_created_equals_updated',
    'test_update_fills_blank_frontmatter_values',
    'test_update_inserts_timestamps_in_canonical_order',
    'test_lint_flags_blank_created',
    'test_lint_ignores_code_blocks',
    'test_lint_ignores_multiline_code_span',
    'test_lint_conflict_markers_scan_raw',
    'test_no_lint_region_scopes_positional_rules',
    'test_region_directive_pairing_errors',
    'test_region_directives_pair_per_directive',
    'test_lint_clean',
    'test_read_resolution',
    'test_read_line_slicing',
    'test_operations_refuse_paths_outside_root',
    'test_read_frontmatter_category',
    'test_quoted_desc_propagates_and_lints_clean',
    'test_quoted_placeholder_desc_is_soft',
    'test_quoted_category_labels_and_filters',
    'test_update_category_labels',
    'test_category_propagates_and_clears',
    'test_sort_unlisted_category',
    'test_page_category',
    'test_map_output',
    'test_map_folds_multiline_desc',
    'test_map_unindexed',
    'test_map_word_counts',
    'test_body_includes_h1_for_counts_and_search',
    'test_search_field_matches_value_only',
    'test_map_handles_dotted_markdown_stem',
    'test_format_words',
    'test_update_config_installs_plugin',
    'test_update_config_offline_warns',
    'test_update_config_keeps_notices_off_warnings',
    'test_update_config_preserves_existing',
    'test_update_config_is_idempotent',
    'test_update_config_seeds_missing_config_dir',
    'test_update_config_rejects_type_mismatch',
    'test_update_config_reports_malformed_target_json',
    'test_update_config_offline_mode',
    'test_update_config_rejects_bad_offline_mode',
    'test_init_rejects_bad_offline_mode_before_scaffolding',
    'test_validate_name',
    'test_update_skips_invalid_name',
    'test_read_suggests_unique_leaf_match',
    'test_lint_stale_body_link_names_canonical',
    'test_markerless_index_warns_in_map_and_flags_in_lint',
    'test_lint_flags_folder_shadowing_page',
    'test_timestamp_format_rejects_blank_or_multiline',
    'test_update_preserves_concurrent_edit',
    'test_update_writes_are_atomic_for_concurrent_readers',
    'test_map_survives_binary_attachment',
    'test_undecodable_page_error_names_the_file',
    'test_update_converges_on_structural_desc_lines',
    'test_non_word_category_labels_filters_and_resolves',
    'test_update_preserves_file_mode',
    'test_update_restores_missing_index_name',
    'test_update_detects_bom_prefixed_frontmatter',
    'test_names_with_colon_write_quoted_yaml',
    'test_map_category_shows_matches_beyond_depth',
    'test_lint_accepts_anchor_links',
    'test_map_marks_copied_subtree_links_broken',
    'test_update_survives_page_deleted_mid_plan',
    'test_update_normalizes_crlf_file',
    'test_update_materializes_missing_settings',
    'test_init_refuses_legacy_layout_before_writing',
]


def test_init_creates_structure(tmp_path: pathlib.Path) -> None:
    """Init creates root index and config."""
    # init creates root _index.md
    root = tmp_path / 'wiki'
    wiki = Wiki(root)
    wiki.init()
    assert (root / '_index.md').is_file()

    # obsidian config template seeded
    assert (root / '.wiki' / 'obsidian').is_dir()

    # init is idempotent (doesn't overwrite user content)
    index = root / '_index.md'
    original = index.read_text(encoding='utf-8')
    wiki.init()
    assert index.read_text(encoding='utf-8') == original


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
        'category: null\n'
        'tags: []\n'
        '---\n'
        '# Topic\n\n'
        'Body content here.\n',
        encoding='utf-8',
    )

    # frontmatter fields and body survive; exactly one frontmatter block remains
    wiki.update()
    result = page.read_text(encoding='utf-8')
    assert 'category: null' in result
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


@pytest.mark.parametrize(
    'mangle',
    ['rewritten_marker', 'escaped_links', 'escaped_links_only'],
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
    capsys: pytest.CaptureFixture[str],
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
    wiki.update()
    err = capsys.readouterr().err
    assert 'Malformed frontmatter' in err
    assert 'notes/readme.md' in err
    assert page.read_text(encoding='utf-8') == authored


@pytest.mark.parametrize(
    'damage',
    ['', '---\nname: core\ndesc: Authored.\n'],
    ids=['emptied', 'unclosed-frontmatter'],
)
def test_update_refuses_truncated_index(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
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
    wiki.update()
    err = capsys.readouterr().err
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


def test_lint_reports_missing_root_name_without_crashing(
    tmp_path: pathlib.Path,
) -> None:
    """Stripping the root name does not crash lint; the index is flagged."""
    wiki = _make_wiki(tmp_path)
    root_index = tmp_path / '_index.md'
    stripped = '\n'.join(
        line
        for line in root_index.read_text(encoding='utf-8').splitlines()
        if not line.startswith('name:')
    )
    root_index.write_text(stripped, encoding='utf-8')
    # must not raise, and must surface the root index as out of date
    issues = wiki.lint()
    assert any('_index.md' in issue for issue in issues)


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
    assert wiki.update() is not None


@pytest.mark.parametrize(
    'header',
    ['|', '|-', '|+', '>-', '>+', '|2'],
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


@pytest.mark.parametrize(
    'header',
    ['|', '|-', '>', '>-'],
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

    # the resolved name is the body text, not the indicator
    frontmatter, _, _ = wiki._parse_index(root_index.read_text(encoding='utf-8'))
    assert wiki._read_frontmatter_name(frontmatter) == 'KeptName'

    # lint does not flag a bogus '|-' wiki name, and update keeps the real name
    assert not any('Invalid wiki name' in issue for issue in wiki.lint())
    wiki.update()
    assert wiki._root_name == 'KeptName'


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


def test_lint_flags_invalid_name(tmp_path: pathlib.Path) -> None:
    """An entry whose name breaks the policy is flagged, naming the file."""
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})

    # author an entry whose name violates the policy (a denied '#')
    bad_page = tmp_path / 'core' / 'bad#name.md'
    bad_page.write_text('not markdown frontmatter', encoding='utf-8')

    # the invalid name is reported and names the offending file
    issues = wiki.lint()
    invalid = [issue for issue in issues if 'Invalid page name' in issue]
    assert invalid
    assert all('bad#name' in issue for issue in invalid)


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


def test_update_emits_every_notice(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Core update emits every notice line; output modes are the CLI's job.

    The library layer stays pure detail -- no caps, no thresholds, no
    memory across runs -- so the CLI's condensed default (one count line
    per category) always aggregates from complete information.
    """
    pages = [f'page{i}' for i in range(8)]
    wiki = _make_wiki(tmp_path, folders={'core': pages})
    for page in pages:
        (tmp_path / 'core' / f'{page}.md').unlink()
    capsys.readouterr()

    # every preserved broken link is warned, run over run (stateless)
    for _ in range(2):
        wiki.update()
        err = capsys.readouterr().err
        detailed = [
            line for line in err.splitlines() if line.startswith('Broken link:')
        ]
        assert len(detailed) == 8


def test_update_announces_created_index(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
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
    capsys.readouterr()

    # the created index is named with the fill hint; a re-run stays quiet
    wiki.update()
    err = capsys.readouterr().err
    assert 'New index: orphan/_index.md (fill in its desc)' in err
    wiki.update()
    assert 'New index:' not in capsys.readouterr().err


def test_update_announces_desc_overwrite(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
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
    capsys.readouterr()
    wiki.update()
    assert 'Overwrote desc:' not in capsys.readouterr().err

    # hand-edit the index-side desc away from the page frontmatter desc
    content = index_path.read_text(encoding='utf-8')
    index_path.write_text(
        content.replace('The readme page.', 'Hand-edited description.'),
        encoding='utf-8',
    )

    # the revert is announced, naming the entry and the place to edit
    wiki.update()
    err = capsys.readouterr().err
    assert (
        'Overwrote desc: [[notes/readme|readme]] in notes/_index.md'
        ' (the page frontmatter desc wins; edit it in notes/readme.md)'
    ) in err
    final = index_path.read_text(encoding='utf-8')
    assert 'The readme page.' in final
    assert 'Hand-edited description.' not in final

    # a converged re-run stays quiet
    wiki.update()
    assert 'Overwrote desc:' not in capsys.readouterr().err


def test_update_trailing_whitespace_desc_converges_quietly(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A block-scalar desc with trailing spaces converges without notices.

    ``_parse_index`` never preserves trailing spaces, so the propagated
    desc is normalized on the write side -- otherwise every converged
    run would re-announce a phantom overwrite.
    """
    wiki = _make_wiki(tmp_path, folders={'notes': ['readme']})
    # author a desc whose first continuation line carries trailing spaces
    (tmp_path / 'notes' / 'padded.md').write_text(
        '---\nname: padded\ndesc: |\n  First line   \n  second line.\n'
        '---\n\n# padded\n\nText.\n',
        encoding='utf-8',
    )
    wiki.update()
    capsys.readouterr()

    # the desc propagates rstripped -- no trailing spaces reach the index
    text = (tmp_path / 'notes' / '_index.md').read_text(encoding='utf-8')
    assert '[[notes/padded|padded]]: First line\nsecond line.' in text

    # a converged re-run stays quiet and writes nothing
    assert wiki.update() == []
    assert 'Overwrote desc:' not in capsys.readouterr().err


def test_update_rewrapped_desc_converges_quietly(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
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
    capsys.readouterr()

    # the rewrapped row is converged: no notice, no write, breaks kept
    assert wiki.update() == []
    assert 'Overwrote desc:' not in capsys.readouterr().err
    assert wrapped in index_path.read_text(encoding='utf-8')


def test_update_scoped(tmp_path: pathlib.Path) -> None:
    """Scoped update only modifies the specified subtree."""
    # build a populated wiki with two sibling folders
    wiki = _make_wiki(
        tmp_path,
        folders={
            'core': ['design'],
            'api': ['endpoints'],
        },
    )

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


@pytest.mark.parametrize(
    'perturb',
    [
        'changed_link_label',
        'wrong_heading',
        'missing_field',
        'missing_marker',
        'missing_page_frontmatter',
    ],
)
def test_lint_flags_what_update_fixes(tmp_path: pathlib.Path, perturb: str) -> None:
    """Anything update would change is flagged, and one update clears it."""
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    assert wiki.lint() == []
    index = tmp_path / 'core' / '_index.md'
    page = tmp_path / 'core' / 'design.md'

    # perturb a clean wiki in a way only update can fix
    if perturb == 'changed_link_label':
        text = index.read_text(encoding='utf-8')
        index.write_text(
            text.replace('[[core/design|design]]', '[[core/design|WRONG]]'),
            encoding='utf-8',
        )
    elif perturb == 'wrong_heading':
        text = page.read_text(encoding='utf-8')
        page.write_text(
            text.replace('# core/design', '# Wrong Title'),
            encoding='utf-8',
        )
    elif perturb == 'missing_field':
        text = page.read_text(encoding='utf-8')
        page.write_text(
            '\n'.join(
                line for line in text.splitlines() if not line.startswith('updated:')
            )
            + '\n',
            encoding='utf-8',
        )
    elif perturb == 'missing_marker':
        text = index.read_text(encoding='utf-8')
        index.write_text(text.replace('***\n', ''), encoding='utf-8')
    elif perturb == 'missing_page_frontmatter':
        page.write_text('# design\n\nJust a body.\n', encoding='utf-8')

    # lint flags the drift; one update fixes it; lint is then clean
    assert wiki.lint() != []
    assert wiki.update() != []
    assert wiki.lint() == []
    assert wiki.update(check=True) == []


@pytest.mark.parametrize(
    ('perturb', 'message'),
    [
        ('invalid_folder', 'Invalid folder name'),
        ('invalid_page', 'Invalid page name'),
        ('invalid_nonmd', 'Invalid page name'),
        ('conflict_markers', 'Merge conflict markers'),
        ('broken_link', 'Broken link'),
        ('stale_user_link', 'Stale link'),
        ('missing_period', 'Missing period'),
        ('escaped_wikilink', 'Escaped wikilinks'),
        ('unclosed_frontmatter', 'Malformed frontmatter'),
        ('emptied_index', 'Empty or truncated index'),
    ],
)
def test_lint_flags_human_only_issues(
    tmp_path: pathlib.Path,
    perturb: str,
    message: str,
) -> None:
    """Problems update cannot fix are flagged and persist after update."""
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    page = tmp_path / 'core' / 'design.md'
    index = tmp_path / 'core' / '_index.md'

    if perturb == 'invalid_folder':
        bad = tmp_path / 'Bad#Folder'
        bad.mkdir()
        (bad / '_index.md').write_text(
            '---\nname: x\ndesc: A folder.\n---\n\n# x\n\n***\n\nText.\n',
            encoding='utf-8',
        )
    elif perturb == 'invalid_page':
        (tmp_path / 'core' / 'bad#name.md').write_text(
            '---\nname: x\ndesc: A page.\n---\n\n# x\n\nText.\n',
            encoding='utf-8',
        )
    elif perturb == 'invalid_nonmd':
        (tmp_path / 'core' / 'bad#data.csv').write_text('raw,data\n', encoding='utf-8')
    elif perturb == 'conflict_markers':
        page.write_text(
            page.read_text(encoding='utf-8')
            + '<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> branch\n',
            encoding='utf-8',
        )
    elif perturb == 'broken_link':
        page.unlink()
    elif perturb == 'stale_user_link':
        index.write_text(
            index.read_text(encoding='utf-8') + '\nSee [[ghost]] for more.\n',
            encoding='utf-8',
        )
    elif perturb == 'missing_period':
        page.write_text(
            page.read_text(encoding='utf-8').replace(
                'desc: The design page.',
                'desc: The design page',
            ),
            encoding='utf-8',
        )
    elif perturb == 'escaped_wikilink':
        page.write_text(
            page.read_text(encoding='utf-8').replace(
                'Content for design.',
                'See \\[\\[design notes\\]\\] for more.',
            ),
            encoding='utf-8',
        )
    elif perturb == 'unclosed_frontmatter':
        page.write_text(
            '---\nname: design\ndesc: The design page.\n\n# design\n\nBody.\n',
            encoding='utf-8',
        )
    elif perturb == 'emptied_index':
        index.write_text('', encoding='utf-8')

    # the issue is flagged, and update does not silence it
    assert any(message in issue for issue in wiki.lint())
    wiki.update()
    assert any(message in issue for issue in wiki.lint())


def test_lint_names_formatter_damage(tmp_path: pathlib.Path) -> None:
    """Lint names escaped wikilinks and a break standing where ``***`` belongs.

    A markdown formatter escaping ``[[...]]`` and rewriting the ``***``
    delimiter is the known corruption source for generated indexes; lint
    names the suspected cause and points at the exclusion docs so the
    first failure is diagnosable at a glance, and one update repairs it.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['api', 'design']})
    # mangle the core index the way a formatter hook would
    index_path = tmp_path / 'core' / '_index.md'
    text = index_path.read_text(encoding='utf-8')
    text = text.replace('[[', '\\[\\[').replace(']]', '\\]\\]')
    text = text.replace('***', '---')
    index_path.write_text(text, encoding='utf-8')

    # both damage signatures are named, pointing at the formatter cause
    issues = wiki.lint()
    escaped = [issue for issue in issues if 'Escaped wikilinks' in issue]
    assert escaped
    assert all('formatter' in issue for issue in escaped)
    mangled = [issue for issue in issues if 'thematic break' in issue]
    assert mangled
    assert all('formatter' in issue for issue in mangled)

    # one update repairs the index and clears the damage report
    wiki.update()
    assert not any('formatter' in issue for issue in wiki.lint())


def test_lint_names_formatter_damage_with_multiline_desc(
    tmp_path: pathlib.Path,
) -> None:
    """A rewritten delimiter under a multi-line desc keeps the diagnosis.

    Desc continuation lines ride directly under their link in the rendered
    run; the classifier must walk past them the way ``_reclaim_links``
    does, or the report degrades to the bare missing-delimiter message and
    hides the formatter cause.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    # propagate a two-line block-scalar desc into the core index
    page = tmp_path / 'core' / 'design.md'
    page.write_text(
        '---\nname: design\ndesc: |-\n  The design page.\n'
        '  Continued on a second line.\n---\n\n# design\n\nBody.\n',
        encoding='utf-8',
    )
    wiki.update()
    # rewrite the delimiter the way a formatter hook would
    index_path = tmp_path / 'core' / '_index.md'
    text = index_path.read_text(encoding='utf-8')
    index_path.write_text(text.replace('***', '---'), encoding='utf-8')

    # the diagnosis survives the continuation line in the link run
    assert any('thematic break' in issue for issue in wiki.lint())


@pytest.mark.parametrize('lead', ['', '\n'], ids=['at-top', 'blank-led'])
def test_lint_truncated_index_is_not_formatter_damage(
    tmp_path: pathlib.Path,
    lead: str,
) -> None:
    """A truncated index lints as truncation, never as formatter damage.

    Unclosed frontmatter extracts as none, leaving its own opening
    ``---`` as the first non-blank line; reading it as a rewritten
    ``***`` would point the user at formatter exclusions when the
    recovery paths are restore-or-delete. Genuine damage (closed
    frontmatter, delimiter rewritten) keeps the diagnosis.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    index = tmp_path / 'core' / '_index.md'
    healthy = index.read_text(encoding='utf-8')
    index.write_text(lead + '---\nname: core\ndesc: Authored.', encoding='utf-8')

    # the missing delimiter and the recovery paths are named, plainly
    issues = wiki.lint()
    assert 'core/_index.md: Index missing *** delimiter' in issues
    assert any('Empty or truncated index' in issue for issue in issues)
    assert not any('formatter' in issue for issue in issues)

    # a genuinely rewritten delimiter keeps the formatter diagnosis
    index.write_text(healthy.replace('***', '---'), encoding='utf-8')
    assert any('thematic break' in issue for issue in wiki.lint())


def test_link_shaped_desc_continuation_lints_clean(tmp_path: pathlib.Path) -> None:
    r"""A link-shaped desc continuation escapes without the damage signature.

    The escape lands inside the leading brackets (``[\[``), so a healthy
    propagated desc never carries the ``\[[`` shape lint reads as
    formatter damage -- the index converges and lints clean. A desc
    continuation that does carry the damage shape is repaired by one
    update re-propagating the child's desc.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['api', 'design']})
    # author a desc whose continuation line is itself wikilink-shaped
    page = tmp_path / 'core' / 'design.md'
    page.write_text(
        '---\nname: design\ndesc: |\n  The design page.\n'
        '  [[core/api|api]]: the database layer.\n'
        '---\n\n# design\n\nBody.\n',
        encoding='utf-8',
    )

    # the continuation escapes inside its brackets, converges, lints clean
    wiki.update()
    index_path = tmp_path / 'core' / '_index.md'
    text = index_path.read_text(encoding='utf-8')
    assert '[\\[core/api|api]]: the database layer.' in text
    assert wiki.update() == []
    assert wiki.lint() == []

    # a continuation carrying the damage shape heals on one update
    index_path.write_text(
        text.replace('[\\[core/api', '\\[[core/api'),
        encoding='utf-8',
    )
    assert any('Escaped wikilinks' in issue for issue in wiki.lint())
    assert wiki.update() != []
    assert wiki.lint() == []


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


def test_lint_allows_thematic_break_in_body(tmp_path: pathlib.Path) -> None:
    """A legitimate ``---`` horizontal rule in body prose is never flagged.

    Only a break standing where the ``***`` delimiter belongs signals
    formatter damage; ordinary thematic breaks below the delimiter are
    content.
    """
    wiki = _make_wiki(tmp_path, folders={'notes': ['readme']})
    index_path = tmp_path / 'notes' / '_index.md'
    content = index_path.read_text(encoding='utf-8')
    content += '\nAbove the rule.\n\n---\n\nBelow the rule.\n'
    index_path.write_text(content, encoding='utf-8')
    wiki.update()
    assert not any('formatter' in issue for issue in wiki.lint())


def test_lint_missing_index(tmp_path: pathlib.Path) -> None:
    """A folder without an index is reported; update creates it and lint clears."""
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    orphan = tmp_path / 'orphan'
    orphan.mkdir()
    (orphan / 'note.md').write_text(
        '---\nname: note\ndesc: A note.\n---\n\n# note\n\nSome text here.\n',
        encoding='utf-8',
    )
    assert any('orphan/: Missing index' in issue for issue in wiki.lint())
    wiki.update()
    assert (orphan / '_index.md').exists()
    assert wiki.lint() == []


def test_update_creates_self_ignoring_cache(tmp_path: pathlib.Path) -> None:
    """Update materializes ``.wiki/cache/`` with counts and a self-ignoring rule.

    Derived counts live in one git-ignored cache file rather than every
    page's frontmatter; the ``.wiki/cache/`` directory carries its own
    ``.gitignore`` (``*``) so no host repo configuration is needed.
    """
    _make_wiki(tmp_path, folders={'core': ['design']})
    gitignore = (tmp_path / '.wiki' / 'cache' / '.gitignore').read_text(
        encoding='utf-8'
    )
    assert gitignore.strip() == '*'
    # entries carry each page's body word count keyed by relative path
    counts = json.loads(
        (tmp_path / '.wiki' / 'cache' / 'word_counts.json').read_text(encoding='utf-8')
    )
    # the body is the H1 ('# core/design' = 2 words) plus three words of prose
    assert counts['core/design.md']['words'] == 5


def test_update_announces_recreated_cache(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A deleted ``.wiki/cache/`` is recreated with a notice, never silently.

    The cache is pure derived state, so recreating it is always safe --
    but a deletion undone without a word reads as if the delete never
    happened.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    shutil.rmtree(tmp_path / '.wiki' / 'cache')
    capsys.readouterr()

    # the recreation is announced and the cache is materialized again
    wiki.update()
    assert 'Recreated .wiki/cache/' in capsys.readouterr().err
    assert (tmp_path / '.wiki' / 'cache' / 'word_counts.json').is_file()

    # an ordinary refresh stays quiet
    wiki.update()
    assert 'Recreated .wiki/cache/' not in capsys.readouterr().err


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


def test_noop_update_leaves_updated_alone(tmp_path: pathlib.Path) -> None:
    """A touch-nothing update re-run never moves ``updated:``.

    With derived counts out of frontmatter, rewrites happen only on real
    generated-content changes, so a converged tree's files -- timestamps
    included -- are byte-stable across arbitrary update re-runs.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    page = tmp_path / 'core' / 'design.md'
    stamped = re.search(r'^updated: .+$', page.read_text(encoding='utf-8'), re.M)
    assert wiki.update() == []
    text = page.read_text(encoding='utf-8')
    assert re.search(r'^updated: .+$', text, re.M).group(0) == stamped.group(0)


@pytest.mark.parametrize('damage', ['missing', 'corrupt'], ids=['missing', 'corrupt'])
def test_map_survives_cache_damage(tmp_path: pathlib.Path, damage: str) -> None:
    """A missing or corrupt counts cache is rebuilt, never an error.

    The cache is pure derived state: the worst case for any damage is a
    full recompute.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    cache = tmp_path / '.wiki' / 'cache' / 'word_counts.json'
    if damage == 'missing':
        cache.unlink()
    else:
        cache.write_text('{not json', encoding='utf-8')

    # map still renders real counts, and the cache is rebuilt behind it
    output = wiki.map()
    assert re.search(r'design \(\d+\)', output)
    counts = json.loads(cache.read_text(encoding='utf-8'))
    assert 'core/design.md' in counts
    # serialization is key-sorted, so the cache is stable and diffable
    assert list(counts) == sorted(counts)


def test_update_check_reports_without_writing(tmp_path: pathlib.Path) -> None:
    """update(check=True) reports would-change files but writes nothing."""
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    page = tmp_path / 'core' / 'design.md'
    page.write_text(
        page.read_text(encoding='utf-8').replace('# core/design', '# Wrong Title'),
        encoding='utf-8',
    )
    perturbed = page.read_text(encoding='utf-8')
    # a dry run reports the page without touching disk
    would_change = wiki.update(check=True)
    assert 'core/design.md' in would_change
    assert page.read_text(encoding='utf-8') == perturbed
    # a real update then writes and clears the report
    assert wiki.update() != []
    assert wiki.update(check=True) == []


def test_lint_diff_set_matches_update(tmp_path: pathlib.Path) -> None:
    """In a dirty state, the files lint diffs match exactly what update would write."""
    wiki = _make_wiki(tmp_path, folders={'core': ['design'], 'core/store': ['db']})
    # perturb files at different depths
    page = tmp_path / 'core' / 'design.md'
    page.write_text(
        page.read_text(encoding='utf-8').replace('# core/design', '# Wrong Title'),
        encoding='utf-8',
    )
    store = tmp_path / 'core' / 'store' / '_index.md'
    store.write_text(
        store.read_text(encoding='utf-8').replace(
            '[[core/store/db|db]]',
            '[[core/store/db|WRONG]]',
        ),
        encoding='utf-8',
    )
    # the set of files lint diffs == the set update would write (a diff issue is
    # the only multi-line kind; its header is "<path>: Requires update")
    diff_paths = {
        issue.splitlines()[0].removesuffix(': Requires update')
        for issue in wiki.lint()
        if '\n' in issue
    }
    assert diff_paths == set(wiki.update(check=True))


def test_lint_conflict_markers_suppress_diff(tmp_path: pathlib.Path) -> None:
    """A conflict-markered file reports only the marker; its own diff is suppressed."""
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    # add conflict markers (which also drift the word count) to a page and an index
    for rel in ('core/design.md', 'core/_index.md'):
        path = tmp_path / rel
        path.write_text(
            path.read_text(encoding='utf-8')
            + '\n<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> branch\n',
            encoding='utf-8',
        )
    issues = wiki.lint()
    for rel in ('core/design.md', 'core/_index.md'):
        assert any(f'{rel}: Merge conflict markers' in issue for issue in issues)
        # the suppressed diff would have a "Requires update" header for the file
        assert not any(
            issue.splitlines()[0] == f'{rel}: Requires update' for issue in issues
        )


def test_lint_link_desc_period(tmp_path: pathlib.Path) -> None:
    """A link desc's missing period is flagged only when update would keep it.

    update propagates a child's real desc into the parent link, so a period-less
    link desc is the user's problem only when the child has no desc to override it.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    index = tmp_path / 'core' / '_index.md'
    page = tmp_path / 'core' / 'design.md'

    # child supplies a real desc -> update will overwrite the link -> not flagged
    index.write_text(
        index.read_text(encoding='utf-8').replace(
            'The design page.',
            'soon overwritten',
        ),
        encoding='utf-8',
    )
    assert not any('Missing period in [[' in issue for issue in wiki.lint())
    wiki.update()

    # child has only a placeholder -> the authored link desc survives -> flagged
    page.write_text(
        page.read_text(encoding='utf-8').replace('desc: The design page.', 'desc: ...'),
        encoding='utf-8',
    )
    index.write_text(
        index.read_text(encoding='utf-8').replace(
            '[[core/design|design]]: The design page.',
            '[[core/design|design]]: kept without a period',
        ),
        encoding='utf-8',
    )
    assert any(
        'Missing period in [[core/design|design]]' in issue for issue in wiki.lint()
    )


def test_lint_scoped(tmp_path: pathlib.Path) -> None:
    """Scoped lint(name=...) reports only issues within the named subtree."""
    wiki = _make_wiki(tmp_path, folders={'core': ['design'], 'api': ['spec']})
    # drift a page in each sibling folder
    for rel in ('core/design.md', 'api/spec.md'):
        path = tmp_path / rel
        path.write_text(
            path.read_text(encoding='utf-8').replace('# ' + rel[:-3], '# Wrong'),
            encoding='utf-8',
        )
    # a scoped lint mentions only the named subtree, never the sibling
    issues = wiki.lint(name='core')
    assert issues != []
    assert all('api' not in issue for issue in issues)


def test_new_file_created_equals_updated(tmp_path: pathlib.Path) -> None:
    """A file created and stamped in one update run gets created == updated."""
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    (tmp_path / 'core' / 'fresh.md').write_text(
        '# fresh\n\nA brand new page body.\n',
        encoding='utf-8',
    )
    wiki.update()
    fresh = (tmp_path / 'core' / 'fresh.md').read_text(encoding='utf-8')
    created = re.search(r'^created:\s*(.+)$', fresh, re.M).group(1)
    updated = re.search(r'^updated:\s*(.+)$', fresh, re.M).group(1)
    assert created == updated


@pytest.mark.parametrize('kind', ['page', 'index'])
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
    'frontmatter',
    [
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
        r'^(name|desc|created|updated):',
        page.read_text(encoding='utf-8'),
        re.M,
    )
    assert fields == ['name', 'desc', 'created', 'updated']


def test_lint_flags_blank_created(tmp_path: pathlib.Path) -> None:
    """Lint's update diff names a blank ``created:`` before update stamps it.

    A present-but-blank ``created:`` is lint-visible drift -- the
    generated diff shows the stamp update would apply -- rather than a
    silent pass that leaves the key empty forever.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    page = tmp_path / 'core' / 'design.md'
    page.write_text(
        '---\nname: core/design\ndesc: The design page.\ncreated:\n'
        'updated: 2026-01-01T00:00:00Z\n---\n\n# core/design\n\nBody.\n',
        encoding='utf-8',
    )
    issues = wiki.lint()
    flagged = [issue for issue in issues if 'Requires update' in issue]
    assert any('+created:' in issue for issue in flagged)


def test_lint_ignores_code_blocks(tmp_path: pathlib.Path) -> None:
    """Wikilinks inside code blocks are not flagged as stale."""
    wiki = Wiki(tmp_path)
    wiki.init()
    (tmp_path / 'page.md').write_text(
        '---\nname: page\ndesc: A page.\n---\n\n# page\n\n'
        '```\n[[nonexistent]]\n```\n\n`[[also_nonexistent]]`\n',
        encoding='utf-8',
    )
    wiki.update()
    issues = wiki.lint()
    stale = [i for i in issues if 'nonexistent' in i.lower()]
    assert not stale


def test_lint_ignores_multiline_code_span(tmp_path: pathlib.Path) -> None:
    """A wikilink in a code span wrapped across a newline is not stale.

    CommonMark allows an inline code span to wrap across a line break;
    per-line masking would let the wrapped span leak into the stale-link
    scan and false-flag its wikilink.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    page = tmp_path / 'core' / 'design.md'
    page.write_text(
        page.read_text(encoding='utf-8').replace(
            'Content for design.',
            'See `the [[nonexistent]]\nspan` for details.',
        ),
        encoding='utf-8',
    )
    wiki.update()
    assert not any('Stale link' in issue for issue in wiki.lint())


@pytest.mark.parametrize(
    ('body', 'flagged'),
    [
        ('A conflict starts with `<<<<<<< HEAD` inline.', False),
        ('```\n<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> branch\n```', True),
        (
            '<!-- start: no-lint -->\n\n'
            '```\n<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> branch\n```\n\n'
            '<!-- end: no-lint -->',
            False,
        ),
        ('<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> branch', True),
    ],
    ids=['inline-span', 'fenced-conflict', 'no-lint-region', 'real-conflict'],
)
def test_lint_conflict_markers_scan_raw(
    tmp_path: pathlib.Path,
    body: str,
    flagged: bool,
) -> None:
    """A conflict anywhere in the raw text is flagged unless suppressed.

    Masked scanning would go blind to a REAL merge conflict landing
    entirely inside a fenced block, so this one rule scans raw text -- a
    marker line (seven ``<``/``>`` at column 0) is never legitimate
    rendered content.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    page = tmp_path / 'core' / 'design.md'
    page.write_text(
        page.read_text(encoding='utf-8').replace('Content for design.', body),
        encoding='utf-8',
    )
    issues = wiki.lint()
    conflicts = [issue for issue in issues if 'Merge conflict markers' in issue]
    assert bool(conflicts) == flagged


def test_no_lint_region_scopes_positional_rules(tmp_path: pathlib.Path) -> None:
    """A ``no-lint`` region suppresses exactly the positional rules inside it.

    Conflict markers, formatter-escaped wikilinks, and stale links are
    attributable to lines, so a region silences them there; file-level
    checks ignore regions entirely.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    page = tmp_path / 'core' / 'design.md'
    body = (
        '<!-- start: no-lint -->\n'
        '<<<<<<< HEAD\n'
        'sample \\[[escaped]] and [[missing_inside]] links\n'
        '>>>>>>> branch\n'
        '<!-- end: no-lint -->\n'
        '\n'
        'A real [[missing_outside]] link.\n'
    )
    page.write_text(
        page.read_text(encoding='utf-8').replace('Content for design.\n', body),
        encoding='utf-8',
    )

    # inside the region nothing positional fires; outside still does
    issues = wiki.lint()
    assert not any('Merge conflict markers' in issue for issue in issues)
    assert not any('Escaped wikilinks' in issue for issue in issues)
    assert not any('missing_inside' in issue for issue in issues)
    assert any('Stale link [[missing_outside]]' in issue for issue in issues)

    # file-level checks ignore regions: a drifted H1 still requires update
    drifted = page.read_text(encoding='utf-8').replace(
        '# core/design',
        '<!-- start: no-lint -->\n# Wrong Title\n<!-- end: no-lint -->',
    )
    page.write_text(drifted, encoding='utf-8')
    issues = wiki.lint()
    assert any('Requires update' in issue for issue in issues)


@pytest.mark.parametrize(
    ('body', 'needle'),
    [
        # an unclosed start is a hard issue naming its line, and its
        # suppression never takes effect
        (
            '<!-- start: no-lint -->\n<<<<<<< HEAD\n',
            "Dangling '<!-- start: no-lint -->'",
        ),
        # an end with no open start is a hard issue
        ('<!-- end: no-lint -->\n', "Dangling '<!-- end: no-lint -->'"),
        # a second start before the end is a hard issue (no nesting), and
        # the poisoned outer pair suppresses nothing
        (
            '<!-- start: no-lint -->\n<!-- start: no-lint -->\n'
            '<<<<<<< HEAD\n<!-- end: no-lint -->\n',
            "Nested '<!-- start: no-lint -->'",
        ),
    ],
    ids=['dangling-start', 'dangling-end', 'nested-start'],
)
def test_region_directive_pairing_errors(
    tmp_path: pathlib.Path,
    body: str,
    needle: str,
) -> None:
    """Same-directive nesting and dangling markers are hard lint issues."""
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    page = tmp_path / 'core' / 'design.md'
    page.write_text(
        page.read_text(encoding='utf-8').replace('Content for design.\n', body),
        encoding='utf-8',
    )
    issues = wiki.lint()
    assert any(needle in issue and 'line' in issue for issue in issues)
    # a malformed region suppresses nothing
    if '<<<<<<<' in body:
        assert any('Merge conflict markers' in issue for issue in issues)


def test_region_directives_pair_per_directive(tmp_path: pathlib.Path) -> None:
    """Each directive pairs as its own bracket stream; fenced markers are inert.

    Cross-directive interleaving is legal (independent streams need no
    nesting discipline between each other), unknown-but-well-formed
    directives are inert, and a marker inside a code fence is a sample,
    not a directive -- it neither opens a region nor dangles.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    page = tmp_path / 'core' / 'design.md'
    body = (
        '<!-- start: no-lint -->\n'
        '<!-- start: future-directive with-args -->\n'
        '<<<<<<< HEAD\n'
        '<!-- end: no-lint -->\n'
        '<!-- end: future-directive -->\n'
        '\n'
        '```\n'
        '<!-- start: no-lint -->\n'
        '```\n'
    )
    page.write_text(
        page.read_text(encoding='utf-8').replace('Content for design.\n', body),
        encoding='utf-8',
    )
    issues = wiki.lint()
    # interleaved pairs are both well-formed: no pairing issues, and the
    # no-lint region still suppresses the marker it wraps
    assert not any('Dangling' in issue for issue in issues)
    assert not any('Nested' in issue for issue in issues)
    assert not any('Merge conflict markers' in issue for issue in issues)


def test_lint_clean(tmp_path: pathlib.Path) -> None:
    """A properly structured wiki produces no lint issues."""
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    issues = wiki.lint()
    assert issues == []


@pytest.mark.parametrize(
    ('name', 'path_suffix'),
    [
        ('core', 'core/_index.md'),
        ('core/design', 'core/design.md'),
    ],
    ids=['folder', 'page'],
)
def test_read_resolution(
    tmp_path: pathlib.Path,
    name: str,
    path_suffix: str,
) -> None:
    """Read resolves names to folder indexes and pages."""
    _make_wiki(tmp_path, folders={'core': ['design']})
    wiki = Wiki(tmp_path)
    content = wiki.read(name)
    expected = (tmp_path / path_suffix).read_text(encoding='utf-8')
    assert content == expected


def test_read_line_slicing(tmp_path: pathlib.Path) -> None:
    """Read with start/stop slices by lines (the default), preserving frontmatter."""
    # init a wiki and author a multi-line page, then update
    wiki = Wiki(tmp_path)
    wiki.init()
    (tmp_path / 'long.md').write_text(
        '---\nname: long\ndesc: A long page.\n---\n\n# long\n\n'
        'line one\nline two\nline three\nline four\n',
        encoding='utf-8',
    )
    wiki.update()

    # read with stop slices by line index (the default), keeping frontmatter
    content = wiki.read('long', stop=3)
    assert 'line one' in content
    assert 'line four' not in content
    assert 'name: long' in content


@pytest.mark.parametrize(
    ('operation', 'name'),
    [
        ('read', '../outside/secret'),
        ('read', '{outside}/secret.md'),
        ('search', '..'),
        ('map', '../outside'),
        ('update', '..'),
        ('lint', '../outside'),
    ],
    ids=[
        'read-relative',
        'read-absolute',
        'search-parent',
        'map-sibling',
        'update-parent',
        'lint-sibling',
    ],
)
def test_operations_refuse_paths_outside_root(
    tmp_path: pathlib.Path,
    operation: str,
    name: str,
) -> None:
    """Name-taking operations refuse to resolve targets outside the wiki root.

    Wiki names are agent-supplied input: a relative or absolute name whose
    target escapes the root must be rejected -- never read, searched, mapped,
    or rewritten -- even when the target exists.
    """
    # build a wiki beside a sibling file it must never reach
    root = tmp_path / 'wiki'
    wiki = _make_wiki(root)
    outside = tmp_path / 'outside'
    outside.mkdir()
    secret = outside / 'secret.md'
    secret.write_text('Secret content.\n', encoding='utf-8')

    # bind the escaping name to the operation under test
    name = name.format(outside=outside)
    calls = {
        'read': lambda: wiki.read(name),
        'search': lambda: wiki.search('Secret', name=name),
        'map': lambda: wiki.map(name),
        'update': lambda: wiki.update(name),
        'lint': lambda: wiki.lint(name),
    }

    # the escaping name is refused ...
    with pytest.raises(ValueError, match='outside wiki root'):
        calls[operation]()
    # ... and the outside file is untouched
    assert secret.read_text(encoding='utf-8') == 'Secret content.\n'


@pytest.mark.parametrize(
    ('frontmatter', 'expected'),
    [
        ('category: store', 'store'),
        ('category:  node ', 'node'),
        ('category: null', ''),
        ('tags: []\ncategory: input', 'input'),
        ('tags: [foo]', ''),
        ('name: test', ''),
    ],
    ids=['simple', 'whitespace', 'null', 'after-tags', 'no-category', 'absent'],
)
def test_read_frontmatter_category(
    tmp_path: pathlib.Path,
    frontmatter: str,
    expected: str,
) -> None:
    """``_read_frontmatter_category`` extracts the category field."""
    wiki = Wiki(tmp_path)
    assert wiki._read_frontmatter_category(frontmatter) == expected


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


def test_quoted_placeholder_desc_is_soft(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A quoted placeholder desc behaves exactly like the bare placeholder.

    ``desc: '...'`` resolves to the bare placeholder once the quotes are
    stripped, so it draws the soft note, not a missing-period issue.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    page = tmp_path / 'core' / 'design.md'
    page.write_text(
        page.read_text(encoding='utf-8').replace(
            'desc: The design page.',
            "desc: '...'",
        ),
        encoding='utf-8',
    )
    wiki.update()

    # a soft note, no missing-period issue, and no quoted dots in the parent
    issues = wiki.lint()
    err = capsys.readouterr().err
    assert 'Needs desc' in err
    assert not any('Missing period' in issue for issue in issues)
    core_index = (tmp_path / 'core' / '_index.md').read_text(encoding='utf-8')
    assert "'...'" not in core_index


def test_quoted_category_labels_and_filters(tmp_path: pathlib.Path) -> None:
    """A quoted category still labels, filters, and resolves its folder.

    The category reader must strip YAML quotes: a quoted value would
    otherwise leak into the label, the filter, and the folder path.
    """
    wiki = Wiki(tmp_path)
    wiki.init(name='root')
    _make_category_folder(tmp_path, 'store', '"backend"', 'The store layer.')
    wiki.update()

    # the parent label carries the unquoted category prefix
    root_index = (tmp_path / '_index.md').read_text(encoding='utf-8')
    assert '[[store/_index|[backend] store/]]' in root_index

    # the category filter matches, and the folder resolves (never unindexed)
    filtered = wiki.map(category=['backend'])
    assert 'store/' in filtered
    assert '(unindexed)' not in wiki.map()


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
    """A category set after a link exists propagates on update; clearing it reverts."""
    # init a category-ordered wiki
    wiki = CategorizedWiki(tmp_path)
    wiki.init()
    # create a folder with no category; first update -> plain parent label
    (tmp_path / 'cache').mkdir()
    wiki.update()
    root_index = tmp_path / '_index.md'
    index_path = tmp_path / 'cache' / '_index.md'
    assert '[[cache/_index|cache/]]' in root_index.read_text(encoding='utf-8')
    # set the child's category, then a single update propagates the prefix
    text = index_path.read_text(encoding='utf-8')
    index_path.write_text(
        text.replace('category: null', 'category: node'),
        encoding='utf-8',
    )
    wiki.update()
    assert '[[cache/_index|[node] cache/]]' in root_index.read_text(encoding='utf-8')
    # clearing the category reverts the parent label
    text = index_path.read_text(encoding='utf-8')
    index_path.write_text(
        text.replace('category: node', 'category: null'),
        encoding='utf-8',
    )
    wiki.update()
    assert '[[cache/_index|cache/]]' in root_index.read_text(encoding='utf-8')


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


def test_map_output(tmp_path: pathlib.Path) -> None:
    """Map renders an indented tree with category prefixes, words, and filters."""
    # build a category-ordered wiki with a categorized folder, an
    # uncategorized folder, a page, and a non-markdown file
    wiki = CategorizedWiki(tmp_path)
    wiki.init(name='root')
    _make_category_folder(tmp_path, 'cache', 'node', 'The cache layer.')
    _make_category_folder(tmp_path, 'notes', '', 'Free-form notes.')
    (tmp_path / 'cache' / 'design.md').write_text(
        '# design\n\nseveral words of body content here now.\n',
        encoding='utf-8',
    )
    (tmp_path / 'cache' / 'data.csv').write_text('a,b,c\n', encoding='utf-8')
    wiki.update()

    # category prefix and indented children
    output = wiki.map()
    assert '[node] cache/' in output
    assert 'notes/' in output
    assert '  design' in output
    assert '  data.csv' in output

    # depth=0 limits to top-level entries
    assert 'design' not in wiki.map(depth=0)

    # category filter applies at all depths: -c=node surfaces node
    # entries; -c='' surfaces uncategorized entries even nested under a
    # categorized folder (which appears as the path to them)
    node_only = wiki.map(category=['node'])
    assert '[node] cache/' in node_only
    assert 'notes/' not in node_only
    uncategorized_only = wiki.map(category=[])
    assert 'notes/' in uncategorized_only
    assert '  design' in uncategorized_only

    # markdown filter: True keeps .md pages, False keeps other files
    assert 'design' in wiki.map(markdown=True)
    assert 'data.csv' not in wiki.map(markdown=True)
    assert 'data.csv' in wiki.map(markdown=False)
    assert 'design' not in wiki.map(markdown=False)

    # words=False drops the (count) annotations
    assert '(' not in wiki.map(words=False)

    # desc_limit truncates long descriptions
    assert 'The cache layer.' not in wiki.map(desc_limit=4)


def test_map_folds_multiline_desc(tmp_path: pathlib.Path) -> None:
    """Map shows the full desc with newlines folded to spaces."""
    # author a block-scalar desc whose breaks land in the index row
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    (tmp_path / 'core' / 'layers.md').write_text(
        '---\nname: layers\ndesc: |\n  Layered architecture with strict\n'
        '  dependency direction.\n---\n\n# layers\n\nText.\n',
        encoding='utf-8',
    )
    wiki.update()

    # the whole desc renders on the map line, folded to one line
    output = wiki.map()
    assert 'Layered architecture with strict dependency direction.' in output


def test_map_unindexed(tmp_path: pathlib.Path) -> None:
    """Map marks un-indexed folders instead of crashing."""
    # build a populated wiki with one folder and page
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    # a folder created after update has no _index.md
    (tmp_path / 'bare').mkdir()
    # top-level target with no index -> single (unindexed) line, no crash
    assert wiki.map(name='bare') == 'bare/ (unindexed)'
    # a linked child whose index is deleted is marked in-tree; the rest still renders
    (tmp_path / 'core' / '_index.md').unlink()
    output = wiki.map()
    assert 'core/ (unindexed)' in output


def test_map_word_counts(tmp_path: pathlib.Path) -> None:
    """Folders render ``(page/tree)``; pages render a single ``(page)``.

    The tree total includes the folder's own index prose, so a leaf folder
    shows equal halves -- ``(N/N)`` -- rather than ``(N/0)``, and a
    parent's tree total exceeds its own page count once a child is added.
    """
    wiki = Wiki(tmp_path)
    wiki.init(name='root')
    # leaf folder: five words of index prose, no pages or subfolders
    leaf = tmp_path / 'leaf'
    leaf.mkdir()
    (leaf / '_index.md').write_text(
        '---\nname: leaf\ndesc: A leaf.\n---\n\n# leaf\n\n***\n\n'
        'one two three four five\n',
        encoding='utf-8',
    )
    # parent folder with a child page so its subtree exceeds its own prose
    parent = tmp_path / 'parent'
    parent.mkdir()
    (parent / '_index.md').write_text(
        '---\nname: parent\ndesc: A parent.\n---\n\n# parent\n\n***\n\nalpha beta\n',
        encoding='utf-8',
    )
    (parent / 'child.md').write_text(
        '---\nname: child\ndesc: A child.\n---\n\n# child\n\nsome words here\n',
        encoding='utf-8',
    )
    wiki.update()
    output = wiki.map()

    # leaf: tree includes its own index prose -- a childless folder shows equal
    # halves (page == tree), not (N/0)
    leaf_match = re.search(r'leaf/ \((\d+)/(\d+)\)', output)
    assert leaf_match is not None
    assert leaf_match.group(1) == leaf_match.group(2)
    assert int(leaf_match.group(1)) > 0
    # parent: a folder always shows the ratio, and the tree exceeds its own page
    match = re.search(r'parent/ \((\d+)/(\d+)\)', output)
    assert match is not None
    page_count, tree_count = int(match.group(1)), int(match.group(2))
    assert tree_count > page_count
    # page: a single count, no ratio slash
    assert re.search(r'child \(\d+\)', output)


def test_body_includes_h1_for_counts_and_search(
    tmp_path: pathlib.Path,
) -> None:
    """Only the frontmatter is special; the H1 is ordinary body content.

    Word count, search, and ``read`` slicing all cover everything below the
    frontmatter -- the H1 heading and an index's auto-generated link block
    alike -- so a query matches the H1 line and the count includes it.
    """
    wiki = Wiki(tmp_path)
    wiki.init(name='root')
    (tmp_path / 'topic.md').write_text(
        '---\nname: topic\ndesc: d\n---\n\n# topic\n\nbody prose words\n',
        encoding='utf-8',
    )
    wiki.update()
    # the count covers the H1 ("# topic" = 2) plus the prose (3)
    assert 'topic (5)' in wiki.map()
    # search matches the page's H1 line (frontmatter is skipped; prose lacks it)
    hits = wiki.search('topic')
    assert any(path == 'topic.md' and '# topic' in line for path, _, line in hits)
    # the index's auto-generated link block is body too, so it is matched as well
    assert any('_index.md' in path for path, _, _ in hits)


def test_search_field_matches_value_only(tmp_path: pathlib.Path) -> None:
    """``field`` patterns match the field's VALUE, never the ``key:`` prefix.

    Matching the raw line would mean a value anchor (``^...``) could
    never hit and a pattern naming the key (``desc``) would hit every
    line of that field; the match runs against the value alone --
    block-scalar continuation lines included, surrounding YAML quotes
    stripped -- while the reported line text stays raw.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    (tmp_path / 'core' / 'block.md').write_text(
        '---\nname: block\ndesc: |\n  Multi-line summary.\n---\n\n# block\n\nBody.\n',
        encoding='utf-8',
    )
    # a ': ' in the page name makes update write the name quoted
    (tmp_path / 'core' / 'note: draft.md').write_text(
        '---\nname: note: draft\ndesc: d\n---\n\n# note: draft\n\nBody.\n',
        encoding='utf-8',
    )
    wiki.update()

    # a value anchor matches from the value's first character
    hits = wiki.search('^The design', field='desc')
    assert [relpath for relpath, _, _ in hits] == ['core/design.md']
    # ... including on a block scalar's continuation lines
    hits = wiki.search('^Multi-line', field='desc')
    assert [relpath for relpath, _, _ in hits] == ['core/block.md']
    # the key name itself is never part of the searched text
    assert wiki.search('desc', field='desc') == []
    # anchors see the unquoted value even when the wiki quotes it (_quote)
    for anchored in ('^core/note', 'draft$', '^core/note: draft$'):
        hits = wiki.search(anchored, field='name')
        assert [relpath for relpath, _, _ in hits] == ['core/note: draft.md']


def test_map_handles_dotted_markdown_stem(tmp_path: pathlib.Path) -> None:
    """A dotted markdown stem (``my.notes.md``) counts words and filters as md.

    Resolving such a page by a name test (``'.' in name``) would read its
    word count from a missing file (0) and invert the ``--markdown`` filter;
    the map must probe the actual ``<name>.md`` file.
    """
    wiki = Wiki(tmp_path)
    wiki.init(name='root')
    (tmp_path / 'my.notes.md').write_text(
        '---\nname: notes\ndesc: d\n---\n\n# Notes\n\nalpha beta gamma\n',
        encoding='utf-8',
    )
    wiki.update()
    # word count covers the body incl. the H1 (5), not 0 from a mis-resolved path
    assert re.search(r'my\.notes \(5\)', wiki.map())
    # classified as markdown: shown with --markdown, hidden without
    assert 'my.notes' in wiki.map(markdown=True)
    assert 'my.notes' not in wiki.map(markdown=False)


@pytest.mark.parametrize(
    ('count', 'expected'),
    [
        (0, '0'),
        (149, '149'),
        (999, '999'),
        (1000, '1.0k'),
        (1001, '1.0k'),
        (1234, '1.2k'),
        (15200, '15.2k'),
        (999_499, '999.5k'),
        (999_950, '1.0m'),
        (1_000_000, '1.0m'),
        (1_500_000, '1.5m'),
        (2_300_000_000, '2.3b'),
        (1_000_000_000_000, '1.0t'),
        (4_700_000_000_000, '4.7t'),
        (999_999_999_999_999, '999.9t'),
    ],
    ids=[
        'zero',
        'small-exact',
        'sub-thousand',
        'thousand-exact',
        'just-over-thousand',
        'k-one-decimal',
        'k-two-digit',
        'k-near-million',
        'k-rounds-up-to-m',
        'million-exact',
        'million',
        'billion',
        'trillion-exact',
        'trillion',
        'top-tier-clamps-below-rollover',
    ],
)
def test_format_words(count: int, expected: str) -> None:
    """``_format_words`` abbreviates with k/m/b/t, promoting on round-up.

    Exact tier thresholds take the suffix (``1000`` -> ``1.0k``) and the
    top tier, which has nowhere to promote, clamps below the roll-over
    instead of rendering a four-digit ``1000.0t``.
    """
    assert _format_words(count) == expected


@pytest.fixture
def stub_download(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the plugin download with a marker write (no real network)."""
    # ensure an ambient _OFFLINE_MODE does not skip the stubbed download
    monkeypatch.delenv(_OFFLINE_MODE, raising=False)

    def download(self: Wiki, url: str, target: pathlib.Path) -> None:
        """Write marker bytes instead of fetching from the network."""
        target.write_bytes(b'CODE')

    monkeypatch.setattr(Wiki, '_download', download)


def test_update_config_installs_plugin(
    tmp_path: pathlib.Path,
    stub_download: None,
) -> None:
    """``update_config`` installs the bundled plugin into ``.obsidian/``."""
    # init seeds the front matter title plugin into .wiki/obsidian
    wiki = Wiki(tmp_path)
    wiki.init()

    # update_config copies settings, downloads code, and enables the plugin
    warnings = wiki.update_config()
    assert warnings == []
    plugin_id = 'obsidian-front-matter-title-plugin'
    plugin = tmp_path / '.obsidian' / 'plugins' / plugin_id
    assert (plugin / 'main.js').read_bytes() == b'CODE'
    assert (plugin / 'manifest.json').read_bytes() == b'CODE'
    assert (plugin / 'data.json').is_file()
    cp_file = tmp_path / '.obsidian' / 'community-plugins.json'
    assert plugin_id in json.loads(cp_file.read_text(encoding='utf-8'))


def test_update_config_offline_warns(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed plugin download warns but still applies local config."""
    # init seeds the front matter title plugin into .wiki/obsidian
    wiki = Wiki(tmp_path)
    wiki.init()
    monkeypatch.delenv(_OFFLINE_MODE, raising=False)

    # stub the download boundary to simulate a failure
    def offline(self: Wiki, url: str, target: pathlib.Path) -> None:
        """Raise as if the download failed."""
        raise OSError('no network')

    monkeypatch.setattr(Wiki, '_download', offline)

    # the failed download is a soft failure (warning, not error)
    warnings = wiki.update_config()
    assert any('could not download' in warning.lower() for warning in warnings)

    # settings and the enabled-plugins list are still written
    plugin_id = 'obsidian-front-matter-title-plugin'
    plugin = tmp_path / '.obsidian' / 'plugins' / plugin_id
    assert (plugin / 'data.json').is_file()
    cp_file = tmp_path / '.obsidian' / 'community-plugins.json'
    assert plugin_id in json.loads(cp_file.read_text(encoding='utf-8'))


def test_update_config_keeps_notices_off_warnings(
    tmp_path: pathlib.Path,
    stub_download: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Restoring the root marker is a notice, never a returned warning.

    The returned warnings mean setup needs another run and gate the
    CLI's success output, so an informational restoration must ride the
    notice channel (stderr) instead of masquerading as a failed plugin
    download.
    """
    wiki = Wiki(tmp_path)
    wiki.init()
    (tmp_path / '.wiki' / 'settings.json').unlink()
    capsys.readouterr()

    # the marker is restored and announced, with no warnings returned
    assert wiki.update_config() == []
    assert 'Restored missing' in capsys.readouterr().err
    assert (tmp_path / '.wiki' / 'settings.json').is_file()


def test_update_config_preserves_existing(
    tmp_path: pathlib.Path,
    stub_download: None,
) -> None:
    """``update_config`` merges into existing config without clobbering it."""
    # init seeds the front matter title plugin into .wiki/obsidian
    wiki = Wiki(tmp_path)
    wiki.init()

    # seed an unrelated enabled plugin and a top-level app.json
    obsidian_dir = tmp_path / '.obsidian'
    (obsidian_dir / 'plugins' / 'other-plugin').mkdir(parents=True)
    (obsidian_dir / 'plugins' / 'other-plugin' / 'main.js').write_bytes(b'OTHER')
    (obsidian_dir / 'community-plugins.json').write_text(
        json.dumps(['other-plugin']),
        encoding='utf-8',
    )
    (obsidian_dir / 'app.json').write_text(
        json.dumps({'existing': 1}),
        encoding='utf-8',
    )
    config_dir = tmp_path / '.wiki' / 'obsidian'
    (config_dir / 'app.json').write_text(
        json.dumps({'setting': True}),
        encoding='utf-8',
    )

    wiki.update_config()

    # the unrelated plugin and its enabled entry survive
    other_main = obsidian_dir / 'plugins' / 'other-plugin' / 'main.js'
    assert other_main.read_bytes() == b'OTHER'
    enabled = json.loads(
        (obsidian_dir / 'community-plugins.json').read_text(encoding='utf-8')
    )
    assert 'other-plugin' in enabled
    assert 'obsidian-front-matter-title-plugin' in enabled

    # the top-level app.json is deep-merged, not replaced
    app = json.loads((obsidian_dir / 'app.json').read_text(encoding='utf-8'))
    assert app == {'existing': 1, 'setting': True}


def test_update_config_is_idempotent(
    tmp_path: pathlib.Path,
    stub_download: None,
) -> None:
    """Re-running ``update_config`` leaves the merged config unchanged."""
    wiki = Wiki(tmp_path)
    wiki.init()

    # first run materializes the config
    wiki.update_config()
    cp_file = tmp_path / '.obsidian' / 'community-plugins.json'
    first = cp_file.read_text(encoding='utf-8')

    # a second run must not duplicate the enabled-plugin entry
    wiki.update_config()
    assert cp_file.read_text(encoding='utf-8') == first
    enabled = json.loads(first)
    assert enabled.count('obsidian-front-matter-title-plugin') == 1


def test_update_config_seeds_missing_config_dir(
    tmp_path: pathlib.Path,
    stub_download: None,
) -> None:
    """A missing ``.wiki/obsidian`` is seeded from the stock template.

    An adopted index tree (or a wiki whose ``.wiki/`` was lost) has no
    staged Obsidian config, and ``init`` refuses to re-run on it, so
    ``update_config`` must seed the staging directory itself and complete
    the full setup rather than aborting on an internal path.
    """
    # a wiki that was never initialized has no .wiki/obsidian
    wiki = Wiki(tmp_path)
    assert wiki.update_config() == []

    # the staging directory is seeded and the plugin fully installed
    plugin_id = 'obsidian-front-matter-title-plugin'
    config_dir = tmp_path / '.wiki' / 'obsidian'
    assert (config_dir / 'community-plugins.json').is_file()
    assert (config_dir / 'plugins' / plugin_id / 'data.json').is_file()
    cp_file = tmp_path / '.obsidian' / 'community-plugins.json'
    assert plugin_id in json.loads(cp_file.read_text(encoding='utf-8'))


def test_update_config_rejects_type_mismatch(
    tmp_path: pathlib.Path,
    stub_download: None,
) -> None:
    """``update_config`` raises on a top-level JSON type mismatch."""
    wiki = Wiki(tmp_path)
    wiki.init()

    # a list source against a dict target cannot be merged
    config_dir = tmp_path / '.wiki' / 'obsidian'
    (config_dir / 'app.json').write_text(json.dumps([1, 2]), encoding='utf-8')
    obsidian_dir = tmp_path / '.obsidian'
    obsidian_dir.mkdir(exist_ok=True)
    (obsidian_dir / 'app.json').write_text(json.dumps({'a': 1}), encoding='utf-8')

    with pytest.raises(TypeError, match=r'\.obsidian/app\.json'):
        wiki.update_config()


def test_update_config_reports_malformed_target_json(
    tmp_path: pathlib.Path,
    stub_download: None,
) -> None:
    """A malformed existing ``.obsidian`` JSON names the file instead of a bare error.

    The target is user-editable, so a hand-edit or truncated write must
    surface a diagnosable message rather than an undiagnosable JSON error.
    """
    wiki = Wiki(tmp_path)
    wiki.init()

    # an already-installed top-level config is then corrupted by hand
    config_dir = tmp_path / '.wiki' / 'obsidian'
    (config_dir / 'app.json').write_text(json.dumps({'a': 1}), encoding='utf-8')
    obsidian_dir = tmp_path / '.obsidian'
    obsidian_dir.mkdir(exist_ok=True)
    (obsidian_dir / 'app.json').write_text('{ "a": 1, }', encoding='utf-8')

    with pytest.raises(ValueError, match=r'\.obsidian/app\.json'):
        wiki.update_config()


@pytest.mark.parametrize(
    ('value', 'offline'),
    [
        ('true', True),
        ('TRUE', True),
        (' true ', True),
        ('false', False),
        ('', False),
    ],
)
def test_update_config_offline_mode(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    value: str,
    offline: bool,
) -> None:
    """``OFFLINE_MODE`` skips the download only for truthy values."""
    wiki = Wiki(tmp_path)
    wiki.init()

    # record whether the download boundary is reached
    reached = []

    def download(self: Wiki, url: str, target: pathlib.Path) -> None:
        """Record the call and write marker bytes."""
        reached.append(url)
        target.write_bytes(b'CODE')

    monkeypatch.setattr(Wiki, '_download', download)
    monkeypatch.setenv(_OFFLINE_MODE, value)

    wiki.update_config()
    assert (not reached) == offline


@pytest.mark.parametrize('value', ['1', '0', 'maybe'])
def test_update_config_rejects_bad_offline_mode(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    """An unrecognized ``OFFLINE_MODE`` value is rejected."""
    wiki = Wiki(tmp_path)
    wiki.init()
    monkeypatch.setenv(_OFFLINE_MODE, value)
    with pytest.raises(ValueError, match='OFFLINE_MODE'):
        wiki.update_config()


def test_init_rejects_bad_offline_mode_before_scaffolding(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bad ``OFFLINE_MODE`` aborts ``init`` before writing anything.

    ``init`` scaffolds the wiki that ``update_config`` configures next, so a
    value ``update_config`` would reject must fail up front rather than strand
    a half-built wiki (root index written, ``.obsidian`` missing) that the
    re-init guard then refuses to finish.
    """
    root = tmp_path / 'wiki'
    monkeypatch.setenv(_OFFLINE_MODE, 'maybe')

    # init raises and leaves no scaffolding behind
    wiki = Wiki(root)
    with pytest.raises(ValueError, match='OFFLINE_MODE'):
        wiki.init()
    assert not root.exists()


@pytest.mark.parametrize(
    ('name', 'valid'),
    [
        ('core', True),
        ('my_page', True),
        ('bad-name', True),
        ('Machine Learning', True),
        ('café', True),
        ('release.notes', True),
        ('123start', True),
        ('a/b', False),
        ('a#b', False),
        ('a|b', False),
        ('.hidden', False),
        ('_index', False),
        ('_notes', True),
        ('', False),
    ],
    ids=[
        'simple',
        'underscore',
        'hyphen',
        'spaces',
        'unicode',
        'interior-dot',
        'leading-digit',
        'path-separator',
        'hash',
        'pipe',
        'leading-dot',
        'reserved',
        'leading-underscore',
        'empty',
    ],
)
def test_validate_name(tmp_path: pathlib.Path, name: str, valid: bool) -> None:
    """The default policy is lenient: any name except the structural characters."""
    wiki = Wiki(tmp_path)
    assert wiki.validate_name(name) == valid


def test_validate_name_strict_via_settings(tmp_path: pathlib.Path) -> None:
    """A ``settings.json`` naming block can restore the strict identifier rule."""
    config = tmp_path / '.wiki'
    config.mkdir()
    policy = {'naming': {'validate': ['ascii', 'identifier'], 'leading_digits': True}}
    (config / 'settings.json').write_text(json.dumps(policy), encoding='utf-8')
    wiki = Wiki(tmp_path)
    # strict accepts ASCII identifiers, including a leading digit ...
    assert wiki.validate_name('MyPage')
    assert wiki.validate_name('123start')
    # ... and rejects what the lenient default would allow
    assert not wiki.validate_name('bad-name')
    assert not wiki.validate_name('café')
    assert not wiki.validate_name('Machine Learning')


@pytest.mark.parametrize(
    ('naming', 'name', 'valid'),
    [
        ({'pattern': '[a-z]+(_[a-z]+)*'}, 'good_name', True),
        ({'pattern': '[a-z]+(_[a-z]+)*'}, 'BadName', False),
        ({'min_length': 3}, 'ab', False),
        ({'min_length': 3}, 'abc', True),
        ({'max_length': 5}, 'abcde', True),
        ({'max_length': 5}, 'abcdef', False),
        ({'deny': '$'}, 'pri$e', False),
        ({'validate': ['identifier']}, 'spin-lock', False),
        ({'validate': ['identifier'], 'allow': '-'}, 'spin-lock', True),
        ({'reserved': ['drafts']}, 'drafts', False),
        ({'validate': ['identifier'], 'leading_digits': False}, '123start', False),
    ],
    ids=[
        'pattern-match',
        'pattern-miss',
        'too-short',
        'min-length-ok',
        'max-length-ok',
        'too-long',
        'denied-char',
        'identifier-rejects-dash',
        'allow-exempts-dash',
        'reserved-name',
        'leading-digit-rejected',
    ],
)
def test_naming_policy_knobs(
    tmp_path: pathlib.Path,
    naming: dict,
    name: str,
    valid: bool,
) -> None:
    """Each ``settings.json`` naming knob shapes ``validate_name``.

    ``pattern`` requires a full match, ``min_length``/``max_length`` bound
    the name, ``deny`` adds rejected characters, ``allow`` exempts characters
    from the predicates, ``reserved`` blocks exact names, and
    ``leading_digits: false`` drops the identifier rule's leading-digit
    exemption.
    """
    config = tmp_path / '.wiki'
    config.mkdir()
    (config / 'settings.json').write_text(
        json.dumps({'naming': naming}),
        encoding='utf-8',
    )
    wiki = Wiki(tmp_path)
    assert wiki.validate_name(name) == valid


def test_init_scaffolds_settings(tmp_path: pathlib.Path) -> None:
    """``init`` writes a discoverable ``.wiki/settings.json`` with naming defaults."""
    wiki = Wiki(tmp_path)
    wiki.init(name='Root')
    settings = tmp_path / '.wiki' / 'settings.json'
    assert settings.is_file()
    data = json.loads(settings.read_text(encoding='utf-8'))
    assert data['naming']['validate'] == []  # the lenient default, spelled out


def test_init_seeds_custom_settings(tmp_path: pathlib.Path) -> None:
    """``init(settings=...)`` seeds the caller's ``settings.json`` and applies it."""
    policy = {'naming': {'validate': ['ascii', 'identifier']}}
    Wiki(tmp_path).init(name='Root', settings=policy)
    # the seeded settings.json is exactly the caller's policy ...
    settings = tmp_path / '.wiki' / 'settings.json'
    data = json.loads(settings.read_text(encoding='utf-8'))
    assert data == policy
    # ... and a fresh instance reads it: the strict rule rejects a dashed name
    wiki = Wiki(tmp_path)
    assert wiki.validate_name('my_page')
    assert not wiki.validate_name('bad-name')


@pytest.mark.parametrize(
    'settings',
    [
        {'naming': {'validate': ['bogus']}},
        {'timestamp': {'timezone': 'Mars/Olympus'}},
    ],
    ids=[
        'bad-naming',
        'bad-timestamp',
    ],
)
def test_init_rejects_bad_settings_before_writing(
    tmp_path: pathlib.Path,
    settings: dict,
) -> None:
    """A rejected ``settings`` seed aborts ``init`` before writing anything.

    ``init`` seeds ``.wiki/settings.json`` and never overwrites it, so a
    policy the resolvers reject must fail up front -- naming the file -- rather
    than strand a wiki whose written seed every later command (and re-init,
    even with corrected settings) keeps failing on.
    """
    root = tmp_path / 'wiki'

    # init raises an error naming the settings file and writes nothing
    with pytest.raises(ValueError, match=r'settings\.json'):
        Wiki(root).init(settings=settings)
    assert not root.exists()
    # so a corrected re-init succeeds where the bad seed would have stuck
    Wiki(root).init(settings={'naming': {'validate': ['ascii']}})
    assert (root / '_index.md').is_file()


def test_init_rejects_invalid_wiki_name(tmp_path: pathlib.Path) -> None:
    """``init`` refuses a wiki name the naming policy rejects, writing nothing."""
    root = tmp_path / 'wiki'
    with pytest.raises(ValueError, match='Invalid wiki name'):
        Wiki(root).init(name='bad|name')
    assert not root.exists()


@pytest.mark.parametrize(
    ('content', 'match'),
    [
        ('{bad json', r'Malformed JSON'),
        ('[]', r'must be a JSON object'),
        ('{"naming": "strict"}', r'naming must be a JSON object'),
        ('{"naming": {"validate": "identifier"}}', r'validate must be a list'),
        ('{"naming": {"validate": ["bogus"]}}', r'Unknown naming predicate'),
        ('{"naming": {"min_length": 0}}', r'min_length must be an int'),
        ('{"naming": {"max_length": 0}}', r'max_length must be an int'),
        ('{"naming": {"deny": ["|"]}}', r'deny must be a string'),
        ('{"naming": {"reserved": "drafts"}}', r'reserved must be a list'),
        ('{"naming": {"leading_digits": "yes"}}', r'leading_digits must be a boolean'),
        ('{"naming": {"pattern": 5}}', r'pattern must be a string'),
        ('{"naming": {"pattern": "["}}', r'not a valid regex'),
        ('{"timestamp": "now"}', r'timestamp must be a JSON object'),
        ('{"timestamp": {"format": 5}}', r'format must be a string'),
        ('{"timestamp": {"timezone": 5}}', r'timezone must be a string'),
        ('{"timestamp": {"timezone": "Mars/Olympus"}}', r'Unknown timestamp.timezone'),
    ],
    ids=[
        'malformed-json',
        'non-object-top-level',
        'non-object-naming',
        'non-list-validate',
        'bad-predicate',
        'bad-min-length',
        'bad-max-length',
        'bad-deny',
        'bad-reserved',
        'bad-leading-digits',
        'non-string-pattern',
        'bad-pattern',
        'non-object-timestamp',
        'non-string-format',
        'non-string-timezone',
        'bad-timezone',
    ],
)
def test_settings_reject_malformed_values(
    tmp_path: pathlib.Path,
    content: str,
    match: str,
) -> None:
    """Malformed ``settings.json`` values fail loudly through a public command.

    ``settings.json`` is user-editable input: an unparseable file or an
    out-of-range/wrong-typed knob raises ``ValueError`` naming the file
    rather than silently falling back to a default, and the error surfaces
    through any command that reads the policy (here ``lint``).
    """
    # build a valid wiki, then corrupt its settings.json
    _make_wiki(tmp_path, folders={'core': ['design']})
    settings = tmp_path / '.wiki' / 'settings.json'
    settings.write_text(content, encoding='utf-8')

    # a fresh instance fails loudly, naming the settings file
    with pytest.raises(ValueError, match=match) as excinfo:
        Wiki(tmp_path).lint()
    assert 'settings.json' in str(excinfo.value)


def test_timestamp_format_configurable(tmp_path: pathlib.Path) -> None:
    """``timestamp.format`` controls the timestamp string format."""
    config = tmp_path / '.wiki'
    config.mkdir()
    (config / 'settings.json').write_text(
        json.dumps({'timestamp': {'format': '%Y'}}), encoding='utf-8'
    )
    stamp = Wiki(tmp_path)._utc_now()
    assert stamp.isdigit()
    assert len(stamp) == 4  # just the year


def test_timestamp_timezone_configurable(tmp_path: pathlib.Path) -> None:
    """``timestamp.timezone`` renders timestamps in the configured zone."""
    config = tmp_path / '.wiki'
    config.mkdir()
    (config / 'settings.json').write_text(
        json.dumps({'timestamp': {'timezone': 'America/New_York', 'format': '%z'}}),
        encoding='utf-8',
    )
    stamp = Wiki(tmp_path)._utc_now()
    assert stamp in ('-0400', '-0500')  # EDT/EST offset, never UTC's +0000


def test_map_presentation_configurable(tmp_path: pathlib.Path) -> None:
    """settings.json ``map.*`` knobs customize the indent unit and ellipsis."""
    config = tmp_path / '.wiki'
    config.mkdir()
    (config / 'settings.json').write_text(
        json.dumps({'map': {'indent': '. ', 'ellipsis': '###'}}),
        encoding='utf-8',
    )
    # a page with a long desc so --desc-limit truncates it
    _make_wiki(tmp_path, folders={'core': ['design']})
    page = tmp_path / 'core' / 'design.md'
    page.write_text(
        '---\nname: core/design\ndesc: A long design note about the subsystem.\n---'
        '\n\n# core/design\n\nBody.\n',
        encoding='utf-8',
    )
    Wiki(tmp_path).update()
    out = Wiki(tmp_path).map(desc_limit=15)
    # map.indent: the nested page entry uses the custom indent unit
    assert any(line.startswith('. ') for line in out.splitlines())
    # map.ellipsis: a desc past --desc-limit is truncated with the custom marker
    assert '###' in out


def test_read_slice_units(tmp_path: pathlib.Path) -> None:
    """``read`` slices by words/lines/chars; words keep original spacing.

    Only the frontmatter is special: the H1 leads the body, so it occupies the
    first word/line/char positions and is sliced alongside the prose. An
    unknown unit is rejected loudly rather than returning unsliced content.
    """
    wiki = Wiki(tmp_path)
    body = 'Alpha   beta gamma\ndelta epsilon.'
    (tmp_path / 'p.md').write_text(
        f'---\nname: P\ndesc: A page.\n---\n\n# P\n\n{body}\n',
        encoding='utf-8',
    )
    # the H1 leads the body: words 2:4 reach the prose, keeping original spacing
    assert 'Alpha   beta' in wiki.read('p', start=2, stop=4, on='words')
    # the first body line is the H1 heading, not the prose
    out = wiki.read('p', start=0, stop=1, on='lines')
    assert '# P' in out
    assert 'Alpha' not in out
    # chars slice by character, reaching the prose past the leading H1
    sliced = wiki.read('p', start=5, stop=10, on='chars')
    assert sliced.strip().endswith('Alpha')
    # an unknown unit is rejected loudly
    with pytest.raises(ValueError, match="must be 'lines', 'words', or 'chars'"):
        wiki.read('p', start=0, stop=1, on='paragraphs')


def test_update_skips_invalid_name(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A page whose name breaks the policy is skipped, warned, and never indexed.

    A denied char like ``|`` would otherwise emit a malformed ``[[a|b|a|b]]``
    link that grows the index every run; update skips it (non-fatal) so the rest
    of the tree still updates and a second run is a no-op.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    # author a page whose stem holds a denied character
    (tmp_path / 'a|b.md').write_text(
        '---\nname: x\ndesc: A page.\n---\n\n# x\n\nPipe page.\n',
        encoding='utf-8',
    )

    # update warns about the bad name but still processes the valid tree
    wiki.update()
    err = capsys.readouterr().err
    assert 'a|b.md' in err
    assert 'invalid name' in err
    root_index = (tmp_path / '_index.md').read_text(encoding='utf-8')
    assert 'a|b' not in root_index
    assert '[[core/_index|core/]]' in root_index

    # a second update changes nothing: the skip never grows the index
    assert wiki.update() == []


def test_read_suggests_unique_leaf_match(tmp_path: pathlib.Path) -> None:
    """A failed read of a bare leaf suggests the unique nested page's read key."""
    _make_wiki(tmp_path, folders={'team/eng': ['oncall']})
    wiki = Wiki(tmp_path)
    # the bare leaf misses, but the error names the path-joined key that resolves
    with pytest.raises(FileNotFoundError, match=r'did you mean team/eng/oncall'):
        wiki.read('oncall')


@pytest.mark.parametrize('anchor', ['', '#context'], ids=['bare', 'anchored'])
def test_lint_stale_body_link_names_canonical(
    tmp_path: pathlib.Path,
    anchor: str,
) -> None:
    """A folder-relative body link is flagged with its root-relative fix.

    An anchor suffix rides along on the suggestion: dropping it would make
    a user applying the fix verbatim silently lose the anchor.
    """
    _make_wiki(tmp_path, folders={'notes': ['meeting']})
    wiki = Wiki(tmp_path)
    # a page that exists at root, and a folder-relative link to it from a subpage
    (tmp_path / 'overview.md').write_text(
        '---\nname: overview\ndesc: An overview.\n---\n\n# overview\n\nText.\n',
        encoding='utf-8',
    )
    meeting = tmp_path / 'notes' / 'meeting.md'
    meeting.write_text(
        meeting.read_text(encoding='utf-8').replace(
            'Content for meeting.',
            f'See [[../overview{anchor}]] for context.',
        ),
        encoding='utf-8',
    )
    wiki.update()
    # the stale link is flagged with the canonical [[overview]] as the fix
    stale = [
        issue for issue in wiki.lint() if f'Stale link [[../overview{anchor}]]' in issue
    ]
    assert stale
    assert all(f'(use [[overview{anchor}]])' in issue for issue in stale)


def test_markerless_index_warns_in_map_and_flags_in_lint(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A root index that lost its ``***`` is named by map (warn) and lint.

    Without the delimiter the demoted link rows await ``wiki update``'s
    reclaim; until it runs, map must warn rather than read the populated wiki
    as empty, and lint must name the missing marker so the cause is obvious.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    # drop the root *** delimiter while pages remain on disk
    root_index = tmp_path / '_index.md'
    text = root_index.read_text(encoding='utf-8')
    text = '\n'.join(line for line in text.split('\n') if line.rstrip() != '***')
    root_index.write_text(text, encoding='utf-8')

    # map warns about the missing delimiter instead of returning an empty tree
    wiki.map()
    err = capsys.readouterr().err
    assert 'missing its *** delimiter' in err

    # lint names the missing marker specifically
    assert any('Index missing *** delimiter' in issue for issue in wiki.lint())


def test_lint_flags_folder_shadowing_page(tmp_path: pathlib.Path) -> None:
    """A ``<name>/`` folder coexisting with ``<name>.md`` is flagged by lint.

    The folder shadows the page in ``read`` (resolution is directory-first), so
    lint surfaces the collision even though update leaves both in place.
    """
    wiki = _make_wiki(tmp_path, folders={'topic': ['sub']})
    # a page colliding with the existing folder name
    (tmp_path / 'topic.md').write_text(
        '---\nname: topic\ndesc: A page.\n---\n\n# topic\n\nHidden body.\n',
        encoding='utf-8',
    )
    wiki.update()
    # the shadowed page is flagged, naming the folder that hides it
    shadowed = [issue for issue in wiki.lint() if 'Shadowed by folder' in issue]
    assert shadowed
    assert all('topic.md' in issue for issue in shadowed)


@pytest.mark.parametrize(
    'format',
    ['', '%Y%n%H'],
    ids=['empty', 'embedded-newline'],
)
def test_timestamp_format_rejects_blank_or_multiline(
    tmp_path: pathlib.Path,
    format: str,
) -> None:
    """A ``timestamp.format`` that yields a blank or multi-line value is rejected.

    Such a value would write a blank or newline-bearing ``created:`` into the
    YAML frontmatter; the resolver rejects it loudly at config load, the way the
    naming policy rejects a bad name.
    """
    config = tmp_path / '.wiki'
    config.mkdir()
    (config / 'settings.json').write_text(
        json.dumps({'timestamp': {'format': format}}),
        encoding='utf-8',
    )
    # the bad format fails loudly when update resolves the timestamp policy
    with pytest.raises(ValueError, match='single non-empty line'):
        Wiki(tmp_path).update()


def test_update_preserves_concurrent_edit(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An edit landing between plan and apply survives the update.

    ``update`` snapshots every file while planning and writes corrected
    content afterwards; an edit another writer (e.g. a sibling fractal
    node) lands inside that window must not be silently reverted to the
    plan-time snapshot.
    """
    _make_wiki(tmp_path, folders={'notes': ['readme']})
    page = tmp_path / 'notes' / 'readme.md'

    class RacingWiki(Wiki):
        """Wiki whose plan is immediately followed by a concurrent edit."""

        def _plan(
            self: RacingWiki,
            folder: pathlib.Path,
            **kwargs: Any,
        ) -> tuple:
            """Plan, then land another writer's edit inside the window."""
            result = super()._plan(folder, **kwargs)
            text = page.read_text(encoding='utf-8')
            page.write_text(text + '\nConcurrent paragraph.\n', encoding='utf-8')
            return result

    # the concurrent edit survives and the skipped file is named
    RacingWiki(tmp_path).update()
    err = capsys.readouterr().err
    assert 'Concurrent paragraph.' in page.read_text(encoding='utf-8')
    assert 'notes/readme.md' in err
    assert 'changed during update' in err

    # the next (unraced) run converges without losing the edit
    Wiki(tmp_path).update()
    assert 'Concurrent paragraph.' in page.read_text(encoding='utf-8')
    assert Wiki(tmp_path).update() == []


def test_update_writes_are_atomic_for_concurrent_readers(
    tmp_path: pathlib.Path,
) -> None:
    """A concurrent reader never observes an empty or truncated file.

    ``update`` rewrites files in place; a truncate-then-write briefly
    empties each file, so a concurrent reader (another node's plan) sees
    ``''`` or a prefix, and a crash mid-write leaves a torn file. Writes
    must stage to a temp file in the same directory and rename into
    place, keeping every read all-or-nothing.
    """
    wiki = _make_wiki(tmp_path)
    page = tmp_path / 'big.md'
    # two complete page variants, large enough that a truncate-then-write
    # window is observable, each ending in a sentinel a torn read loses
    variants = []
    for word in ('alpha', 'omega'):
        body = f'{word} ' * 300_000
        variants.append(
            f'---\nname: big\ndesc: A big page.\nupdated: x\n---\n\n'
            f'# big\n\n{body}END\n'
        )
    page.write_text(variants[0], encoding='utf-8')

    # a reader polling for torn reads while the writer rewrites the page
    torn = []
    stop = threading.Event()

    def read_loop() -> None:
        """Record any read that is not a complete page variant."""
        while not stop.is_set():
            # a missing file is as torn as a truncated one: record the
            # error instead of letting it kill the reader thread
            try:
                text = page.read_text(encoding='utf-8')
            except OSError as e:
                torn.append(repr(e))
                continue
            if not text.endswith('END\n'):
                torn.append(len(text))

    reader = threading.Thread(target=read_loop)
    reader.start()
    try:
        # alternate staged variants so every apply performs a real write
        now = wiki._utc_now()
        for i in range(1, 60):
            content = variants[i % 2]
            baseline = {page: page.read_text(encoding='utf-8')}
            wiki._apply_plan({page: content}, baseline, now)
    finally:
        stop.set()
        reader.join()
    assert torn == []


def test_map_survives_binary_attachment(tmp_path: pathlib.Path) -> None:
    """One binary attachment neither crashes map nor reads anonymously.

    Non-markdown files are first-class wiki entries; a word-count pass that
    read every entry as UTF-8 would let a single indexed image fail ``map``
    wiki-wide with an error naming no path.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    binary = tmp_path / 'core' / 'diagram.png'
    binary.write_bytes(b'\x89PNG\r\n\x1a\n\xff\xfe\x00\x01')
    wiki.update()

    # the whole tree still maps, with the attachment listed
    output = wiki.map()
    assert 'diagram.png' in output
    assert 'design' in output

    # reading the binary itself names the file, not a bare decode error
    with pytest.raises(UnicodeDecodeError, match=r'diagram\.png'):
        wiki.read('core/diagram.png')


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
    'hazard',
    ['***', '[[y|y]]: the other page.'],
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


@pytest.mark.parametrize(
    'category',
    ['to-do', 'v1.2', 'my cat'],
    ids=['dashed', 'dotted', 'spaced'],
)
def test_non_word_category_labels_filters_and_resolves(
    tmp_path: pathlib.Path,
    category: str,
) -> None:
    """A category with punctuation still labels, filters, and resolves.

    Were ``_parse_category`` to match word characters only, a dashed/dotted/
    spaced category would make the whole bracketed label read as the entry
    name, silently corrupting the map, its counts, and both filters.
    """
    wiki = Wiki(tmp_path)
    wiki.init(name='root')
    _make_category_folder(tmp_path, 'db', category, 'The db section.')
    (tmp_path / 'db' / 'notes.md').write_text(
        '---\nname: notes\ndesc: Db pages.\n---\n\n# notes\n\nBody words here.\n',
        encoding='utf-8',
    )
    (tmp_path / 'alpha.md').write_text(
        f'---\nname: alpha\ndesc: A page.\ncategory: {category}\n---\n\n'
        '# alpha\n\nBody.\n',
        encoding='utf-8',
    )
    wiki.update()

    # the folder resolves (never unindexed) and its subtree stays visible
    output = wiki.map()
    assert f'[{category}] db/' in output
    assert '(unindexed)' not in output
    assert 'notes' in output
    # the page reads its real word count, not 0 from a mis-resolved path
    count = re.search(rf'\[{re.escape(category)}\] alpha \((\d+)\)', output)
    assert count is not None
    assert count.group(1) != '0'
    # the category filter matches, and the markdown filter is not inverted
    filtered = wiki.map(category=[category])
    assert 'alpha' in filtered
    assert 'db/' in filtered
    assert 'alpha' in wiki.map(markdown=True)


def test_update_preserves_file_mode(tmp_path: pathlib.Path) -> None:
    """A rewrite keeps the page's permission bits.

    The atomic-write staging file is created 0600 by ``mkstemp`` and
    ``os.replace`` carries the temp's mode onto the target, so the helper
    must restore the original mode (and honor the umask for fresh files)
    or every update strips group/other read bits.
    """
    wiki = _make_wiki(tmp_path, folders={'notes': ['readme']})
    page = tmp_path / 'notes' / 'readme.md'
    page.chmod(0o604)
    # perturb the generated H1 so update rewrites the page
    text = page.read_text(encoding='utf-8')
    page.write_text(text.replace('# notes/readme', '# Wrong Title'), encoding='utf-8')
    assert wiki.update() == ['notes/readme.md']
    assert (page.stat().st_mode & 0o777) == 0o604


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

    # one update restores both fields and converges
    wiki.update()
    healed = index_path.read_text(encoding='utf-8')
    assert re.search(rf'^name: {re.escape(expected)}$', healed, re.M)
    assert re.search(r'^desc:', healed, re.M)
    assert wiki.update() == []


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


def test_map_category_shows_matches_beyond_depth(tmp_path: pathlib.Path) -> None:
    """A folder whose only category matches lie below ``--depth`` shows.

    The category prune drops a folder whose rendered children are empty,
    but a depth cutoff empties them for depth reasons, not content -- the
    subtree must be probed so ``--category X --depth 0`` never reports a
    populated wiki as empty.
    """
    wiki = _make_wiki(tmp_path)
    for folder_name, category in [('outer', 'keep'), ('plain', '')]:
        folder = tmp_path / folder_name
        folder.mkdir()
        frontmatter = '---\nname: inner\ndesc: Inner.\n'
        if category:
            frontmatter += f'category: {category}\n'
        (folder / 'inner.md').write_text(
            frontmatter + '---\n\n# inner\n\nBody.\n',
            encoding='utf-8',
        )
    wiki.update()

    # unlimited depth shows the folder and its matching page
    full = wiki.map(category=['keep'])
    assert 'outer/' in full
    assert 'inner' in full
    # a depth cutoff still shows the folder (its match lies beyond it) ...
    shallow = wiki.map(category=['keep'], depth=0)
    assert 'outer/' in shallow
    assert 'inner' not in shallow
    # ... while a folder with no matching descendants stays pruned
    assert 'plain/' not in shallow


def test_lint_accepts_anchor_links(tmp_path: pathlib.Path) -> None:
    """An Obsidian anchor link to an existing page is never stale.

    ``#`` is a denied name character, so everything after it in a
    ``[[page#heading]]`` / ``[[page#^block]]`` target addresses within
    the page; only the page part decides staleness.
    """
    wiki = _make_wiki(tmp_path, folders={'notes': ['q']})
    (tmp_path / 'anchor.md').write_text(
        '---\nname: anchor\ndesc: Anchor links.\n---\n\n# anchor\n\n'
        'See [[notes/q#top]] and [[notes/q#^block1]] but [[missing#x]].\n',
        encoding='utf-8',
    )
    wiki.update()

    # only the link whose page is gone is stale; anchors alone never are
    stale = [issue for issue in wiki.lint() if 'Stale link' in issue]
    assert len(stale) == 1
    assert 'missing' in stale[0]


def test_map_marks_copied_subtree_links_broken(tmp_path: pathlib.Path) -> None:
    """Map resolves entries by target, annotating preserved broken links.

    Copying a subtree keeps its root-relative links; update preserves
    them as broken beside the regenerated ones. Resolving entries by
    display label would render each broken link as its healthy same-named
    sibling, with no brokenness hint.
    """
    wiki = _make_wiki(tmp_path, folders={'src': ['doc']})
    shutil.copytree(tmp_path / 'src', tmp_path / 'dup')
    wiki.update()

    # the two healthy entries render with real counts; the preserved
    # broken link is annotated instead of impersonating its sibling
    output = wiki.map()
    assert len(re.findall(r'^\s*doc \(\d', output, re.M)) == 2
    assert len(re.findall(r'^\s*doc \(broken\)', output, re.M)) == 1


def test_update_survives_page_deleted_mid_plan(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A page deleted while update plans (a concurrent node) never crashes.

    In ``_read_child_labels``, reading pages without the ``None`` guard
    its folder branch has would let a page vanishing between enumeration
    and read raise ``AttributeError`` instead of degrading to the
    broken-link warning the next run reports.
    """
    wiki = _make_wiki(tmp_path, folders={'notes': ['doomed', 'readme']})
    doomed = tmp_path / 'notes' / 'doomed.md'
    real = Wiki._current_text

    def racy(
        self: Wiki,
        path: pathlib.Path,
        overlay: Optional[dict[pathlib.Path, str]] = None,
    ) -> Optional[str]:
        """Delete the doomed page just before update first reads it."""
        if path == doomed and doomed.exists():
            doomed.unlink()
        return real(self, path, overlay)

    # the mid-plan deletion is handled, not crashed on
    monkeypatch.setattr(Wiki, '_current_text', racy)
    wiki.update()

    # the next run degrades to the ordinary broken-link warning
    capsys.readouterr()
    wiki.update()
    err = capsys.readouterr().err
    assert 'Broken link' in err
    assert 'doomed' in err


@pytest.mark.parametrize('kind', ['page', 'index'])
def test_update_normalizes_crlf_file(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    kind: str,
) -> None:
    """A CRLF file is noted by lint and rewritten to LF by the next update.

    Universal-newline reads make a CRLF file look permanently clean --
    without a byte-level probe, update would never rewrite it, lint would
    never flag it, and mixed-EOL wikis would drift silently.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    if kind == 'page':
        target = tmp_path / 'core' / 'design.md'
    else:
        target = tmp_path / 'core' / '_index.md'
    rel = str(target.relative_to(tmp_path))
    # convert the converged file to CRLF, as a Windows editor would
    target.write_bytes(target.read_bytes().replace(b'\n', b'\r\n'))

    # lint notes the line endings without raising a hard issue
    capsys.readouterr()
    assert wiki.lint() == []
    err = capsys.readouterr().err
    assert f'{rel}: CRLF line endings; update will normalize' in err

    # a dry run reports the file; the real update rewrites it to LF
    assert rel in wiki.update(check=True)
    assert rel in wiki.update()
    assert b'\r' not in target.read_bytes()

    # converged: nothing further to update, and the note is gone
    assert wiki.update() == []
    capsys.readouterr()
    wiki.lint()
    assert 'CRLF' not in capsys.readouterr().err


def test_update_materializes_missing_settings(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
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
    capsys.readouterr()

    # a dry run tolerates the missing marker without restoring it
    assert wiki.update(check=True) == []
    assert not settings.exists()

    # a real update restores {} and announces the restoration, alone
    wiki.update()
    err = capsys.readouterr().err
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


def test_init_refuses_legacy_layout_before_writing(
    tmp_path: pathlib.Path,
) -> None:
    """Init on a legacy-layout wiki refuses up front, writing nothing.

    Init plans the same sweep update and lint refuse on: proceeding would
    seed ``.wiki/settings.json`` from defaults -- silently masking the
    legacy ``_config/settings.json`` policy -- and index ``_config/`` as
    content, so init must raise the migration steps before the settings
    seed, the root index, or any sweep write lands.
    """
    (tmp_path / '_config').mkdir()
    (tmp_path / '_config' / 'settings.json').write_text('{}\n', encoding='utf-8')
    (tmp_path / 'notes.md').write_text('# notes\n\nLegacy content.\n', encoding='utf-8')
    wiki = Wiki(tmp_path)
    with pytest.raises(ValueError, match=r'(?s)Legacy wiki layout.*wiki update'):
        wiki.init(name='root')

    # nothing was written: no marker, no root index, no indexed _config/
    assert not (tmp_path / '.wiki').exists()
    assert not (tmp_path / '_index.md').exists()
    assert not (tmp_path / '_config' / '_index.md').exists()


# ------ helpers


class CategorizedWiki(Wiki):
    """Wiki subclass with category ordering for tests."""

    category_order = ['node', 'store']


def _make_wiki(
    path: pathlib.Path,
    folders: Optional[dict[str, list[str]]] = None,
) -> Wiki:
    """Create a wiki with optional folders and pages."""
    # init the root wiki
    wiki = Wiki(path)
    wiki.init(name='root')
    # set root desc
    root_index = path / '_index.md'
    content = root_index.read_text(encoding='utf-8')
    content = content.replace('desc: ...', 'desc: The root wiki.')
    content = content.replace('***\n', '***\n\nRoot overview.\n')
    root_index.write_text(content, encoding='utf-8')
    # create folders and pages
    if folders:
        for folder_name, pages in folders.items():
            folder = path / folder_name
            folder.mkdir(parents=True, exist_ok=True)
            (folder / '_index.md').write_text(
                f'---\nname: {folder_name}\n'
                f'desc: The {folder_name} section.\n'
                f'---\n\n# {folder_name}\n\n***\n\n'
                f'Overview of {folder_name}.\n',
                encoding='utf-8',
            )
            for page in pages:
                (folder / f'{page}.md').write_text(
                    f'---\nname: {page}\n'
                    f'desc: The {page} page.\n'
                    f'---\n\n# {page}\n\nContent for {page}.\n',
                    encoding='utf-8',
                )
    wiki.update()
    return wiki


def _make_category_folder(
    path: pathlib.Path,
    name: str,
    category: str = '',
    desc: str = 'A section.',
) -> pathlib.Path:
    """Create a child folder whose ``_index.md`` carries a category and desc."""
    folder = path / name
    folder.mkdir(parents=True, exist_ok=True)
    frontmatter = f'---\nname: {name}\ndesc: {desc}\n'
    if category:
        frontmatter += f'category: {category}\n'
    frontmatter += 'tags: []\n---\n\n# x\n\n***\n\nOverview.\n'
    (folder / '_index.md').write_text(frontmatter, encoding='utf-8')
    return folder
