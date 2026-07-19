"""Behavioral tests for ``Wiki.lint``.

The issue taxonomy: out-of-date diffs pinned to what ``update``
would write, human-only issues that persist after update,
formatter-damage diagnosis, ``no-lint`` region suppression, and the
hard-issue vs soft-note split.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from wiki.core.wiki import Wiki

from ._helpers import _capture_notices, _make_wiki, page_index

__all__ = [
    'test_lint_reports_missing_root_name_without_crashing',
    'test_lint_flags_invalid_name',
    'test_lint_names_the_failing_naming_rule',
    'test_lint_flags_what_update_fixes',
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
    'test_lint_link_desc_period',
    'test_lint_scoped',
    'test_lint_flags_blank_created',
    'test_lint_flags_unparseable_stamp',
    'test_lint_future_stamp_is_clean',
    'test_lint_stamp_parse_follows_configured_format',
    'test_lint_hyphen_dangle',
    'test_lint_wrapped_list_marker',
    'test_lint_blank_led_list_is_clean',
    'test_lint_code_span_lead_is_not_marker',
    'test_lint_ignores_code_blocks',
    'test_lint_ignores_multiline_code_span',
    'test_lint_conflict_markers_scan_raw',
    'test_no_lint_region_scopes_positional_rules',
    'test_region_directive_pairing_errors',
    'test_region_directives_pair_per_directive',
    'test_lint_clean',
    'test_quoted_placeholder_desc_is_soft',
    'test_long_desc_is_note_only',
    'test_lint_stale_body_link_names_canonical',
    'test_index_broken_link_is_issue_but_body_link_is_note',
    'test_lint_flags_folder_shadowing_page',
    'test_lint_accepts_anchor_links',
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

    # lint flags the drift; one update fixes it; lint is then clean
    assert wiki.lint() != []
    assert wiki.update() != []
    assert wiki.lint() == []
    assert wiki.update(check=True) == []


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
        ('broken_link', 'Broken link'),
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
    elif perturb == 'broken_link':
        page.unlink()
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

    # inside the region nothing positional fires -- issue or note --
    # while the stale link outside still draws its note
    notices = _capture_notices(wiki)
    issues = wiki.lint()
    notes = '\n'.join(event.description for event in notices)
    assert not any('Merge conflict markers' in issue for issue in issues)
    assert not any('Escaped wikilinks' in issue for issue in issues)
    assert not any('missing_inside' in issue for issue in issues)
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


@pytest.mark.parametrize('anchor', ['', '#context'], ids=['bare', 'anchored'])
def test_lint_stale_body_link_names_canonical(
    tmp_path: pathlib.Path,
    anchor: str,
) -> None:
    """A folder-relative body link is noted with its root-relative fix.

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
    # the stale link is noted with the canonical [[overview]] as the fix
    notices = _capture_notices(wiki)
    wiki.lint()
    stale = [
        event.description
        for event in notices
        if f'Stale link [[../overview{anchor}]]' in event.description
    ]
    assert stale
    assert all(f'(use [[overview{anchor}]])' in note for note in stale)


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

    # update keeps both (no prune), so the issue and the note persist
    wiki.update()
    notices.clear()
    assert any('Broken link [[core/ghost|ghost]]' in issue for issue in wiki.lint())
    notes = '\n'.join(event.description for event in notices)
    assert 'core/_index.md: Stale link [[core/ghost]]' in notes


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
