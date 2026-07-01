"""Tests for ``Wiki`` init, config, update, lint, read, map, validation, and merging."""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
from typing import Optional

import pytest

from wiki.core.wiki import _OFFLINE_MODE, Wiki, _format_words

__all__ = [
    'test_init_creates_structure',
    'test_update_full_workflow',
    'test_update_preserves_content',
    'test_update_preserves_content_with_thematic_break',
    'test_update_preserves_frontmatter_with_dashes',
    'test_update_no_delimiter_keeps_content',
    'test_update_preserves_body_with_unclosed_frontmatter',
    'test_lint_reports_missing_root_name_without_crashing',
    'test_update_survives_backslash_digit_name',
    'test_update_accepts_block_scalar_desc',
    'test_update_accepts_block_scalar_name',
    'test_update_folds_and_preserves_inline_desc',
    'test_update_preserves_prose_above_delimiter',
    'test_update_preserves_prose_below_delimiter_above_h1',
    'test_validate_name_strict_via_settings',
    'test_init_scaffolds_settings',
    'test_init_seeds_custom_settings',
    'test_settings_reject_malformed_values',
    'test_timestamp_format_configurable',
    'test_map_presentation_configurable',
    'test_slice_units',
    'test_lint_flags_invalid_name',
    'test_update_broken_links',
    'test_update_scoped',
    'test_lint_flags_what_update_fixes',
    'test_lint_flags_human_only_issues',
    'test_lint_missing_index',
    'test_lint_cascade_through_tree',
    'test_update_check_reports_without_writing',
    'test_lint_diff_set_matches_update',
    'test_lint_conflict_markers_suppress_diff',
    'test_lint_link_desc_period',
    'test_lint_scoped',
    'test_new_file_created_equals_updated',
    'test_lint_ignores_code_blocks',
    'test_lint_clean',
    'test_read_resolution',
    'test_read_line_slicing',
    'test_read_frontmatter_category',
    'test_update_category_labels',
    'test_category_propagates_and_clears',
    'test_sort_unlisted_category',
    'test_page_category',
    'test_map_output',
    'test_map_unindexed',
    'test_map_word_counts',
    'test_body_includes_h1_for_counts_and_search',
    'test_map_handles_dotted_markdown_stem',
    'test_format_words',
    'test_update_config_installs_plugin',
    'test_update_config_offline_warns',
    'test_update_config_preserves_existing',
    'test_update_config_is_idempotent',
    'test_update_config_requires_config_dir',
    'test_update_config_rejects_type_mismatch',
    'test_update_config_reports_malformed_target_json',
    'test_update_config_offline_mode',
    'test_update_config_rejects_bad_offline_mode',
    'test_init_rejects_bad_offline_mode_before_scaffolding',
    'test_validate_name',
    'test_config_page_reserved_only_at_root',
    'test_merge_driver',
    'test_update_skips_invalid_name',
    'test_read_suggests_unique_leaf_match',
    'test_lint_stale_body_link_names_canonical',
    'test_markerless_index_warns_in_map_and_flags_in_lint',
    'test_lint_flags_folder_shadowing_page',
    'test_timestamp_format_rejects_blank_or_multiline',
]


def test_init_creates_structure(tmp_path: pathlib.Path) -> None:
    """Init creates root index and config."""
    # init creates root _index.md
    root = tmp_path / 'wiki'
    wiki = Wiki(root)
    wiki.init()
    assert (root / '_index.md').is_file()

    # obsidian config template seeded
    assert (root / '_config' / 'obsidian').is_dir()

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

    # word counts computed
    assert 'page_words:' in design

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


def test_update_preserves_body_with_unclosed_frontmatter(
    tmp_path: pathlib.Path,
) -> None:
    """Update never discards a page body whose frontmatter has no closing '---'.

    Regression: unclosed frontmatter used to be consumed to EOF, so the
    page-words insertion (``rfind('---')`` at the opener) wiped the whole body.
    """
    wiki = _make_wiki(tmp_path, folders={'notes': ['readme']})
    # opening '---' but NO closing '---', then real body text
    page = tmp_path / 'notes' / 'readme.md'
    page.write_text(
        '---\nname: readme\ndesc: Important.\n\nCritical body that must survive.\n',
        encoding='utf-8',
    )
    wiki.update()
    assert 'Critical body that must survive.' in page.read_text(encoding='utf-8')


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

    # add a new page to core only
    (tmp_path / 'core' / 'new_page.md').write_text(
        '---\nname: new_page\ndesc: New.\n---\n\n# new_page\n\nNew content.\n',
        encoding='utf-8',
    )

    # scoped update to core
    updated = wiki.update(name='core')

    # only core files were updated
    for path in updated:
        assert 'core' in path


@pytest.mark.parametrize(
    'perturb',
    [
        'stale_word_count',
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
    if perturb == 'stale_word_count':
        text = page.read_text(encoding='utf-8')
        page.write_text(
            text.replace('Content for design.', 'word ' * 30),
            encoding='utf-8',
        )
    elif perturb == 'changed_link_label':
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
                line for line in text.splitlines() if not line.startswith('page_words:')
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

    # the issue is flagged, and update does not silence it
    assert any(message in issue for issue in wiki.lint())
    wiki.update()
    assert any(message in issue for issue in wiki.lint())


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


def test_lint_cascade_through_tree(tmp_path: pathlib.Path) -> None:
    """A deep edit is flagged on the page and every ancestor; one update fixes all."""
    wiki = _make_wiki(
        tmp_path,
        folders={'core': ['design'], 'core/store': ['db']},
    )
    assert wiki.lint() == []
    # capture a grandparent's tree_words before the edit
    grandparent = tmp_path / 'core' / '_index.md'
    before = int(
        re.search(
            r'^tree_words:\s*(\d+)',
            grandparent.read_text(encoding='utf-8'),
            re.M,
        ).group(1)
    )
    # edit a deep page's body so its count -- and every ancestor's tree_words -- drift
    db = tmp_path / 'core' / 'store' / 'db.md'
    db.write_text(
        db.read_text(encoding='utf-8').replace('Content for db.', 'word ' * 40),
        encoding='utf-8',
    )
    flagged = '\n'.join(wiki.lint())
    assert 'core/store/db.md' in flagged
    assert 'core/store/_index.md' in flagged
    assert 'core/_index.md' in flagged
    # one update clears the cascade and honestly reports every file it wrote,
    # including ancestor indexes whose only change is a refreshed tree_words
    written = wiki.update()
    assert 'core/store/db.md' in written
    assert 'core/store/_index.md' in written
    assert 'core/_index.md' in written
    # the descendant's new words actually propagated into the grandparent's total
    after = int(
        re.search(
            r'^tree_words:\s*(\d+)',
            grandparent.read_text(encoding='utf-8'),
            re.M,
        ).group(1)
    )
    assert after > before
    assert wiki.lint() == []


def test_update_check_reports_without_writing(tmp_path: pathlib.Path) -> None:
    """update(check=True) reports would-change files but writes nothing."""
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    page = tmp_path / 'core' / 'design.md'
    page.write_text(
        page.read_text(encoding='utf-8').replace('Content for design.', 'word ' * 30),
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
        page.read_text(encoding='utf-8').replace('Content for design.', 'word ' * 15),
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
            path.read_text(encoding='utf-8').replace('Content for', 'word word word'),
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

    ``tree_words`` includes the folder's own index prose, so a leaf folder
    shows equal halves -- ``(N/N)`` -- rather than the old ``(N/0)``, and a
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
    assert wiki._read_frontmatter_page_words(tmp_path / 'topic.md') == 5
    # search matches the page's H1 line (frontmatter is skipped; prose lacks it)
    hits = wiki.search('topic')
    assert any(path == 'topic.md' and '# topic' in line for path, _, line in hits)
    # the index's auto-generated link block is body too, so it is matched as well
    assert any('_index.md' in path for path, _, _ in hits)


def test_map_handles_dotted_markdown_stem(tmp_path: pathlib.Path) -> None:
    """A dotted markdown stem (``my.notes.md``) counts words and filters as md.

    The map resolved such a page by a name test (``'.' in name``), reading its
    word count from a missing file (0) and inverting the ``--markdown`` filter. It
    now probes the actual ``<name>.md`` file.
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
    # init seeds the front matter title plugin into _config/obsidian
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
    # init seeds the front matter title plugin into _config/obsidian
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


def test_update_config_preserves_existing(
    tmp_path: pathlib.Path,
    stub_download: None,
) -> None:
    """``update_config`` merges into existing config without clobbering it."""
    # init seeds the front matter title plugin into _config/obsidian
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
    config_dir = tmp_path / '_config' / 'obsidian'
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


def test_update_config_requires_config_dir(tmp_path: pathlib.Path) -> None:
    """``update_config`` raises when ``_config/obsidian`` is absent."""
    # a wiki that was never initialized has no _config/obsidian
    wiki = Wiki(tmp_path)
    with pytest.raises(FileNotFoundError):
        wiki.update_config()


def test_update_config_rejects_type_mismatch(
    tmp_path: pathlib.Path,
    stub_download: None,
) -> None:
    """``update_config`` raises on a top-level JSON type mismatch."""
    wiki = Wiki(tmp_path)
    wiki.init()

    # a list source against a dict target cannot be merged
    config_dir = tmp_path / '_config' / 'obsidian'
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
    config_dir = tmp_path / '_config' / 'obsidian'
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
        ('_config', True),
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
        'config-subfolder',
        'empty',
    ],
)
def test_validate_name(tmp_path: pathlib.Path, name: str, valid: bool) -> None:
    """The default policy is lenient: any name except the structural characters."""
    wiki = Wiki(tmp_path)
    assert wiki.validate_name(name) == valid


def test_config_page_reserved_only_at_root(tmp_path: pathlib.Path) -> None:
    """``_config`` is a valid page name in a subfolder but reserved at the root.

    The ``_config`` stem guards the wiki's root ``_config/`` directory, which exists
    only at the root -- so a subfolder page may mirror a source file named
    ``_config.py``, while a ``_config.md`` at the root still collides.
    """
    wiki = Wiki(tmp_path)
    wiki.init('root')
    # a subfolder _config page has no _config/ dir to collide with -> valid
    section = tmp_path / 'project'
    section.mkdir()
    (section / '_config.md').write_text('# project/_config\n', encoding='utf-8')
    wiki.update()
    assert not any('Invalid page name' in issue for issue in wiki.lint())
    # at the root, _config.md collides with the _config/ directory -> rejected
    (tmp_path / '_config.md').write_text('# _config\n', encoding='utf-8')
    assert any('_config.md: Invalid page name' in issue for issue in wiki.lint())


def test_validate_name_strict_via_settings(tmp_path: pathlib.Path) -> None:
    """A ``settings.json`` naming block can restore the strict identifier rule."""
    config = tmp_path / '_config'
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


def test_init_scaffolds_settings(tmp_path: pathlib.Path) -> None:
    """``init`` writes a discoverable ``_config/settings.json`` with naming defaults."""
    wiki = Wiki(tmp_path)
    wiki.init(name='Root')
    settings = tmp_path / '_config' / 'settings.json'
    assert settings.is_file()
    data = json.loads(settings.read_text(encoding='utf-8'))
    assert data['naming']['validate'] == []  # the lenient default, spelled out


def test_init_seeds_custom_settings(tmp_path: pathlib.Path) -> None:
    """``init(settings=...)`` seeds the caller's ``settings.json`` and applies it."""
    policy = {'naming': {'validate': ['ascii', 'identifier']}}
    Wiki(tmp_path).init(name='Root', settings=policy)
    # the seeded settings.json is exactly the caller's policy ...
    settings = tmp_path / '_config' / 'settings.json'
    data = json.loads(settings.read_text(encoding='utf-8'))
    assert data == policy
    # ... and a fresh instance reads it: the strict rule rejects a dashed name
    wiki = Wiki(tmp_path)
    assert wiki.validate_name('my_page')
    assert not wiki.validate_name('bad-name')


@pytest.mark.parametrize(
    'settings',
    [
        {'naming': {'validate': ['no_such_predicate']}},
    ],
    ids=[
        'bad-predicate',
    ],
)
def test_settings_reject_malformed_values(
    tmp_path: pathlib.Path,
    settings: dict,
) -> None:
    """Malformed ``settings.json`` knobs fail loudly at the boundary.

    The naming policy resolver raises ``ValueError`` on an out-of-range or
    wrong-typed value rather than silently falling back to a default.
    """
    config = tmp_path / '_config'
    config.mkdir()
    (config / 'settings.json').write_text(json.dumps(settings), encoding='utf-8')
    wiki = Wiki(tmp_path)

    def trigger() -> None:
        """Touch the naming resolver through every entry point that reads it."""
        _ = wiki._naming
        wiki.lint()
        wiki.init(name='root')

    # the bad value fails loudly rather than silently falling back to a default
    with pytest.raises(ValueError):
        trigger()


def test_timestamp_format_configurable(tmp_path: pathlib.Path) -> None:
    """``timestamp.format`` controls the timestamp string format."""
    config = tmp_path / '_config'
    config.mkdir()
    (config / 'settings.json').write_text(
        json.dumps({'timestamp': {'format': '%Y'}}), encoding='utf-8'
    )
    stamp = Wiki(tmp_path)._utc_now()
    assert stamp.isdigit()
    assert len(stamp) == 4  # just the year


def test_map_presentation_configurable(tmp_path: pathlib.Path) -> None:
    """settings.json ``map.*`` knobs customize the indent unit and ellipsis."""
    config = tmp_path / '_config'
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


def test_slice_units(tmp_path: pathlib.Path) -> None:
    """``_slice`` slices by words/lines/chars; words keep original spacing.

    Only the frontmatter is special: the H1 leads the body, so it occupies the
    first word/line/char positions and is sliced alongside the prose.
    """
    wiki = Wiki(tmp_path)
    path = tmp_path / 'p.md'
    body = 'Alpha   beta gamma\ndelta epsilon.'
    content = f'---\nname: P\ndesc: A page.\n---\n\n# P\n\n{body}\n'
    # the H1 leads the body: words 2:4 reach the prose, keeping original spacing
    assert 'Alpha   beta' in wiki._slice(content, path, 2, 4, 'words')
    # the first body line is the H1 heading, not the prose
    out = wiki._slice(content, path, 0, 1, 'lines')
    assert '# P' in out
    assert 'Alpha' not in out
    # chars slice by character, reaching the prose past the leading H1
    assert wiki._slice(content, path, 5, 10, 'chars').strip().endswith('Alpha')


def test_merge_driver(tmp_path: pathlib.Path) -> None:
    """Merge driver resolves frontmatter and preserves content conflicts."""
    # locate the merge driver script in the package
    merge_script = (
        pathlib.Path(__file__).resolve().parent.parent.parent
        / 'wiki'
        / '_config'
        / 'git'
        / 'merge_index.sh'
    )
    if not merge_script.is_file():
        pytest.skip('merge_index.sh not found')

    # set up ancestor, ours, theirs files
    ancestor = tmp_path / 'ancestor.md'
    ours = tmp_path / 'ours.md'
    theirs = tmp_path / 'theirs.md'

    fm = '---\nname: test\ndesc: Original.\n---\n\n'
    ancestor.write_text(fm + '# test\n\n***\n\nShared content.\n', encoding='utf-8')
    ours.write_text(fm + '# test\n\n***\n\nOur changes.\n', encoding='utf-8')
    theirs.write_text(fm + '# test\n\n***\n\nTheir changes.\n', encoding='utf-8')

    # run merge driver (arg order matches the git driver: %A %O %B == ours base theirs)
    subprocess.run(
        ['bash', str(merge_script), str(ours), str(ancestor), str(theirs)],
        capture_output=True,
        text=True,
    )

    # merge driver writes the merged result back to the "ours" file
    merged = ours.read_text(encoding='utf-8')

    # above *** is regenerated content -- taken from ours unconditionally
    assert 'name: test' in merged
    # below *** is a real three-way merge: both sides edited the same line,
    # so the content conflict is surfaced with markers and both versions
    assert '<<<<<<<' in merged
    assert 'Our changes' in merged
    assert 'Their changes' in merged


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


def test_lint_stale_body_link_names_canonical(tmp_path: pathlib.Path) -> None:
    """A folder-relative body link is flagged with its root-relative fix."""
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
            'See [[../overview]] for context.',
        ),
        encoding='utf-8',
    )
    wiki.update()
    # the stale link is flagged with the canonical [[overview]] as the fix
    stale = [issue for issue in wiki.lint() if 'Stale link [[../overview]]' in issue]
    assert stale
    assert all('(use [[overview]])' in issue for issue in stale)


def test_markerless_index_warns_in_map_and_flags_in_lint(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A root index that lost its ``***`` is named by map (warn) and lint.

    Without the delimiter the link block folds into user content and parses to
    zero links; map must warn rather than read the populated wiki as empty, and
    lint must name the missing marker so the cause is obvious.
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
    config = tmp_path / '_config'
    config.mkdir()
    (config / 'settings.json').write_text(
        json.dumps({'timestamp': {'format': format}}),
        encoding='utf-8',
    )
    # the bad format fails loudly when update resolves the timestamp policy
    with pytest.raises(ValueError, match='single non-empty line'):
        Wiki(tmp_path).update()


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
