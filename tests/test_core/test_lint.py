"""Behavioral tests for ``Wiki.lint``.

The issue taxonomy: out-of-date diffs pinned to what ``update``
would write, human-only issues that persist after update,
formatter-damage diagnosis, ``no-lint`` region suppression, and the
hard-issue vs soft-note split.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
from typing import Optional

import pytest

from wiki.core import format
from wiki.core.wiki import Issue, Wiki
from wiki.util import markdown

from ._helpers import (
    _capture_notices,
    _git,
    _git_repo,
    _make_wiki,
    _needs_git,
    _needs_unprivileged,
    _set_exclude_patterns,
    page_index,
)

__all__ = [
    'test_lint_reports_missing_root_name_without_crashing',
    'test_lint_flags_invalid_name',
    'test_lint_names_the_failing_naming_rule',
    'test_lint_flags_what_update_fixes',
    'test_lint_issues_are_typed',
    'test_lint_notes_unconfigured_merge_driver',
    'test_lint_names_bare_page',
    'test_lint_names_nested_wiki_root',
    'test_lint_flags_human_only_issues',
    'test_lint_names_formatter_damage',
    'test_lint_names_formatter_damage_with_multiline_desc',
    'test_lint_truncated_index_is_not_formatter_damage',
    'test_link_shaped_desc_continuation_lints_clean',
    'test_damage_shaped_desc_continuation_lints_clean',
    'test_lint_allows_thematic_break_in_body',
    'test_lint_missing_index',
    'test_lint_diff_set_matches_update',
    'test_lint_conflict_markers_suppress_diff',
    'test_lint_flags_leftover_merge_hint',
    'test_lint_link_desc_period',
    'test_lint_scoped',
    'test_lint_flags_blank_created',
    'test_lint_flags_unparseable_stamp',
    'test_lint_future_stamp_is_clean',
    'test_lint_stamp_parse_follows_configured_format',
    'test_lint_flags_frontmatter_a_strict_yaml_reader_rejects',
    'test_lint_flags_a_stamp_that_is_a_sequence',
    'test_lint_flags_a_block_whose_repair_would_break_it',
    'test_lint_flags_an_unaddressable_block_as_malformed',
    'test_lint_reports_deep_nesting_instead_of_crashing',
    'test_lint_reports_an_escape_naming_no_character',
    'test_lint_invalid_yaml_yields_to_merge_states',
    'test_lint_names_a_comment_truncated_desc',
    'test_lint_hyphen_dangle',
    'test_lint_wrapped_list_marker',
    'test_lint_blank_led_list_is_clean',
    'test_lint_code_span_lead_is_not_marker',
    'test_lint_code_span_continuation_keeps_list_open',
    'test_lint_ignores_code_blocks',
    'test_lint_ignores_multiline_code_span',
    'test_lint_conflict_markers_scan_raw',
    'test_no_lint_region_scopes_positional_rules',
    'test_region_directive_pairing_errors',
    'test_region_directives_pair_per_directive',
    'test_lint_clean',
    'test_lint_survives_page_deleted_mid_walk',
    'test_lint_survives_page_deleted_before_crlf_probe',
    'test_lint_survives_folder_deleted_mid_walk',
    'test_lint_survives_index_deleted_mid_check',
    'test_quoted_placeholder_desc_is_soft',
    'test_long_desc_is_note_only',
    'test_lint_relative_prefix_inside_wiki_is_issue',
    'test_lint_relative_root_link_names_the_index_page',
    'test_lint_directory_link_is_issue_naming_index_form',
    'test_lint_link_rules_spare_samples_not_prose',
    'test_lint_directory_link_keeps_display_text',
    'test_lint_index_form_link_is_clean',
    'test_index_broken_link_is_issue_but_body_link_is_note',
    'test_lint_names_excluded_link_target',
    'test_lint_silent_inside_excluded_subtree',
    'test_lint_stale_link_into_excluded_subtree_is_live',
    'test_lint_directory_link_to_unindexed_folder_is_live',
    'test_lint_flags_folder_shadowing_page',
    'test_lint_accepts_anchor_links',
    'test_lint_link_probes_never_raise',
]


# ------ update-repairable issues


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


def test_lint_names_the_failing_naming_rule(tmp_path: pathlib.Path) -> None:
    """An invalid-name issue says which naming rule the name breaks.

    A rejection that names the rule is diagnosable on its own; the message
    carries no remediation how-to.
    """
    # an identifier-policy wiki (the strict project-wiki seed shape)
    wiki = Wiki(tmp_path)
    wiki.init(
        name='root',
        settings={'naming': {'validate': ['ascii', 'identifier']}},
    )
    (tmp_path / 'command-core.md').write_text(
        '---\nname: command-core\ndesc: A page.\n---\n\n# x\n\nBody.\n',
        encoding='utf-8',
    )

    # the issue names the broken rule, with no how-to
    issues = wiki.lint()
    invalid = [issue for issue in issues if 'Invalid page name' in issue]
    assert invalid
    assert all("fails the 'identifier' rule" in issue for issue in invalid)
    assert all('naming.allow' not in issue for issue in invalid)
    assert all('snake_case' not in issue for issue in invalid)


@pytest.mark.parametrize(
    argnames='perturb',
    argvalues=[
        'changed_link_label',
        'wrong_heading',
        'missing_field',
        'missing_marker',
        'missing_page_frontmatter',
        'misplaced_title',
        'null_title',
        'deleted_page_row',
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
    elif perturb == 'misplaced_title':
        # a title at the block tail belongs directly under name
        text = index.read_text(encoding='utf-8')
        index.write_text(
            text.replace('\n---\n', '\ntitle: Fancy\n---\n', 1),
            encoding='utf-8',
        )
    elif perturb == 'null_title':
        # a null title is the transient unset request update removes
        text = index.read_text(encoding='utf-8')
        index.write_text(
            text.replace('name: core\n', 'name: core\ntitle: null\n'),
            encoding='utf-8',
        )
    elif perturb == 'deleted_page_row':
        # a deleted target's row is drift too: update prunes it
        page.unlink()

    # lint flags the drift; one update fixes it; lint is then clean
    assert wiki.lint() != []
    assert wiki.update() != []
    assert wiki.lint() == []
    assert wiki.update(check=True) == []


def test_lint_issues_are_typed(tmp_path: pathlib.Path) -> None:
    """Every lint issue carries its machine kind and payload fields.

    ``lint --json`` renders issues from the typed fields, so an issue
    kind emitting a bare prose string is a defect. The fixture exhibits
    every issue-emitting surface (the gitignored link-target cause has
    its own git-backed test): each returned issue must be an ``Issue``
    whose ``kind`` is set and whose prose line opens with the file its
    ``path`` field names.
    """
    _make_wiki(
        tmp_path,
        folders={
            'core': ['design', 'ghost'],
            'data': ['report', 'keep', 'shadowed'],
        },
    )
    # a broken link (and its pending prune diff)
    (tmp_path / 'core' / 'ghost.md').unlink()
    # a bare page
    (tmp_path / 'core' / 'bare.md').write_text('# Bare\n\nBody.\n', encoding='utf-8')
    # an invalid folder name (and its missing index)
    (tmp_path / 'Bad#Folder').mkdir()
    # an invalid page name
    (tmp_path / 'core' / 'bad#page.md').write_text(
        '---\nname: x\ndesc: A page.\n---\n\n# x\n\nBody.\n',
        encoding='utf-8',
    )
    # a folder shadowing its same-named page
    (tmp_path / 'data' / 'shadowed').mkdir()
    # merge conflict markers
    (tmp_path / 'core' / 'conflicted.md').write_text(
        '---\nname: conflicted\ndesc: A page.\n---\n\n# conflicted\n\n'
        '<<<<<<< ours\na\n=======\nb\n>>>>>>> theirs\n',
        encoding='utf-8',
    )
    # escaped wikilinks (the formatter-damage signature)
    (tmp_path / 'data' / 'escaped.md').write_text(
        '---\nname: escaped\ndesc: A page.\n---\n\n# escaped\n\n'
        'See \\[\\[design notes\\]\\] for more.\n',
        encoding='utf-8',
    )
    # unclosed frontmatter
    (tmp_path / 'data' / 'malformed.md').write_text(
        '---\nname: malformed\ndesc: A page.\n\n# malformed\n\nBody.\n',
        encoding='utf-8',
    )
    # a period-less desc, an unparseable stamp, both wrap mangles, a
    # directory link, a relative link, and a dangling region marker on
    # one messy page
    (tmp_path / 'data' / 'messy.md').write_text(
        '---\nname: messy\ndesc: No trailing period\n'
        'created: not-a-stamp\n---\n\n# messy\n\n'
        'a twenty-\nclass system, that\n+ wraps into a marker.\n\n'
        'See [[core]] and [[./keep]] for more.\n\n<!-- start: no-lint -->\n',
        encoding='utf-8',
    )
    # frontmatter a strict YAML reader rejects
    (tmp_path / 'data' / 'unquoted.md').write_text(
        '---\nname: unquoted\ndesc: A lane for X: HR, WR.\n---\n\n# unquoted\n\nBody.\n',
        encoding='utf-8',
    )
    # an emptied index (and, with a page to link, its missing delimiter)
    trunc = tmp_path / 'trunc'
    trunc.mkdir()
    (trunc / '_index.md').write_text('', encoding='utf-8')
    (trunc / 'stub.md').write_text(
        '---\nname: stub\ndesc: A page.\n---\n\n# stub\n\nBody.\n',
        encoding='utf-8',
    )
    # a nested declared wiki
    nested = tmp_path / 'backup'
    (nested / '.wiki').mkdir(parents=True)
    (nested / '.wiki' / 'settings.json').write_text('{}\n', encoding='utf-8')
    # an invalid root wiki name
    root_index = tmp_path / '_index.md'
    root_index.write_text(
        root_index.read_text(encoding='utf-8').replace('name: root', 'name: bad#root'),
        encoding='utf-8',
    )
    # a row into a freshly excluded page, plus required titles
    settings = tmp_path / '.wiki' / 'settings.json'
    data = json.loads(settings.read_text(encoding='utf-8'))
    data['exclude'] = {'patterns': ['data/keep.md']}
    data['titles'] = {'required': True}
    settings.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    # a row whose target became a symlink
    secret = tmp_path.parent / 'typed_issues_outside'
    secret.write_text('outside\n', encoding='utf-8')
    report = tmp_path / 'data' / 'report.md'
    report.unlink()
    report.symlink_to(secret)

    issues = Wiki(tmp_path).lint()
    assert issues
    assert all(isinstance(issue, Issue) for issue in issues)
    assert all(issue.kind for issue in issues)
    # the prose line opens with the file the typed path field names
    assert all(str(issue).startswith(issue.fields['path']) for issue in issues)
    kinds = {issue.kind for issue in issues}
    assert kinds == {
        'bare_page',
        'broken_link',
        'conflict_markers',
        'directory_link',
        'escaped_wikilinks',
        'excluded_link_target',
        'hyphen_dangle',
        'invalid_folder_name',
        'invalid_page_name',
        'invalid_wiki_name',
        'invalid_yaml',
        'malformed_frontmatter',
        'missing_delimiter',
        'missing_index',
        'missing_period',
        'missing_title',
        'nested_wiki_root',
        'region_marker',
        'relative_link',
        'requires_update',
        'shadowed_page',
        'symlink_link_target',
        'truncated_index',
        'unparseable_stamp',
        'wrapped_marker',
    }


@_needs_git
def test_lint_notes_unconfigured_merge_driver(tmp_path: pathlib.Path) -> None:
    """A clone carrying ``merge=wiki`` with no driver config draws a note.

    Only ``.gitattributes`` travels with the repository -- the
    ``merge.wiki.driver`` config is per clone -- so a fresh clone
    text-merges ``_index.md`` files silently at its first merge. Lint
    notes the gap (soft, exit unchanged, a typed ``--json`` row), and
    wiring the config half silences it.
    """
    _git_repo(tmp_path)
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    (tmp_path / '.gitattributes').write_text(
        '**/_index.md merge=wiki\n', encoding='utf-8'
    )

    # the gap is a note, never an issue, naming the fix
    notices = _capture_notices(wiki)
    assert wiki.lint() == []
    gaps = [
        event.description
        for event in notices
        if 'merge.wiki.driver is not configured' in event.description
    ]
    assert gaps
    assert all('wiki config' in gap for gap in gaps)

    # wiring the config half silences the note
    _git(tmp_path, 'config', 'merge.wiki.driver', 'wiki _merge %O %A %B %L %P')
    wiki = Wiki(tmp_path)
    notices = _capture_notices(wiki)
    assert wiki.lint() == []
    assert not any('merge.wiki.driver' in event.description for event in notices)


def test_lint_names_bare_page(tmp_path: pathlib.Path) -> None:
    """A frontmatterless page draws a named hard issue until adopted.

    The bare page is already diff-flagged as out of date; the named
    issue says what the rewrite is -- an adoption -- and one update
    clears it.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    (tmp_path / 'core' / 'notes.md').write_text(
        '# Notes\n\nBody prose.\n',
        encoding='utf-8',
    )

    # the bare page is named beside its adoption diff; update clears it
    issues = wiki.lint()
    assert 'core/notes.md: Bare page (no frontmatter); update will adopt it' in issues
    wiki.update()
    assert wiki.lint() == []


def test_lint_names_nested_wiki_root(tmp_path: pathlib.Path) -> None:
    """A nested declared wiki is a hard issue naming its root.

    ``update`` refuses to sweep across a nested ``.wiki/settings.json``,
    so lint names the marker as the root cause rather than leaving the
    would-be absorption diffs as the only signal.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    nested = tmp_path / 'backup'
    (nested / '.wiki').mkdir(parents=True)
    (nested / '.wiki' / 'settings.json').write_text('{}\n', encoding='utf-8')

    issues = wiki.lint()
    needle = 'backup/: Nested wiki root (declared by .wiki/settings.json)'
    assert any(issue.startswith(needle) for issue in issues)


# ------ human-only issues


@pytest.mark.parametrize(
    argnames=('perturb', 'message'),
    argvalues=[
        ('invalid_folder', 'Invalid folder name'),
        ('invalid_page', 'Invalid page name'),
        ('invalid_nonmd', 'Invalid page name'),
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


# ------ formatter damage


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
    run; the classifier must walk past them the way
    ``format.reclaim_link_run`` does, or the report degrades to the bare
    missing-delimiter message and hides the formatter cause.
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


def test_damage_shaped_desc_continuation_lints_clean(tmp_path: pathlib.Path) -> None:
    r"""A desc continuation carrying the damage shape escapes and lints clean.

    ``escape_desc`` rewrites a damage-shaped continuation (``\[[...``)
    to the healthy ``[\[`` escape, whose interior ``\[[`` the
    formatter-damage signature exempts -- the generated index converges
    instead of drawing an Escaped-wikilinks issue no update can clear.
    The raw shape in the child's own desc still flags on the child, the
    surface a human fix must target.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['api', 'design']})
    # author a desc whose continuation line carries the damage shape
    page = tmp_path / 'core' / 'design.md'
    page.write_text(
        '---\nname: design\ndesc: |\n  The design page.\n'
        '  \\[[core/api|api]]: the database layer.\n'
        '---\n\n# design\n\nBody.\n',
        encoding='utf-8',
    )

    # the continuation escapes inside its brackets and converges
    wiki.update()
    index_path = tmp_path / 'core' / '_index.md'
    text = index_path.read_text(encoding='utf-8')
    assert '[\\[[core/api|api]]: the database layer.' in text
    assert wiki.update() == []

    # the healthy index escape never reads as formatter damage
    issues = wiki.lint()
    assert not any(
        '_index.md' in issue and 'Escaped wikilinks' in issue for issue in issues
    )


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


# ------ missing index and update diffs


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


@pytest.mark.parametrize(
    argnames='placement',
    argvalues=['frontmatter', 'body'],
)
def test_lint_flags_leftover_merge_hint(
    tmp_path: pathlib.Path,
    placement: str,
) -> None:
    """A merge repair hint left behind after a resolution is a hard issue.

    The driver plants the hint above the first conflict marker, so it
    lands wherever the conflict was -- inside the frontmatter (the usual
    spot, the ``updated:`` stamps differing first) it parses as an
    authored key that every later rewrite carries forward. Nothing else
    in the tool sees it there, so the resolution that dropped the
    markers and forgot the hint would otherwise be permanently clean.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    index = tmp_path / 'core' / '_index.md'
    hint = (
        '<!-- index *** separator missing on one side: likely formatter'
        ' damage; restore the *** line (wiki update repairs it), redo the'
        ' merge, and delete this line when resolving -->'
    )
    lines = index.read_text(encoding='utf-8').split('\n')
    lines.insert(2 if placement == 'frontmatter' else -2, hint)
    index.write_text('\n'.join(lines), encoding='utf-8')

    # the hint is named as debris, and the sweep does not carry it off
    issues = wiki.lint()
    assert 'core/_index.md: Leftover merge repair hint;' in '\n'.join(issues)
    assert [issue.kind for issue in issues if 'repair hint' in issue] == ['merge_hint']
    wiki.update()
    assert hint in index.read_text(encoding='utf-8')
    assert any(issue.kind == 'merge_hint' for issue in Wiki(tmp_path).lint())


# ------ scoping and field rules


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


@page_index
@pytest.mark.parametrize('field', ['created', 'updated'])
def test_lint_flags_unparseable_stamp(
    tmp_path: pathlib.Path,
    field: str,
    kind: str,
) -> None:
    """A non-blank stamp that defies the timestamp format is a hard issue.

    The stamps are tool-owned, so lint cannot tell a hand edit from a
    tool write by value -- but a value the configured format cannot
    parse is detectable damage, flagged naming the file and field.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    name = 'design.md' if kind == 'page' else '_index.md'
    path = tmp_path / 'core' / name
    path.write_text(
        re.sub(
            pattern=rf'^{field}:.*$',
            repl=f'{field}: around noon',
            string=path.read_text(encoding='utf-8'),
            count=1,
            flags=re.MULTILINE,
        ),
        encoding='utf-8',
    )
    issues = wiki.lint()
    flagged = [issue for issue in issues if f'Unparseable {field}' in issue]
    assert flagged
    assert all(f'core/{name}' in issue for issue in flagged)


def test_lint_future_stamp_is_clean(tmp_path: pathlib.Path) -> None:
    """A parseable stamp is never judged against a clock.

    Machines sharing a wiki skew, so the stamps are tool-owned rather
    than audited: a future-dated ``created:``/``updated:`` pair that
    parses under the configured format lints clean.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    page = tmp_path / 'core' / 'design.md'
    text = page.read_text(encoding='utf-8')
    for field in ('created', 'updated'):
        text = re.sub(
            pattern=rf'^{field}:.*$',
            repl=f'{field}: 2999-01-01T00:00:00Z',
            string=text,
            count=1,
            flags=re.MULTILINE,
        )
    page.write_text(text, encoding='utf-8')
    assert wiki.lint() == []


def test_lint_stamp_parse_follows_configured_format(
    tmp_path: pathlib.Path,
) -> None:
    """The stamp parse honors a custom ``timestamp.format``.

    Under a custom format the tool's own stamps parse clean, and a
    default-ISO value is the unparseable one.
    """
    wiki = Wiki(tmp_path)
    wiki.init(name='root', settings={'timestamp': {'format': '%d.%m.%Y %H:%M'}})
    page = tmp_path / 'page.md'
    page.write_text(
        '---\nname: page\ndesc: A page.\n---\n\n# page\n\nBody.\n',
        encoding='utf-8',
    )
    wiki.update()
    assert not any('Unparseable' in issue for issue in wiki.lint())
    page.write_text(
        re.sub(
            pattern=r'^created:.*$',
            repl='created: 2026-01-01T00:00:00Z',
            string=page.read_text(encoding='utf-8'),
            count=1,
            flags=re.MULTILINE,
        ),
        encoding='utf-8',
    )
    assert any('Unparseable created' in issue for issue in wiki.lint())


@pytest.mark.parametrize(
    argnames=('field', 'line', 'reason', 'advice'),
    argvalues=[
        (
            'desc: Independent lane for L1: the harness.',
            3,
            'mapping values',
            'quote or rewrite the value',
        ),
        (
            'title: Theorem for X: HR, WR',
            3,
            'mapping values',
            'quote or rewrite the value',
        ),
        ('desc: Ends with a colon:', 3, 'mapping values', 'quote or rewrite the value'),
        ('desc: One.\ndesc: Two.', 4, 'duplicate key', 'the wiki reads the first'),
        (
            'desc: Position and\x0c routes.',
            3,
            'characters are not allowed',
            'quote or rewrite the value',
        ),
        (
            'desc: caf\xe9 caf\xe9 caf\xe9.\ncategory: bad\x0c',
            4,
            'characters are not allowed',
            'quote or rewrite the value',
        ),
        ('? [a, b]\n: x', 3, 'not a scalar', 'line grammar alone'),
        ('desc: "a\x85b"\ndesc: Two.', 4, 'duplicate key', 'the wiki reads the first'),
        (
            'desc: "unterminated\ntags: []',
            3,
            'end of stream',
            'quote or rewrite the value',
        ),
        (
            'stray\ndesc: A.',
            3,
            "could not find expected ':'",
            'quote or rewrite the value',
        ),
    ],
    ids=[
        'colon-space-desc',
        'colon-space-title',
        'trailing-colon',
        'duplicate-key',
        'control-char',
        'control-char-after-non-ascii',
        'complex-key',
        'nel-before-the-error',
        'unterminated-quote',
        'stray-line',
    ],
)
@pytest.mark.usefixtures('_vary_loader')
def test_lint_flags_frontmatter_a_strict_yaml_reader_rejects(
    tmp_path: pathlib.Path,
    field: str,
    line: int,
    reason: str,
    advice: str,
) -> None:
    """A block the wiki reads leniently but a strict YAML reader refuses is an issue.

    The wiki reads an unquoted ``: `` value as the authored text through
    the line grammar, but a strict reader (Obsidian's) drops the whole
    block; lint names the file and line as a hard issue typed for
    ``--json`` with the remedy for its cause, under the C loader and the
    pure-Python loader alike, and update leaves the authored bytes alone
    (the fix is the author's). The line is the offending one whatever
    precedes it: a forbidden character after accented text (the C loader
    counts stream positions in bytes), an error after a NEL the parser
    counts as a line break, an unterminated quote or a stray line the
    parser only notices further on.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    page = tmp_path / 'core' / 'design.md'
    desc = '' if field.startswith('desc:') else 'desc: A page.\n'
    page.write_text(
        f'---\nname: core/design\n{field}\n{desc}tags: []\nsources: []\n'
        'created: 2026-01-01T00:00:00Z\nupdated: 2026-01-01T00:00:00Z\n---\n'
        '\n# design\n\nBody.\n',
        encoding='utf-8',
    )

    # the finding names the line and the remedy; the block is otherwise read leniently
    issues = [issue for issue in wiki.lint() if issue.kind == 'invalid_yaml']
    assert [issue.fields['path'] for issue in issues] == ['core/design.md']
    assert issues[0].fields['line'] == line
    assert reason in issues[0].fields['reason']
    assert f'core/design.md: Invalid YAML frontmatter (line {line})' in issues[0]
    assert advice in issues[0]
    # update never rewrites an authored value: the field lines stay as typed
    wiki.update()
    assert field in page.read_text(encoding='utf-8').split('---\n')[1]
    assert any(issue.kind == 'invalid_yaml' for issue in Wiki(tmp_path).lint())


@pytest.mark.parametrize(
    argnames=('stamp', 'value'),
    argvalues=[
        ('created:\n- 2025-01-01', '- 2025-01-01'),
        ('created: [2025-01-01]', '[2025-01-01]'),
        ('created: {a: b}', '{a: b}'),
        ('created: []', '[]'),
    ],
    ids=['block-sequence', 'flow-sequence', 'flow-mapping', 'empty-sequence'],
)
def test_lint_flags_a_stamp_that_is_a_sequence(
    tmp_path: pathlib.Path,
    stamp: str,
    value: str,
) -> None:
    """A ``created:`` over a sequence or mapping is an unparseable stamp, not an absent one.

    The reader resolves no collection, so the key would otherwise read as
    missing and neither update (which fills only a valueless key) nor
    lint would say a word about a stamp no format parses -- whether the
    collection sits under the key or on its line.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    page = tmp_path / 'core' / 'design.md'
    page.write_text(
        f'---\nname: core/design\ndesc: A page.\n{stamp}\n---\n\n# design\n\nBody.\n',
        encoding='utf-8',
    )
    wiki.update()

    # the collection is named as the unparseable stamp it is
    issues = [issue for issue in wiki.lint() if issue.kind == 'unparseable_stamp']
    assert [issue.fields['field'] for issue in issues] == ['created']
    assert issues[0].fields['value'] == value


def test_lint_flags_a_block_whose_repair_would_break_it(tmp_path: pathlib.Path) -> None:
    """A block update refuses to repair is a malformed-frontmatter issue, not a silent stall.

    Refreshing an anchored ``name:`` would strand the alias of it; update
    keeps the page as written with a notice on every run, and lint names
    the same refusal so the stale name is never a surprise.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    page = tmp_path / 'core' / 'design.md'
    authored = (
        '---\nname: &n wrong\ndesc: A page.\ntitle: *n\n---\n\n# design\n\nBody.\n'
    )
    page.write_text(authored, encoding='utf-8')

    # update keeps the page and lint names the refusal
    wiki.update()
    assert page.read_text(encoding='utf-8') == authored
    issues = [
        issue
        for issue in Wiki(tmp_path).lint()
        if issue.kind == 'malformed_frontmatter'
    ]
    assert [str(issue) for issue in issues] == [
        'core/design.md: Malformed frontmatter (its repair would break the YAML)'
    ]


def test_lint_flags_an_unaddressable_block_as_malformed(tmp_path: pathlib.Path) -> None:
    """A mapping with no column-0 key lines is malformed frontmatter to lint, page or index.

    A collection-valued stamp inside such a mapping is the same finding,
    never a crash of the whole run.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    (tmp_path / 'core' / 'design.md').write_text(
        '---\n{name: x, desc: Flow.}\n---\n\n# design\n\nBody.\n', encoding='utf-8'
    )
    (tmp_path / 'core' / '_index.md').write_text(
        '---\n  created: []\n  name: core\n  desc: Indented.\n---\n\n# core\n\n***\n\nProse.\n',
        encoding='utf-8',
    )

    # both files carry the malformed-frontmatter issue with the reason
    issues = [issue for issue in wiki.lint() if issue.kind == 'malformed_frontmatter']
    assert sorted(issue.fields['path'] for issue in issues) == [
        'core/_index.md',
        'core/design.md',
    ]
    assert all('keys are not column-0 key: value lines' in issue for issue in issues)


@pytest.mark.usefixtures('_vary_loader')
def test_lint_reports_deep_nesting_instead_of_crashing(tmp_path: pathlib.Path) -> None:
    """Nesting past the composer's bound is an issue, never an abort or a crash.

    The pure-Python loader recurses once per nesting level and raises
    ``RecursionError`` -- not a YAML error -- a few hundred levels down,
    and the C loader recurses unchecked into the C stack; the bound
    refuses the block before either, so lint reports it the same way
    under both loaders and the sweep goes on.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    page = tmp_path / 'core' / 'design.md'
    nested = '[' * 2000 + ']' * 2000
    page.write_text(
        f'---\nname: core/design\ndesc: A page.\nextra: {nested}\n---\n\n'
        '# design\n\nBody.\n',
        encoding='utf-8',
    )

    # the sweep completes with the block named as invalid, and update converges
    issues = [issue for issue in wiki.lint() if issue.kind == 'invalid_yaml']
    assert [issue.fields['path'] for issue in issues] == ['core/design.md']
    reason = f'collections nested deeper than {format._MAX_NESTING} levels'
    assert issues[0].fields['reason'] == reason
    assert issues[0].fields['line'] == 4
    wiki.update()
    assert Wiki(tmp_path).update() == []


@pytest.mark.usefixtures('_vary_loader')
@pytest.mark.parametrize(
    argnames='escape',
    argvalues=['\\U00110000', '\\uD800'],
    ids=['past-unicode', 'surrogate'],
)
def test_lint_reports_an_escape_naming_no_character(
    tmp_path: pathlib.Path, escape: str
) -> None:
    """A double-quoted escape naming no character is an issue, never a crash, under either loader.

    The C loader rejects it as an invalid escape; the pure loader decodes
    it into text no writer can emit (or raises from ``chr``), which the
    reader turns into the same finding, so ``update`` completes and the
    value reads verbatim through the line grammar.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    page = tmp_path / 'core' / 'design.md'
    page.write_text(
        f'---\nname: core/design\ndesc: "a{escape}b."\n---\n\n# design\n\nBody.\n',
        encoding='utf-8',
    )

    # the sweep completes, the block is named, and the parent row reads the escape as text
    wiki.update()
    issues = [issue for issue in Wiki(tmp_path).lint() if issue.kind == 'invalid_yaml']
    assert [issue.fields['path'] for issue in issues] == ['core/design.md']
    index = (tmp_path / 'core' / '_index.md').read_text(encoding='utf-8')
    assert f'[[core/design|design]]: a{escape}b.' in index
    assert Wiki(tmp_path).update() == []


def test_lint_invalid_yaml_yields_to_merge_states(tmp_path: pathlib.Path) -> None:
    """Merge debris keeps its own finding: markers win, the hint stays valid YAML.

    Conflict markers inside a block make it unparseable, but the marker
    check runs first and suppresses the per-file checks, so the file
    reports only ``conflict_markers``; the driver's resolution hint is
    valid YAML for every parser, so it reports only ``merge_hint``.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    page = tmp_path / 'core' / 'design.md'
    index = tmp_path / 'core' / '_index.md'
    lines = page.read_text(encoding='utf-8').split('\n')
    lines[2:3] = [
        '<<<<<<< ours',
        lines[2],
        '=======',
        'desc: Theirs.',
        '>>>>>>> theirs',
    ]
    page.write_text('\n'.join(lines), encoding='utf-8')
    hint = (
        '<!-- index *** separator missing on one side: likely formatter'
        ' damage; restore the *** line (wiki update repairs it), redo the'
        ' merge, and delete this line when resolving -->'
    )
    lines = index.read_text(encoding='utf-8').split('\n')
    lines.insert(2, hint)
    index.write_text('\n'.join(lines), encoding='utf-8')

    # each file carries exactly its merge-state finding
    kinds: dict[str, set[str]] = {}
    for issue in wiki.lint():
        kinds.setdefault(issue.fields['path'], set()).add(issue.kind)
    assert kinds['core/design.md'] == {'conflict_markers'}
    assert kinds['core/_index.md'] == {'merge_hint'}


def test_lint_names_a_comment_truncated_desc(tmp_path: pathlib.Path) -> None:
    """A desc cut short by a ``' #'`` comment fails the period check with the cause named.

    A YAML reader stops a plain value at ``' #'``, so the parent row and
    the period check see only the text before it; the message says so
    instead of leaving the author to guess, while a comment after a
    complete sentence draws nothing.
    """
    pages = ['cut', 'bare', 'commented', 'header', 'fallback', 'quoted', 'under']
    wiki = _make_wiki(tmp_path, folders={'core': pages})
    (tmp_path / 'core' / 'quoted.md').write_text(
        "---\nname: core/quoted\ndesc: 'Notes on room #12'\n---\n\n# quoted\n\nBody.\n",
        encoding='utf-8',
    )
    (tmp_path / 'core' / 'under.md').write_text(
        '---\nname: core/under\ndesc: Notes\n  # a comment line under the value\n---\n\n'
        '# under\n\nBody.\n',
        encoding='utf-8',
    )
    (tmp_path / 'core' / 'cut.md').write_text(
        '---\nname: core/cut\ndesc: Notes on room #12 and the key.\n---\n\n'
        '# cut\n\nBody.\n',
        encoding='utf-8',
    )
    (tmp_path / 'core' / 'bare.md').write_text(
        '---\nname: core/bare\ndesc:\n  Notes on room #12 and the key.\n---\n\n'
        '# bare\n\nBody.\n',
        encoding='utf-8',
    )
    (tmp_path / 'core' / 'commented.md').write_text(
        '---\nname: core/commented\ndesc: Short summary.  # TODO expand\n---\n\n'
        '# commented\n\nBody.\n',
        encoding='utf-8',
    )
    (tmp_path / 'core' / 'header.md').write_text(
        '---\nname: core/header\ndesc: | # note\n  No period here\n---\n\n'
        '# header\n\nBody.\n',
        encoding='utf-8',
    )
    (tmp_path / 'core' / 'fallback.md').write_text(
        '---\nname: core/fallback\ndesc: Notes on room #12 and the key.\nzz: A: b\n---\n\n'
        '# fallback\n\nBody.\n',
        encoding='utf-8',
    )
    wiki.update()

    # a truncated desc is named with its cause, on the key line or a
    # continuation line; the intentional comment is silent; a block header's
    # comment truncates nothing; a block the parser rejects reads through the
    # line grammar, where the hint would explain nothing
    issues = {
        issue.fields['path']: issue
        for issue in wiki.lint()
        if issue.kind == 'missing_period'
    }
    hint = "(the text after ' #' is a YAML comment"
    assert sorted(issues) == [
        'core/bare.md',
        'core/cut.md',
        'core/fallback.md',
        'core/header.md',
        'core/quoted.md',
        'core/under.md',
    ]
    assert hint in issues['core/cut.md']
    assert hint in issues['core/bare.md']
    # a quoted value, a comment line under the value, a block header's
    # comment, and a block the parser rejects lost nothing to a comment
    for page in ('header', 'fallback', 'quoted', 'under'):
        assert issues[f'core/{page}.md'] == f'core/{page}.md: Missing period in desc'
    index = (tmp_path / 'core' / '_index.md').read_text(encoding='utf-8')
    assert '[[core/cut|cut]]: Notes on room\n' in index
    assert '[[core/commented|commented]]: Short summary.\n' in index


# ------ wrap mangles


@pytest.mark.parametrize(
    argnames=('body', 'flagged'),
    argvalues=[
        ('supports twenty-\nclass workloads.', True),
        ('supports twenty-\nand thirty-class workloads.', False),
        ('supports neither twenty-\nnor thirty-class workloads.', False),
    ],
    ids=['dangle', 'suspended-and', 'suspended-nor'],
)
def test_lint_hyphen_dangle(
    tmp_path: pathlib.Path,
    body: str,
    flagged: bool,
) -> None:
    """A line break splitting a hyphenated word is a hard issue.

    Every folded read joins the pair with a space, mangling the word;
    only the suspended-hyphen idiom (a next line opening ``and ``/
    ``or ``/``nor ``) legitimately ends a line on a hyphen.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    page = tmp_path / 'core' / 'design.md'
    page.write_text(
        page.read_text(encoding='utf-8').replace('Content for design.', body),
        encoding='utf-8',
    )
    issues = wiki.lint()
    dangles = [issue for issue in issues if 'Hyphen dangle' in issue]
    assert bool(dangles) == flagged


@pytest.mark.parametrize('surface', ['desc', 'index-row', 'prose'])
def test_lint_wrapped_list_marker(
    tmp_path: pathlib.Path,
    surface: str,
) -> None:
    """A list marker continuing a sentence is flagged on every surface.

    A wrapped continuation opening with ``+ ``/``- ``/``* `` renders as
    a phantom list item -- in a page desc's raw block lines, an index
    link row, and page prose alike.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    index = tmp_path / 'core' / '_index.md'
    page = tmp_path / 'core' / 'design.md'
    if surface == 'desc':
        target = page
        page.write_text(
            page.read_text(encoding='utf-8').replace(
                'desc: The design page.',
                'desc: >\n  handles cases\n  + streaming input.',
            ),
            encoding='utf-8',
        )
    elif surface == 'index-row':
        target = index
        index.write_text(
            index.read_text(encoding='utf-8').replace(
                '[[core/design|design]]: The design page.',
                '[[core/design|design]]: handles cases\n+ streaming input.',
            ),
            encoding='utf-8',
        )
    else:
        target = page
        page.write_text(
            page.read_text(encoding='utf-8').replace(
                'Content for design.',
                'handles cases\n+ streaming input.',
            ),
            encoding='utf-8',
        )
    issues = wiki.lint()
    flagged = [issue for issue in issues if 'Wrapped list marker' in issue]
    assert flagged
    assert all(str(target.relative_to(tmp_path)) in issue for issue in flagged)


def test_lint_blank_led_list_is_clean(tmp_path: pathlib.Path) -> None:
    """The house list shapes are never flagged as wrap mangles.

    A list opening after a blank line, a bullet following its sibling's
    wrapped continuation line, and a nested sublist opening after its
    parent's continuation are all healthy -- only a marker continuing a
    sentence or interrupting a paragraph is a mangle.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    body = (
        'Intro paragraph.\n\n'
        '- item one\n- item two\n\n'
        '1. step\n   - detail\n\n'
        '- an item that wraps\n  onto a continuation line\n- next item\n'
        '- another wrapping item\n  with its continuation\n  - nested detail\n'
    )
    page = tmp_path / 'core' / 'design.md'
    page.write_text(
        page.read_text(encoding='utf-8').replace('Content for design.\n', body),
        encoding='utf-8',
    )
    assert wiki.lint() == []


def test_lint_code_span_lead_is_not_marker(tmp_path: pathlib.Path) -> None:
    """A line opening with a code span then ``+`` is prose, not a bullet.

    Masking removes inline-span bytes, which can leave a marker-shaped
    remainder on a wrapped paragraph line; list rendering keys on the
    raw leading bytes, so the line is never a phantom list item.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    body = 'Reject the\n`--preview` + `--commit` combination.'
    page = tmp_path / 'core' / 'design.md'
    page.write_text(
        page.read_text(encoding='utf-8').replace('Content for design.', body),
        encoding='utf-8',
    )
    assert wiki.lint() == []


def test_lint_code_span_continuation_keeps_list_open(
    tmp_path: pathlib.Path,
) -> None:
    """A continuation line that is only a code span never closes its list.

    Masking a bare backticked path leaves a blank line, but the list is
    still open on the rendered surface: with further continuation prose
    between it and the next legal bullet, that bullet must not flag as a
    wrapped marker.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    body = (
        '- evidence lives at\n'
        '  `spine/L001/evidence/main.py`\n'
        '  and re-runs on demand\n'
        '- next item\n'
    )
    page = tmp_path / 'core' / 'design.md'
    page.write_text(
        page.read_text(encoding='utf-8').replace('Content for design.\n', body),
        encoding='utf-8',
    )
    assert wiki.lint() == []


# ------ masked regions and suppression


def test_lint_ignores_code_blocks(tmp_path: pathlib.Path) -> None:
    """Wikilinks inside code blocks are never noted as stale."""
    wiki = Wiki(tmp_path)
    wiki.init()
    (tmp_path / 'page.md').write_text(
        '---\nname: page\ndesc: A page.\n---\n\n# page\n\n'
        '```\n[[nonexistent]]\n```\n\n`[[also_nonexistent]]`\n',
        encoding='utf-8',
    )
    wiki.update()
    notices = _capture_notices(wiki)
    issues = wiki.lint()
    lines = issues + [event.description for event in notices]
    stale = [line for line in lines if 'nonexistent' in line.lower()]
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
    notices = _capture_notices(wiki)
    assert wiki.lint() == []
    assert not any('Stale link' in event.description for event in notices)


@pytest.mark.parametrize(
    argnames=('body', 'flagged'),
    argvalues=[
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

    Conflict markers, formatter-escaped wikilinks, and stale and
    directory links are attributable to lines, so a region silences them
    there; file-level checks ignore regions entirely.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    page = tmp_path / 'core' / 'design.md'
    body = (
        '<!-- start: no-lint -->\n'
        '<<<<<<< HEAD\n'
        'sample \\[[escaped]] and [[missing_inside]] links\n'
        'a sample [[core]] directory link\n'
        '>>>>>>> branch\n'
        '<!-- end: no-lint -->\n'
        '\n'
        'A real [[missing_outside]] link.\n'
    )
    page.write_text(
        page.read_text(encoding='utf-8').replace('Content for design.\n', body),
        encoding='utf-8',
    )

    # inside the region nothing positional fires -- issue or note --
    # while the stale link outside still draws its note
    notices = _capture_notices(wiki)
    issues = wiki.lint()
    notes = '\n'.join(event.description for event in notices)
    assert not any('Merge conflict markers' in issue for issue in issues)
    assert not any('Escaped wikilinks' in issue for issue in issues)
    assert not any('missing_inside' in issue for issue in issues)
    assert not any('targets a folder' in issue for issue in issues)
    assert 'missing_inside' not in notes
    assert 'Stale link [[missing_outside]]' in notes

    # file-level checks ignore regions: a drifted H1 still requires update
    drifted = page.read_text(encoding='utf-8').replace(
        '# core/design',
        '<!-- start: no-lint -->\n# Wrong Title\n<!-- end: no-lint -->',
    )
    page.write_text(drifted, encoding='utf-8')
    issues = wiki.lint()
    assert any('Requires update' in issue for issue in issues)


@pytest.mark.parametrize(
    argnames=('body', 'needle'),
    argvalues=[
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


# ------ clean runs and soft notes


def test_lint_clean(tmp_path: pathlib.Path) -> None:
    """A properly structured wiki produces no lint issues."""
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    issues = wiki.lint()
    assert issues == []


def test_lint_survives_page_deleted_mid_walk(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A page deleted after the walk lists it never crashes lint.

    Lint reads each walked page directly, so a page vanishing between
    its folder's enumeration and its read (a concurrent delete) must
    lint as absent from the walk, with the next run flagging the stale
    index row it leaves behind.
    """
    wiki = _make_wiki(tmp_path, folders={'notes': ['alpha', 'doomed']})
    alpha = tmp_path / 'notes' / 'alpha.md'
    doomed = tmp_path / 'notes' / 'doomed.md'
    real = Wiki._has_crlf

    def racy(self: Wiki, path: pathlib.Path) -> bool:
        """Delete the doomed page while its already-walked sibling lints."""
        if (path == alpha) and doomed.exists():
            doomed.unlink()
        return real(self, path)

    # the mid-walk deletion is handled, not crashed on
    monkeypatch.setattr(Wiki, '_has_crlf', racy)
    assert wiki.lint() == []

    # the next run flags the stale index row the vanished page left
    issues = wiki.lint()
    assert any(issue.kind == 'broken_link' for issue in issues)


def test_lint_survives_page_deleted_before_crlf_probe(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A page vanishing between its read and its CRLF probe never crashes.

    Lint reads a page's text, masks it, then probes the file's bytes for
    CRLF drift; a delete landing inside that window must leave the probe
    with nothing to normalize rather than raising ``FileNotFoundError``.
    """
    wiki = _make_wiki(tmp_path, folders={'notes': ['doomed']})
    doomed = tmp_path / 'notes' / 'doomed.md'
    doomed_text = doomed.read_text(encoding='utf-8')
    real = markdown.mask_code

    def racy(text: str) -> str:
        """Delete the doomed page once its lint read is already in hand."""
        if (text == doomed_text) and doomed.exists():
            doomed.unlink()
        return real(text)

    # the pre-probe deletion is handled, not crashed on
    monkeypatch.setattr(markdown, 'mask_code', racy)
    assert wiki.lint() == []

    # the next run flags the stale index row the vanished page left
    issues = wiki.lint()
    assert any(issue.kind == 'broken_link' for issue in issues)


def test_lint_survives_folder_deleted_mid_walk(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A folder deleted after the walk lists it never crashes lint.

    Lint enumerates every walked folder's children for the shadow
    check, so a folder vanishing between the walk and its own listing
    (a concurrent delete) must lint as absent from the walk, with the
    same run flagging the stale index row it leaves behind.
    """
    wiki = _make_wiki(tmp_path, folders={'doomed': ['gone'], 'notes': ['alpha']})
    doomed = tmp_path / 'doomed'
    real = Wiki._has_crlf

    def racy(self: Wiki, path: pathlib.Path) -> bool:
        """Delete the doomed folder while the already-walked root lints."""
        if doomed.exists():
            shutil.rmtree(doomed)
        return real(self, path)

    # the mid-walk deletion is handled, not crashed on: the vanished
    # folder lints as absent, leaving only its stale parent row flagged
    monkeypatch.setattr(Wiki, '_has_crlf', racy)
    issues = wiki.lint()
    assert {issue.kind for issue in issues} == {'broken_link'}
    assert all('doomed' in issue for issue in issues)


@pytest.mark.parametrize(
    argnames=('vanish_read', 'expected_kinds'),
    argvalues=[
        # the plan's baseline read is first; lint's own index read is second
        (2, {'missing_index'}),
        # the marker probe re-reads after lint's read: the check ran clean
        (3, set()),
    ],
    ids=['index-check-read', 'marker-probe-read'],
)
def test_lint_survives_index_deleted_mid_check(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    vanish_read: int,
    expected_kinds: set[str],
) -> None:
    """An index vanishing between probe and re-read never crashes lint.

    Lint re-reads each index after probing it -- once for the index
    check and once for the missing-delimiter probe -- so a delete
    landing before either re-read (a concurrent delete) must classify
    the index as missing (or leave the probe silent) rather than raise.
    """
    wiki = _make_wiki(tmp_path, folders={'notes': ['alpha']})
    index = tmp_path / 'notes' / '_index.md'
    real = Wiki._read_text
    reads: list[pathlib.Path] = []

    def racy(self: Wiki, path: pathlib.Path) -> str:
        """Delete the index just as the numbered re-read begins."""
        if path == index:
            reads.append(path)
            if len(reads) == vanish_read:
                index.unlink()
        return real(self, path)

    # the mid-check deletion is handled, not crashed on
    monkeypatch.setattr(Wiki, '_read_text', racy)
    issues = wiki.lint()
    assert {issue.kind for issue in issues} == expected_kinds


def test_quoted_placeholder_desc_is_soft(
    tmp_path: pathlib.Path,
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
    notices = _capture_notices(wiki)
    issues = wiki.lint()
    err = '\n'.join(event.description for event in notices)
    assert 'Needs desc' in err
    assert not any('Missing period' in issue for issue in issues)
    core_index = (tmp_path / 'core' / '_index.md').read_text(encoding='utf-8')
    assert "'...'" not in core_index


def test_long_desc_is_note_only(tmp_path: pathlib.Path) -> None:
    """An oversized desc draws a soft note, never an issue.

    Every map row and parent index link reproduces the desc, so lint
    nudges toward concision -- but length is author judgment, not
    structure, and must not fail the wiki.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    long_desc = ('A design note that explains far too much detail. ' * 11).strip()
    page = tmp_path / 'core' / 'design.md'
    page.write_text(
        page.read_text(encoding='utf-8').replace(
            'desc: The design page.',
            f'desc: {long_desc}',
        ),
        encoding='utf-8',
    )
    wiki.update()

    # the note names the page; the wiki stays clean
    notices = _capture_notices(wiki)
    issues = wiki.lint()
    err = '\n'.join(event.description for event in notices)
    assert 'keep descs under' in err
    assert 'design.md' in err
    assert issues == []


# ------ body links and structure


@page_index
@pytest.mark.parametrize('anchor', ['', '#context'], ids=['bare', 'anchored'])
@pytest.mark.parametrize(
    argnames=('link', 'fix'),
    argvalues=[
        # the root page, written as if relative to the page's folder
        ('../overview', 'overview'),
        # a sibling page through './'
        ('./sibling', 'notes/sibling'),
        # an indexed folder: the fix is its index page
        ('../core', 'core/_index'),
        # an excluded folder keeps its bare form
        ('../vendor', 'vendor'),
        # a raw file at the root
        ('../Makefile', 'Makefile'),
        # the root itself
        ('..', '_index'),
        # an interior '..' segment reads the same way
        ('sibling/../sibling', 'notes/sibling'),
        # nothing exists there: the issue stands without a fix
        ('./gone', None),
    ],
    ids=[
        'page',
        'dot-sibling',
        'indexed-folder',
        'excluded-folder',
        'raw-file',
        'root',
        'interior',
        'missing',
    ],
)
def test_lint_relative_prefix_inside_wiki_is_issue(
    tmp_path: pathlib.Path,
    kind: str,
    anchor: str,
    link: str,
    fix: Optional[str],
) -> None:
    """A ``./`` or ``../`` link that lands inside the wiki is a hard issue.

    A prefixed target is read from the page's folder, as Obsidian and
    markdown read it, and means "outside the wiki"; one that resolves
    inside is written wrong, so lint fails it and names the prefix-free
    form -- a page by stem, an indexed folder's ``_index`` page, an
    excluded folder's bare form, a raw file's path -- with the anchor and
    alias riding along, and no fix when nothing exists there. The link is
    never also noted as stale.
    """
    _make_wiki(
        tmp_path,
        folders={'notes': ['meeting', 'sibling'], 'core': ['design']},
    )
    (tmp_path / 'overview.md').write_text(
        '---\nname: overview\ndesc: An overview.\n---\n\n# overview\n\nText.\n',
        encoding='utf-8',
    )
    (tmp_path / 'Makefile').write_text('all:\n', encoding='utf-8')
    # a folder carrying an index on disk that the walk will not enter
    vendor = tmp_path / 'vendor'
    vendor.mkdir()
    (vendor / '_index.md').write_text(
        '---\nname: vendored\ndesc: A vendored index.\n---\n\n# vendored\n\n***\n',
        encoding='utf-8',
    )
    _set_exclude_patterns(tmp_path, ['vendor'])
    name = 'meeting.md' if kind == 'page' else '_index.md'
    marker = 'Content for meeting.' if kind == 'page' else 'Overview of notes.'
    page = tmp_path / 'notes' / name
    body = f'See [[{link}{anchor}|Here]] for context.'
    text = page.read_text(encoding='utf-8').replace(marker, body)
    page.write_text(text, encoding='utf-8')
    Wiki(tmp_path).update()

    # the issue names the prefix-free spelling; the link never also notes
    wiki = Wiki(tmp_path)
    notices = _capture_notices(wiki)
    flagged = [issue for issue in wiki.lint() if 'points inside the wiki' in issue]
    tail = '' if fix is None else f' (use [[{fix}{anchor}|Here]])'
    assert flagged == [
        f'notes/{name}: Link [[{link}{anchor}|Here]] points inside the wiki'
        f" through './' or '../'{tail}"
    ]
    assert flagged[0].kind == 'relative_link'
    assert not any('Stale link' in event.description for event in notices)


def test_lint_relative_root_link_names_the_index_page(tmp_path: pathlib.Path) -> None:
    """A prefixed link to the wiki root itself is steered to its index page.

    ``[[.]]`` from a root page and ``[[..]]`` from a nested one land on
    the root, whose fix is ``_index`` -- never the stem of a file beside
    the wiki that shares the root folder's name.
    """
    root = tmp_path / 'wiki'
    _make_wiki(root, folders={'notes': ['meeting']})
    (tmp_path / 'wiki.md').write_text('x\n', encoding='utf-8')
    for page, link in (('_index.md', '.'), ('notes/meeting.md', '..')):
        path = root / page
        marker = 'Root overview.' if page == '_index.md' else 'Content for meeting.'
        text = path.read_text(encoding='utf-8').replace(marker, f'See [[{link}]] now.')
        path.write_text(text, encoding='utf-8')

    issues = Wiki(root).lint()
    assert sorted(issues) == [
        "_index.md: Link [[.]] points inside the wiki through './' or '../'"
        ' (use [[_index]])',
        "notes/meeting.md: Link [[..]] points inside the wiki through './' or '../'"
        ' (use [[_index]])',
    ]


@page_index
@pytest.mark.parametrize('anchor', ['', '#context'], ids=['bare', 'anchored'])
def test_lint_directory_link_is_issue_naming_index_form(
    tmp_path: pathlib.Path,
    anchor: str,
    kind: str,
) -> None:
    """A body link naming a folder is a hard issue naming the ``/_index`` fix.

    The folder is not a page -- following the link resolves to nothing
    -- so lint fails it and names the exact repair, with an anchor
    suffix riding along like the stale-note suggestion. Repeats of a
    target collapse to one line: the scan is content-local, so there is
    no file line number to tell the occurrences apart.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design'], 'notes': ['meeting']})
    name = 'meeting.md' if kind == 'page' else '_index.md'
    path = tmp_path / 'notes' / name
    path.write_text(
        path.read_text(encoding='utf-8')
        + f'\nSee [[core{anchor}]] for context.\nAnd [[core{anchor}]] again.\n',
        encoding='utf-8',
    )
    wiki.update()
    # the directory link is an issue (never a note), naming the fix once
    notices = _capture_notices(wiki)
    issues = wiki.lint()
    flagged = [issue for issue in issues if f'Link [[core{anchor}]]' in issue]
    assert flagged == [
        f'notes/{name}: Link [[core{anchor}]] targets a folder,'
        f' not a page (use [[core/_index{anchor}]])'
    ]
    assert not any('Stale link' in event.description for event in notices)


@pytest.mark.parametrize(
    argnames=('body', 'flagged'),
    argvalues=[
        ('para:\n\n    see [[core]]\n', False),
        ('para:\n\n\tsee [[core]]\n', False),
        ('<!-- todo: link [[core]] later -->\n', False),
        ('```\nsee [[core]]\n```\n', False),
        ('- outer\n    - see [[core]]\n', True),
        ('- outer\n\n    see [[core]]\n', True),
        ('see [[core]] here\n', True),
    ],
    ids=[
        'indented',
        'tabbed',
        'comment',
        'fenced',
        'nested-list',
        'list-body',
        'prose',
    ],
)
def test_lint_link_rules_spare_samples_not_prose(
    tmp_path: pathlib.Path,
    body: str,
    flagged: bool,
) -> None:
    """The link rules read prose, skipping code samples and comments.

    A wikilink in a code sample or an HTML comment is text about a link,
    not a link to follow, so neither the directory-link issue nor the
    stale note fires. Indentation alone cannot decide that: a bullet
    indents its continuation and its nested items four spaces too, so
    those stay prose and stay checked.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design'], 'notes': ['meeting']})
    meeting = tmp_path / 'notes' / 'meeting.md'
    meeting.write_text(
        meeting.read_text(encoding='utf-8') + f'\n{body}', encoding='utf-8'
    )
    wiki.update()
    issues = [issue for issue in wiki.lint() if 'Link [[core]]' in issue]
    assert bool(issues) is flagged


@pytest.mark.parametrize(
    argnames=('body', 'link', 'fix'),
    argvalues=[
        (
            'See [[core#context|the core docs]] for background.',
            '[[core#context|the core docs]]',
            '[[core/_index#context|the core docs]]',
        ),
        (
            '| docs |\n| --- |\n| [[core\\|the core docs]] |',
            '[[core\\|the core docs]]',
            '[[core/_index\\|the core docs]]',
        ),
    ],
    ids=['prose', 'table'],
)
def test_lint_directory_link_keeps_display_text(
    tmp_path: pathlib.Path,
    body: str,
    link: str,
    fix: str,
) -> None:
    """A directory link's display text rides into the suggested fix.

    The suggestion is meant to be pasted over the offending link, so
    dropping the label would silently change what the page renders --
    and a table cell's escaped pipe stays escaped, since an unescaped
    pipe in the pasted fix would split the cell.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design'], 'notes': ['meeting']})
    meeting = tmp_path / 'notes' / 'meeting.md'
    meeting.write_text(
        meeting.read_text(encoding='utf-8') + f'\n{body}\n', encoding='utf-8'
    )
    wiki.update()
    issues = [issue for issue in wiki.lint() if 'targets a folder' in issue]
    assert issues == [
        f'notes/meeting.md: Link {link} targets a folder, not a page (use {fix})'
    ]


def test_lint_index_form_link_is_clean(tmp_path: pathlib.Path) -> None:
    """A ``/_index`` body link is clean; a dangling target notes once.

    The explicit ``[[core/_index]]`` form resolves to the folder's index
    page, so it draws neither issue nor note, while a target resolving
    to no page and no folder keeps its stale note -- one note however
    often the prose repeats it, since the notes are indistinguishable.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design'], 'notes': ['meeting']})
    meeting = tmp_path / 'notes' / 'meeting.md'
    meeting.write_text(
        meeting.read_text(encoding='utf-8').replace(
            'Content for meeting.',
            'See [[core/_index]] and [[missing]], then [[missing]] again.',
        ),
        encoding='utf-8',
    )
    wiki.update()
    # no issues; the dangling target draws one stale note for both mentions
    notices = _capture_notices(wiki)
    assert wiki.lint() == []
    stale = [
        event.description for event in notices if 'Stale link' in event.description
    ]
    assert stale == ['notes/meeting.md: Stale link [[missing]]']


def test_index_broken_link_is_issue_but_body_link_is_note(
    tmp_path: pathlib.Path,
) -> None:
    """Index-block broken links fail hard; an identical body link is a note.

    User prose references pages that come and go, so a stale body-level
    wikilink draws a soft note (run over run -- update never silences
    it) without failing the wiki, while the generated index link block
    keeps its broken-link hard issue for the same missing target.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design', 'ghost']})
    # reference the page from the index body, then delete it: the
    # generated row and the body link now dangle identically
    index = tmp_path / 'core' / '_index.md'
    index.write_text(
        index.read_text(encoding='utf-8') + '\nSee [[core/ghost]] for more.\n',
        encoding='utf-8',
    )
    (tmp_path / 'core' / 'ghost.md').unlink()

    # the generated row fails hard; the body link is a note only
    notices = _capture_notices(wiki)
    issues = wiki.lint()
    notes = '\n'.join(event.description for event in notices)
    assert any('Broken link [[core/ghost|ghost]]' in issue for issue in issues)
    assert not any('Stale link' in issue for issue in issues)
    assert 'core/_index.md: Stale link [[core/ghost]]' in notes

    # update prunes the row (the hard issue clears); the body note
    # persists, since update never edits prose
    wiki.update()
    notices.clear()
    issues = wiki.lint()
    assert not any('Broken link' in issue for issue in issues)
    notes = '\n'.join(event.description for event in notices)
    assert 'core/_index.md: Stale link [[core/ghost]]' in notes


def test_lint_names_excluded_link_target(tmp_path: pathlib.Path) -> None:
    """A preserved row into an excluded target is a hard issue naming the pattern.

    The row's target is still on disk, so a generic broken-link report
    would send the user hunting for a deleted file; the issue names the
    exclusion and the ``exclude.patterns`` pattern behind it instead.
    """
    _make_wiki(tmp_path, folders={'data': ['child', 'report']})
    # index the page first, then exclude it, preserving the stale row
    _set_exclude_patterns(tmp_path, ['data/report.md'])

    issues = Wiki(tmp_path).lint()
    joined = '\n'.join(issues)
    assert 'targets an excluded path; excluded paths are not indexed' in joined
    assert "exclude.patterns: 'data/report.md'" in joined
    assert 'Broken link' not in joined


def test_lint_silent_inside_excluded_subtree(tmp_path: pathlib.Path) -> None:
    """Lint checks nothing inside an excluded subtree (dot-dir parity).

    Violations lint flags anywhere indexed -- an invalid name, a bare
    page, conflict markers, a missing index -- go unreported inside the
    excluded subtree, because the walk never enters it.
    """
    _make_wiki(tmp_path, folders={'core': ['design']})
    # plant violations lint would flag anywhere indexed
    vendor = tmp_path / 'vendor'
    vendor.mkdir()
    (vendor / 'bad|name.md').write_text('# bad\n\nBody.\n', encoding='utf-8')
    (vendor / 'bare.md').write_text('No frontmatter.\n', encoding='utf-8')
    (vendor / 'conflict.md').write_text(
        '<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> branch\n',
        encoding='utf-8',
    )
    _set_exclude_patterns(tmp_path, ['vendor'])

    assert Wiki(tmp_path).lint() == []


def test_lint_stale_link_into_excluded_subtree_is_live(
    tmp_path: pathlib.Path,
) -> None:
    """A prose wikilink into an excluded-but-present file stays live.

    Exclusion is indexing policy, not access control: body prose
    referencing excluded content resolves on the filesystem (matching
    how symlinked targets probe today), so it draws no stale-link note.
    """
    _make_wiki(tmp_path, folders={'notes': ['meeting']})
    (tmp_path / 'vendor').mkdir()
    (tmp_path / 'vendor' / 'lib.md').write_text(
        '---\nname: lib\ndesc: A vendored page.\n---\n\n# lib\n\nBody.\n',
        encoding='utf-8',
    )
    # reference the excluded page from an indexed page's prose
    meeting = tmp_path / 'notes' / 'meeting.md'
    meeting.write_text(
        meeting.read_text(encoding='utf-8').replace(
            'Content for meeting.',
            'See [[vendor/lib]] for details.',
        ),
        encoding='utf-8',
    )
    _set_exclude_patterns(tmp_path, ['vendor'])
    wiki = Wiki(tmp_path)
    notices = _capture_notices(wiki)

    assert wiki.lint() == []
    assert not any('Stale link' in event.description for event in notices)


@pytest.mark.parametrize(
    argnames=('skip', 'target'),
    argvalues=[
        # a folder an exclude.patterns glob keeps out of the walk
        ('excluded', 'vendor'),
        # a symlinked folder -- the walk never follows one
        ('symlinked', 'vendor'),
        # a real folder sitting under a symlinked ancestor, which keeps
        # the whole subtree out however deep the target reaches
        ('under-symlink', 'vendor/sub'),
    ],
)
def test_lint_directory_link_to_unindexed_folder_is_live(
    tmp_path: pathlib.Path,
    skip: str,
    target: str,
) -> None:
    """A prose link naming a folder the walk skips draws neither issue nor note.

    The directory-link rule steers prose at ``folder/_index``, so it
    fires only for a folder whose index the tool maintains. A pattern-
    excluded or symlinked folder is never walked -- naming its index
    would contradict the index block's own report that the path is not
    indexed -- so the link stays live, like a link into any excluded
    subtree.
    """
    _make_wiki(tmp_path, folders={'notes': ['meeting']})
    # a folder carrying an index on disk that the walk will not enter
    folder = tmp_path / 'vendor' if skip == 'excluded' else tmp_path / '.store'
    if skip == 'under-symlink':
        folder = folder / 'sub'
    folder.mkdir(parents=True)
    (folder / '_index.md').write_text(
        '---\nname: vendored\ndesc: A vendored index.\n---\n\n# vendored\n\n***\n',
        encoding='utf-8',
    )
    if skip != 'excluded':
        (tmp_path / 'vendor').symlink_to(tmp_path / '.store', target_is_directory=True)
    # reference the skipped folder from an indexed page's prose
    meeting = tmp_path / 'notes' / 'meeting.md'
    meeting.write_text(
        meeting.read_text(encoding='utf-8').replace(
            'Content for meeting.',
            f'See [[{target}]] for details.',
        ),
        encoding='utf-8',
    )
    if skip == 'excluded':
        _set_exclude_patterns(tmp_path, ['vendor'])
    wiki = Wiki(tmp_path)
    notices = _capture_notices(wiki)

    assert wiki.lint() == []
    assert not any('Stale link' in event.description for event in notices)


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

    # only the link whose page is gone draws the note (and never an
    # issue); anchors alone are never stale
    notices = _capture_notices(wiki)
    assert wiki.lint() == []
    stale = [
        event.description for event in notices if 'Stale link' in event.description
    ]
    assert len(stale) == 1
    assert 'missing' in stale[0]


@_needs_unprivileged
@pytest.mark.parametrize(
    argnames=('surface', 'target'),
    argvalues=[
        # a prose target past the filesystem's name length limit
        ('prose', 'a' * 300),
        # a prose target under a directory the user cannot search
        ('prose', '.locked/page'),
        # a generated row's target past the name length limit
        ('row', 'a' * 300),
    ],
    ids=['prose-long', 'prose-unreadable', 'row-long'],
)
def test_lint_link_probes_never_raise(
    tmp_path: pathlib.Path,
    surface: str,
    target: str,
) -> None:
    """A link target the filesystem cannot stat reads as missing.

    Link text is authored prose, so a name past the filesystem's length
    limit or a path under an unreadable directory reaches the probes;
    each reads as a missing target -- a stale note for prose, a broken
    link for a generated row -- rather than surfacing the ``OSError``
    that ``pathlib`` re-raises on interpreters before 3.14.
    """
    wiki = _make_wiki(tmp_path, folders={'notes': ['meeting']})
    locked = tmp_path / '.locked'
    locked.mkdir()
    if surface == 'prose':
        meeting = tmp_path / 'notes' / 'meeting.md'
        body = f'See [[{target}]] now.'
        text = meeting.read_text(encoding='utf-8').replace('Content for meeting.', body)
        meeting.write_text(text, encoding='utf-8')
    else:
        # inject the row into the root index's generated block
        root_index = tmp_path / '_index.md'
        text = root_index.read_text(encoding='utf-8')
        text = text.replace(
            '[[notes/_index|notes/]]',
            f'[[{target}|long]]: ...\n[[notes/_index|notes/]]',
            1,
        )
        root_index.write_text(text, encoding='utf-8')
    os.chmod(locked, 0o000)
    try:
        notices = _capture_notices(wiki)
        issues = wiki.lint()
    finally:
        os.chmod(locked, 0o700)
    if surface == 'prose':
        assert issues == []
        stale = [
            event.description for event in notices if 'Stale link' in event.description
        ]
        assert stale == [f'notes/meeting.md: Stale link [[{target}]]']
    else:
        broken = [issue for issue in issues if 'Broken link' in issue]
        assert broken == [f'_index.md: Broken link [[{target}|long]]']
