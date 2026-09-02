"""Behavioral tests for the external link allowlist.

``links.external`` (``.wiki/settings.json``): policy validation and the
init-seed refusal, the verdicts for targets under an allowlisted folder
(pages by stem, literal files, folders), the page-relative spelling a
mis-depth or absolute link is steered to, the note naming the entry to
add for a real file under no allowlisted folder, the once-per-run note
for an entry naming no folder on this machine, sample and region
masking, the symlink probe posture, another wiki's folders judged by its
own settings (its notices riding the host's funnel; a marker at the home
or config home never a wiki), and the unchanged root boundary of the
name-taking operations and the generated index rows. The in-wiki relative-prefix
issue is covered beside the link tests in ``test_lint``.
"""

from __future__ import annotations

import json
import os
import pathlib

import pytest

from wiki.core.wiki import Wiki

from ._helpers import (
    _capture_notices,
    _git_repo,
    _make_wiki,
    _needs_git,
    _needs_unprivileged,
    _set_exclude_patterns,
    _set_links_external,
    page_index,
)

__all__ = [
    'test_links_policy_rejects_invalid_settings',
    'test_init_rejects_invalid_links_seed',
    'test_links_policy_accepts_missing_and_ancestor_folders',
    'test_lint_external_target_verdicts',
    'test_lint_external_link_parity_across_pages_and_indexes',
    'test_lint_relative_prefix_names_both_readings',
    'test_lint_stale_external_link_suggests_page_relative_spelling',
    'test_lint_external_links_spare_samples_and_regions',
    'test_lint_external_symlink_probe_follows_its_text',
    'test_links_resolve_from_the_real_root',
    'test_lint_external_probe_never_raises',
    'test_lint_other_wiki_targets_follow_its_rules',
    'test_lint_other_wiki_unreadable_settings_fail_loudly',
    'test_links_other_wiki_runs_no_hook',
    'test_links_other_wiki_notices_ride_the_host_funnel',
    'test_links_home_marker_is_not_a_wiki',
    'test_lint_missing_external_folder_notes_once',
    'test_links_external_never_widens_root_boundary',
    'test_links_external_never_admits_index_rows',
]


# ------ policy validation


@pytest.mark.parametrize(
    argnames=('links', 'match'),
    argvalues=[
        ('vendor', r'links block must be a JSON object'),
        ({'external': '../src'}, r'links\.external must be a list'),
        ({'external': [5]}, r'entry must be a string'),
        ({'external': ['']}, r'empty or whitespace-only'),
        ({'external': ['   ']}, r'empty or whitespace-only'),
        ({'external': ['/']}, r'empty or whitespace-only'),
        ({'external': ['/etc']}, r'absolute'),
        ({'external': ['~/x']}, r'absolute'),
        ({'external': ['..\\x']}, r'separator'),
        ({'external': ['../c#']}, r'no wikilink target can carry'),
        ({'external': ['../a|b']}, r'no wikilink target can carry'),
        ({'external': ['../a]b']}, r'no wikilink target can carry'),
        ({'external': ['../a\x00b']}, r'no wikilink target can carry'),
        ({'external': ['..//x']}, r'empty segment'),
        ({'external': ['../x/./y']}, r"'\.' segments"),
        ({'external': ['.']}, r"'\.' segments"),
        ({'external': ['../a/../b']}, r'only at the start'),
        ({'external': ['core/..']}, r'only at the start'),
        ({'external': ['{fsroot}']}, r'whole filesystem'),
        ({'external': ['src']}, r'inside the wiki root'),
        ({'external': ['../{root}/core']}, r'inside the wiki root'),
        ({'external': ['../alias']}, r'through an alias'),
    ],
    ids=[
        'non-object-block',
        'non-list-external',
        'non-string-entry',
        'empty',
        'whitespace-only',
        'slash-alone',
        'absolute',
        'home-tilde',
        'backslash',
        'anchor-char',
        'pipe-char',
        'bracket-char',
        'nul',
        'empty-segment',
        'dot-segment',
        'root-dot',
        'interior-dot-dot',
        'lands-on-root',
        'filesystem-root',
        'inside-root',
        'reenters-root',
        'root-alias',
    ],
)
def test_links_policy_rejects_invalid_settings(
    tmp_path: pathlib.Path,
    links: object,
    match: str,
) -> None:
    """A malformed ``links`` block fails loudly, naming the file and key.

    ``settings.json`` is user-editable input: a wrong-typed block or an
    entry outside the folder grammar (absolute and home paths, escapes,
    characters no wikilink target can carry, ``.`` and interior ``..``
    segments, the filesystem root, and any spelling of a folder inside
    the wiki -- lexical or through a symlink alias) raises ``ValueError``
    through any command that reads the policy, links or none.
    """
    root = tmp_path / 'wiki'
    _make_wiki(root, folders={'core': ['design']})
    (tmp_path / 'alias').symlink_to(root, target_is_directory=True)
    # the placeholders spell paths only the layout knows: the root's own
    # folder name, and enough climbs to reach the filesystem root
    if isinstance(links, dict) and isinstance(links['external'], list):
        climbs = '/'.join(['..'] * len(root.parts))
        external = []
        for entry in links['external']:
            if isinstance(entry, str):
                entry = entry.format(root=root.name, fsroot=climbs)
            external.append(entry)
        links = {'external': external}
    settings = root / '.wiki' / 'settings.json'
    settings.write_text(json.dumps({'links': links}), encoding='utf-8')

    # a fresh instance fails loudly, naming the settings file
    with pytest.raises(ValueError, match=match) as excinfo:
        Wiki(root).lint()
    assert 'links' in str(excinfo.value)
    assert 'settings.json' in str(excinfo.value)


def test_init_rejects_invalid_links_seed(tmp_path: pathlib.Path) -> None:
    """A rejected ``links`` seed aborts ``init`` before writing anything.

    The links policy is read lazily (first inside lint's folder check),
    so init touches it up front: a bad seed must fail before the
    settings file, the root index, or any sweep write lands.
    """
    root = tmp_path / 'wiki'
    with pytest.raises(ValueError, match=r'links\.external'):
        Wiki(root).init(settings={'links': {'external': ['src']}})
    assert not root.exists()


@pytest.mark.parametrize(
    argnames='folders',
    argvalues=[
        ['../missing'],
        ['..'],
        ['../src/'],
        ['../src', '../src/'],
    ],
    ids=['absent-folder', 'ancestor', 'trailing-slash', 'repeated'],
)
def test_links_policy_accepts_missing_and_ancestor_folders(
    tmp_path: pathlib.Path,
    folders: list[str],
) -> None:
    """An entry need not exist on disk, and an ancestor of the root is legal.

    A cross-repository entry is absent on a partial checkout, so presence
    is a lint note rather than a load-time error; the enclosing repository
    is the natural single entry for a same-repository wiki; a trailing
    slash and a repeated spelling name the same folder.
    """
    root = tmp_path / 'wiki'
    _make_wiki(root)
    (tmp_path / 'src').mkdir()
    _set_links_external(root, folders)
    assert Wiki(root).lint() == []


# ------ external targets


@pytest.mark.parametrize(
    argnames=('external', 'tree', 'page', 'link', 'verdict'),
    argvalues=[
        # a literal file under an allowlisted folder
        (['../src'], {'src/main.py'}, 'root', '../src/main.py', 'live'),
        # the same file from a nested page climbs once more
        (['../src'], {'src/main.py'}, 'nested', '../../src/main.py', 'live'),
        # a markdown file by its explicit name, and by stem
        (['../docs'], {'docs/guide.md'}, 'root', '../docs/guide.md', 'live'),
        (['../docs'], {'docs/guide.md'}, 'root', '../docs/guide', 'live'),
        # a plain folder, with or without a stray index file
        (['../src'], {'src/pkg/'}, 'root', '../src/pkg', 'live'),
        (['../src'], {'src/pkg/_index.md'}, 'root', '../src/pkg', 'live'),
        # the allowlisted folder itself
        (['../src'], {'src/'}, 'root', '../src', 'live'),
        # a dot-prefixed leaf, and segments with spaces and colons
        (['../dotdir'], {'dotdir/.zshrc'}, 'root', '../dotdir/.zshrc', 'live'),
        (['../sp ace'], {'sp ace/a:b.md'}, 'root', '../sp ace/a:b', 'live'),
        # a trailing slash on the entry, and an ancestor entry
        (['../src/'], {'src/main.py'}, 'root', '../src/main.py', 'live'),
        (['..'], {'README.md'}, 'root', '../README.md', 'live'),
        # an absent entry beside an ancestor: skipped in either order
        (['..', '../missing'], set(), 'root', '../missing/x', 'skip'),
        (['../missing', '..'], set(), 'root', '../missing/x', 'skip'),
        # an entry naming a file names no folder, and shadows an ancestor
        (['../README.md'], {'README.md'}, 'root', '../README.md', 'skip'),
        (['..', '../README.md'], {'README.md'}, 'root', '../README.md', 'skip'),
        # a missing file under a present folder
        (['../src'], {'src/'}, 'root', '../src/gone.py', 'stale'),
        # a real file, folder, or normalized path under no entry
        (['../src'], {'docs/guide.md'}, 'root', '../docs/guide', 'outside:../docs'),
        (['../src'], {'docs/'}, 'root', '../docs', 'outside:../docs'),
        (['../src'], {'docs/x.md'}, 'root', '../src/../docs/x', 'outside:../docs'),
        # nothing at all under no entry
        (['../src'], set(), 'root', '../docs/guide', 'stale'),
        # no block: a real file is the outside note, a missing one stale
        ([], {'src/main.py'}, 'root', '../src/main.py', 'outside:../src'),
        ([], set(), 'root', '../src/main.py', 'stale'),
        # an absolute target is never live; under an entry its fix is spelled
        (
            ['../src'],
            {'src/main.py'},
            'nested',
            '{abs}/src/main.py',
            'stale-fix:../../src/main.py',
        ),
        ([], {'src/main.py'}, 'root', '{abs}/src/main.py', 'stale'),
        # a '..' chain clamped at the filesystem root: no entry could admit it
        (['../src'], set(), 'root', '{fsroot}', 'stale'),
    ],
    ids=[
        'literal-file',
        'nested-page-depth',
        'page-explicit-md',
        'page-stem',
        'plain-folder',
        'plain-folder-with-stray-index',
        'entry-itself',
        'dot-leaf',
        'spaces-and-colon',
        'trailing-slash-entry',
        'ancestor-entry',
        'overlap-order-a',
        'overlap-order-b',
        'file-entry',
        'file-entry-shadows-ancestor',
        'missing-file',
        'not-allowlisted-exists',
        'not-allowlisted-folder-target',
        'normalizes-out',
        'not-allowlisted-missing',
        'no-block-exists',
        'no-block-missing',
        'absolute-under-entry',
        'absolute-not-allowlisted',
        'clamped-at-filesystem-root',
    ],
)
def test_lint_external_target_verdicts(
    tmp_path: pathlib.Path,
    external: list[str],
    tree: set[str],
    page: str,
    link: str,
    verdict: str,
) -> None:
    """A link leaving the wiki is judged by the folders the allowlist names.

    Under an allowlisted folder present on this machine a target is live
    when the file, its ``.md`` form, or the folder exists, and stale
    otherwise; under an entry naming no folder here it is skipped; under
    no entry a real file draws the note naming the entry to add, and a
    missing one the stale note; an absolute target is never live, but is
    steered to its page-relative spelling when it lands under an entry; a
    ``..`` chain clamped at the filesystem root is stale, since no entry
    could admit it. Nothing here is a hard issue.
    """
    root = tmp_path / 'wiki'
    _make_wiki(root, folders={'notes': ['meeting']})
    _plant(tmp_path, tree)
    # the placeholders spell paths only the layout knows: the wiki's real
    # location, and enough climbs to reach the filesystem root
    climbs = '/'.join(['..'] * len(root.resolve().parts))
    link = link.format(abs=tmp_path.resolve(), fsroot=climbs)
    relpath = _link_from(root, page, link)
    _set_links_external(root, external)
    wiki = Wiki(root)

    notices = _capture_notices(wiki)
    assert wiki.lint() == []
    notes = [
        event.description
        for event in notices
        if 'names no folder' not in event.description
    ]
    if verdict in ('live', 'skip'):
        assert notes == []
    elif verdict == 'stale':
        assert notes == [f'{relpath}: Stale link [[{link}]]']
    elif verdict.startswith('stale-fix:'):
        fix = verdict.partition(':')[2]
        assert notes == [f'{relpath}: Stale link [[{link}]] (use [[{fix}]])']
    else:
        folder = verdict.partition(':')[2]
        assert notes == [
            f'{relpath}: Link [[{link}]] points outside the wiki (add {folder!r}'
            ' to links.external in .wiki/settings.json to allow it)'
        ]


@page_index
@pytest.mark.parametrize('anchor', ['', '#context'], ids=['bare', 'anchored'])
def test_lint_external_link_parity_across_pages_and_indexes(
    tmp_path: pathlib.Path,
    kind: str,
    anchor: str,
) -> None:
    """External links read alike in a page body and an index body.

    A live link is silent, a target notes once however often the prose
    repeats it, and the alias, anchor, and a table's escaped pipe ride
    into every note so the fix stays copy-pasteable.
    """
    root = tmp_path / 'wiki'
    _make_wiki(root, folders={'notes': ['meeting']})
    _plant(tmp_path, {'src/main.py', 'docs/y.md'})
    name = 'meeting.md' if kind == 'page' else '_index.md'
    marker = 'Content for meeting.' if kind == 'page' else 'Overview of notes.'
    page = root / 'notes' / name
    body = (
        f'See [[../../src/main.py{anchor}|Main]] and'
        f' [[../../src/gone.py{anchor}|Gone]], then'
        f' [[../../src/gone.py{anchor}|Gone]] again, and'
        f' [[../../docs/y{anchor}|Doc]].\n\n'
        f'| a | b |\n|---|---|\n| [[../../src/gone2.py{anchor}\\|Gone]] | c |'
    )
    text = page.read_text(encoding='utf-8').replace(marker, body)
    page.write_text(text, encoding='utf-8')
    _set_links_external(root, ['../src'])
    wiki = Wiki(root)

    notices = _capture_notices(wiki)
    assert wiki.lint() == []
    notes = sorted(event.description for event in notices if '[[' in event.description)
    assert notes == sorted(
        [
            f'notes/{name}: Stale link [[../../src/gone.py{anchor}|Gone]]',
            f'notes/{name}: Stale link [[../../src/gone2.py{anchor}\\|Gone]]',
            f'notes/{name}: Link [[../../docs/y{anchor}|Doc]] points outside the'
            " wiki (add '../docs' to links.external in .wiki/settings.json to"
            ' allow it)',
        ]
    )


def test_lint_relative_prefix_names_both_readings(tmp_path: pathlib.Path) -> None:
    """A prefixed link inside the wiki names both files it could mean.

    Read from the page's folder the text lands on a page inside the wiki;
    read from the wiki root it would reach a file under an allowlisted
    folder. The issue offers the prefix-free spelling for the first and
    the page-relative spelling for the second, so the author picks the
    file they meant, and only the readings that exist are offered.
    """
    root = tmp_path / 'wiki'
    _make_wiki(root, folders={'folder_a': ['file_a'], 'folder_b': ['file_b']})
    _plant(tmp_path, {'folder_b/file_b.md', 'folder_b/file_b.py'})
    page = root / 'folder_a' / 'file_a.md'
    page.write_text(
        page.read_text(encoding='utf-8').replace(
            'Content for file_a.',
            'See [[../folder_b/file_b]] and [[../folder_b/file_b.py]].',
        ),
        encoding='utf-8',
    )
    _set_links_external(root, ['..'])
    wiki = Wiki(root)

    notices = _capture_notices(wiki)
    issues = wiki.lint()
    assert issues == [
        'folder_a/file_a.md: Link [[../folder_b/file_b]] points inside the wiki'
        " through './' or '../' (use [[folder_b/file_b]], or"
        ' [[../../folder_b/file_b]] for the file outside the wiki)',
        'folder_a/file_a.md: Link [[../folder_b/file_b.py]] points inside the'
        " wiki through './' or '../' (use [[../../folder_b/file_b.py]] for the"
        ' file outside the wiki)',
    ]
    assert [issue.fields.get('canonical') for issue in issues] == [
        'folder_b/file_b',
        None,
    ]
    assert [issue.fields['external'] for issue in issues] == [
        '../../folder_b/file_b',
        '../../folder_b/file_b.py',
    ]
    assert not any('[[' in event.description for event in notices)


def test_lint_stale_external_link_suggests_page_relative_spelling(
    tmp_path: pathlib.Path,
) -> None:
    """A link that misses its allowlisted file is steered to the right spelling.

    Read from the wiki root, a target written one folder short reaches
    the file the author meant, so the note names its page-relative
    spelling; so does an absolute target under an allowlisted folder. A
    target reaching nothing either way stays a bare stale note, and one
    that lands inside the wiki is the relative-link issue with the same
    spelling offered.
    """
    root = tmp_path / 'wiki'
    _make_wiki(root, folders={'notes': ['meeting']})
    _plant(tmp_path, {'src/main.py'})
    absolute = tmp_path.resolve() / 'src' / 'main.py'
    page = root / 'notes' / 'meeting.md'
    page.write_text(
        page.read_text(encoding='utf-8').replace(
            'Content for meeting.',
            f'See [[../../src/gone.py]], [[{absolute}]], [[../../../src/main.py]],'
            ' and [[../src/main.py]].',
        ),
        encoding='utf-8',
    )
    _set_links_external(root, ['../src'])
    wiki = Wiki(root)

    notices = _capture_notices(wiki)
    issues = wiki.lint()
    assert issues == [
        'notes/meeting.md: Link [[../src/main.py]] points inside the wiki through'
        " './' or '../' (use [[../../src/main.py]] for the file outside the wiki)"
    ]
    notes = sorted(event.description for event in notices if '[[' in event.description)
    assert notes == sorted(
        [
            'notes/meeting.md: Stale link [[../../../src/main.py]]',
            'notes/meeting.md: Stale link [[../../src/gone.py]]',
            f'notes/meeting.md: Stale link [[{absolute}]] (use [[../../src/main.py]])',
        ]
    )


@pytest.mark.parametrize(
    argnames=('body', 'noted'),
    argvalues=[
        ('```\n{links}\n```', False),
        ('a `{links}` span', False),
        ('<!-- {links} -->', False),
        ('para\n\n    {links}', False),
        ('<!-- start: no-lint -->\n{links}\n<!-- end: no-lint -->', False),
        ('See {links} now.', True),
    ],
    ids=['fenced', 'inline', 'comment', 'indented', 'region', 'prose'],
)
def test_lint_external_links_spare_samples_and_regions(
    tmp_path: pathlib.Path,
    body: str,
    noted: bool,
) -> None:
    """Masks and regions spare every external link rule, prose sees them all.

    A stale allowlisted target, a real file under no entry, and a
    prefixed target landing inside the wiki each fire from plain prose
    only; inside a code sample, an HTML comment, or a ``no-lint`` region
    none of them does, as the in-wiki rules behave.
    """
    root = tmp_path / 'wiki'
    _make_wiki(root, folders={'notes': ['meeting', 'sibling']})
    _plant(tmp_path, {'src/', 'docs/y.md'})
    links = '[[../../src/gone.py]] [[../../docs/y]] [[./sibling]]'
    page = root / 'notes' / 'meeting.md'
    body = body.format(links=links)
    text = page.read_text(encoding='utf-8').replace('Content for meeting.', body)
    page.write_text(text, encoding='utf-8')
    _set_links_external(root, ['../src'])
    wiki = Wiki(root)

    notices = _capture_notices(wiki)
    issues = [issue for issue in wiki.lint() if 'points inside the wiki' in issue]
    stale = [event for event in notices if 'Stale link' in event.description]
    outside = [event for event in notices if 'points outside' in event.description]
    expected = 1 if noted else 0
    assert [len(issues), len(stale), len(outside)] == [expected] * 3


def test_lint_external_symlink_probe_follows_its_text(tmp_path: pathlib.Path) -> None:
    """Containment reads the link's text; existence follows the symlink.

    A symlink under an allowlisted folder counts as inside by its text
    and is live when its destination exists, a dangling one notes stale,
    and a symlinked page inside the wiki pointing outside is live too --
    the posture every link probe already takes, since only a stat
    follows.
    """
    root = tmp_path / 'wiki'
    _make_wiki(root)
    _plant(tmp_path, {'src/', 'elsewhere/real.md', 'outside/real.md'})
    (tmp_path / 'src' / 'alias.md').symlink_to(tmp_path / 'elsewhere' / 'real.md')
    (tmp_path / 'src' / 'dangling.md').symlink_to(tmp_path / 'elsewhere' / 'nope.md')
    (root / 'alias.md').symlink_to(tmp_path / 'outside' / 'real.md')
    _link_from(root, 'root', '../src/alias]], [[../src/dangling]], and [[alias')
    _set_links_external(root, ['../src'])
    wiki = Wiki(root)

    notices = _capture_notices(wiki)
    assert wiki.lint() == []
    notes = [event.description for event in notices if '[[' in event.description]
    assert notes == ['_index.md: Stale link [[../src/dangling]]']


def test_links_resolve_from_the_real_root(tmp_path: pathlib.Path) -> None:
    """Entries join onto the wiki's real location, not the path it was opened by.

    The root is resolved at construction, so ``..`` in an entry is the
    real parent: a wiki opened through a symlink elsewhere still finds
    the folder beside its real self, and nothing beside the alias.
    """
    real = tmp_path / 'ws' / 'wiki'
    _make_wiki(real)
    _plant(tmp_path / 'ws', {'src/main.py'})
    (tmp_path / 'other').mkdir()
    (tmp_path / 'other' / 'wiki').symlink_to(real, target_is_directory=True)
    _link_from(real, 'root', '../src/main.py')
    _set_links_external(real, ['../src'])
    wiki = Wiki(tmp_path / 'other' / 'wiki')

    notices = _capture_notices(wiki)
    assert wiki.lint() == []
    assert not any('[[' in event.description for event in notices)


@_needs_unprivileged
def test_lint_external_probe_never_raises(tmp_path: pathlib.Path) -> None:
    """A path outside the wiki that cannot be stat-ed reads as missing.

    A link into a directory the user cannot search notes stale under an
    allowlisted folder and stays a bare stale note under none; a file the
    user cannot read is live, since only a stat follows; a target carrying
    a NUL byte is stale under a plain folder and inside another wiki
    alike; an entry beneath an unreadable parent draws the folder note.
    None of it raises, on any interpreter the package supports.
    """
    root = tmp_path / 'wiki'
    _make_wiki(root)
    _make_sibling_wiki(tmp_path / 'math')
    tree = {'src/locked/', 'src/secret.txt', 'unlisted/locked/', 'parent/inner/'}
    _plant(tmp_path, tree)
    links = (
        '../src/locked/x]], [[../src/secret.txt]], [[../src/a\x00b]],'
        ' [[../math/g2/a\x00b]], and [[../unlisted/locked/x'
    )
    _link_from(root, 'root', links)
    _set_links_external(root, ['../src', '../math', '../parent/inner'])
    wiki = Wiki(root)
    locked = [
        tmp_path / 'src' / 'locked',
        tmp_path / 'src' / 'secret.txt',
        tmp_path / 'unlisted' / 'locked',
        tmp_path / 'parent',
    ]
    for path in locked:
        os.chmod(path, 0o000)
    try:
        notices = _capture_notices(wiki)
        issues = wiki.lint()
    finally:
        for path in locked:
            os.chmod(path, 0o700)
    assert issues == []
    assert sorted(
        event.description for event in notices if 'link' in event.description
    ) == [
        '_index.md: Stale link [[../math/g2/a\x00b]]',
        '_index.md: Stale link [[../src/a\x00b]]',
        '_index.md: Stale link [[../src/locked/x]]',
        '_index.md: Stale link [[../unlisted/locked/x]]',
        "links.external entry '../parent/inner' names no folder on this machine;"
        ' links into it are not checked',
    ]


# ------ other wikis


@pytest.mark.parametrize(
    argnames=('external', 'link', 'verdict'),
    argvalues=[
        (['../math'], '../math/lemmas', 'live'),
        (['../math'], '../math/lemmas.md', 'live'),
        (['../math'], '../math/g2/_index', 'live'),
        (['../math'], '../math/g2', 'issue:../math/g2/_index'),
        (['../math'], '../math', 'issue:../math/_index'),
        (['../math'], '../math/vendor', 'live'),
        (['../math'], '../math/gone', 'stale'),
        (['../math'], '../math/g2#top|G', 'issue:../math/g2/_index#top|G'),
        # an ancestor entry, and one inside the wiki, reach the same rules
        (['..'], '../math/g2', 'issue:../math/g2/_index'),
        (['../math/g2'], '../math/g2', 'issue:../math/g2/_index'),
        (['../math/g2'], '../math/g2/topic', 'live'),
        # an absolute target is steered to the index page the folder links by
        (['../math'], '{abs}/math/g2', 'stale-fix:../math/g2/_index'),
    ],
    ids=[
        'page-stem',
        'page-explicit-md',
        'index-form',
        'indexed-folder',
        'wiki-root',
        'excluded-folder',
        'missing-page',
        'anchor-and-alias',
        'ancestor-entry',
        'entry-inside-wiki-folder',
        'entry-inside-wiki-page',
        'absolute-indexed-folder',
    ],
)
def test_lint_other_wiki_targets_follow_its_rules(
    tmp_path: pathlib.Path,
    external: list[str],
    link: str,
    verdict: str,
) -> None:
    """A target inside another wiki is judged by that wiki's own settings.

    Its pages link by stem, a folder it indexes is the same directory-link
    issue as at home (naming its ``_index`` page, anchor and alias riding
    along), a folder its ``exclude.patterns`` keeps out of its walk is
    live in the bare form, and a missing page is stale -- whether the
    entry names the wiki, an ancestor of it, or a folder inside it. An
    absolute target landing on an indexed folder is steered to the index
    page. Nothing of that wiki runs; its settings are read like the
    host's.
    """
    root = tmp_path / 'wiki'
    _make_wiki(root)
    _make_sibling_wiki(tmp_path / 'math')
    link = link.format(abs=tmp_path.resolve())
    _link_from(root, 'root', link)
    _set_links_external(root, external)
    wiki = Wiki(root)

    notices = _capture_notices(wiki)
    issues = wiki.lint()
    notes = [event.description for event in notices if '[[' in event.description]
    if verdict == 'live':
        assert (issues, notes) == ([], [])
    elif verdict == 'stale':
        assert (issues, notes) == ([], [f'_index.md: Stale link [[{link}]]'])
    elif verdict.startswith('stale-fix:'):
        fix = verdict.partition(':')[2]
        expected = f'_index.md: Stale link [[{link}]] (use [[{fix}]])'
        assert (issues, notes) == ([], [expected])
    else:
        fix = verdict.partition(':')[2]
        assert issues == [
            f'_index.md: Link [[{link}]] targets a folder, not a page (use [[{fix}]])'
        ]
        assert issues[0].kind == 'directory_link'
        assert notes == []


@pytest.mark.parametrize(
    argnames='fault',
    argvalues=['malformed', pytest.param('unreadable', marks=_needs_unprivileged)],
)
def test_lint_other_wiki_unreadable_settings_fail_loudly(
    tmp_path: pathlib.Path,
    fault: str,
) -> None:
    """A settings file lint cannot read in an allowlisted wiki fails naming it.

    Another wiki's settings are read as the host's are -- user-editable
    input that fails loudly, malformed or unreadable alike -- and the
    error names which wiki's file it is, so the two
    ``.wiki/settings.json`` are never confused.
    """
    root = tmp_path / 'wiki'
    _make_wiki(root)
    _make_sibling_wiki(tmp_path / 'math')
    settings = tmp_path / 'math' / '.wiki' / 'settings.json'
    if fault == 'malformed':
        settings.write_text('{', encoding='utf-8')
    else:
        os.chmod(settings, 0o000)
    _link_from(root, 'root', '../math/g2')
    _set_links_external(root, ['../math'])

    match = r"links\.external wiki '\.\./math'"
    try:
        with pytest.raises(ValueError, match=match) as excinfo:
            Wiki(root).lint()
    finally:
        os.chmod(settings, 0o644)
    assert 'settings.json' in str(excinfo.value)


def test_links_other_wiki_runs_no_hook(tmp_path: pathlib.Path) -> None:
    """Judging another wiki's folders never runs that wiki's code.

    A ``.wiki/wiki.py`` hook loads only through the CLI's trust check;
    the instance lint builds for an allowlisted wiki is the plain class,
    so a hook planted there is never imported.
    """
    root = tmp_path / 'wiki'
    _make_wiki(root)
    _make_sibling_wiki(tmp_path / 'math')
    hook = tmp_path / 'math' / '.wiki' / 'wiki.py'
    hook.write_text(
        "import pathlib\npathlib.Path(__file__).with_name('EXECUTED').touch()\n",
        encoding='utf-8',
    )
    _link_from(root, 'root', '../math/g2')
    _set_links_external(root, ['../math'])

    issues = Wiki(root).lint()
    assert any('targets a folder' in issue for issue in issues)
    assert not (tmp_path / 'math' / '.wiki' / 'EXECUTED').exists()


@_needs_git
def test_links_other_wiki_notices_ride_the_host_funnel(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A note the other wiki's rules raise reaches the host's notice hook.

    Judging a folder consults that wiki's gitignore fence; with git
    unavailable inside its repository the fence is narrated, and the note
    lands in the host's ``on_notice`` -- where a capturing host counts
    it -- rather than on the guest instance's own logger.
    """
    root = tmp_path / 'wiki'
    _make_wiki(root)
    _make_sibling_wiki(tmp_path / 'math')
    _git_repo(tmp_path / 'math')
    _link_from(root, 'root', '../math/g2')
    _set_links_external(root, ['../math'])
    wiki = Wiki(root)
    (tmp_path / 'nobin').mkdir()
    monkeypatch.setenv('PATH', str(tmp_path / 'nobin'))

    notices = _capture_notices(wiki)
    issues = wiki.lint()
    assert any('targets a folder' in issue for issue in issues)
    fence = [
        event for event in notices if type(event).__name__ == 'GitFenceUnavailableEvent'
    ]
    assert len(fence) == 1


@pytest.mark.parametrize('exemption', ['home', 'config-home'])
def test_links_home_marker_is_not_a_wiki(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    exemption: str,
) -> None:
    """A settings marker at the home or config home never makes a wiki.

    The trust store lives at ``~/.wiki/settings.json`` (or under
    ``WIKI_CONFIG_DIR``), so a wiki beneath the home directory with its
    parent allowlisted would otherwise judge every neighbor by a wiki
    rooted at home: an indexed-looking folder beside it stays a plain
    folder, live in the bare form.
    """
    home = tmp_path / 'home'
    root = home / 'wiki'
    _make_wiki(root)
    # a marker and an indexed-looking folder beside the wiki, at "home"
    _plant(home, {'sub/_index.md'})
    (home / '.wiki').mkdir()
    (home / '.wiki' / 'settings.json').write_text('{}\n', encoding='utf-8')
    if exemption == 'home':
        monkeypatch.setenv('HOME', str(home))
    else:
        monkeypatch.setenv('WIKI_CONFIG_DIR', str(home / '.wiki'))
    _link_from(root, 'root', '../sub')
    _set_links_external(root, ['..'])
    wiki = Wiki(root)

    notices = _capture_notices(wiki)
    assert wiki.lint() == []
    assert not any('[[' in event.description for event in notices)


# ------ missing entries


def test_lint_missing_external_folder_notes_once(tmp_path: pathlib.Path) -> None:
    """An entry naming no folder on this machine draws one note per lint run.

    The note names the entry as written, fires whether or not any link
    reaches under it, collapses repeated spellings of one folder, and
    repeats once for a scoped run -- an environment condition reported
    once -- while the links into the entry draw nothing at all.
    """
    root = tmp_path / 'wiki'
    _make_wiki(root, folders={'notes': ['meeting', 'other']})
    (tmp_path / 'src').mkdir()
    for name in ('meeting', 'other'):
        page = root / 'notes' / f'{name}.md'
        page.write_text(
            page.read_text(encoding='utf-8').replace(
                f'Content for {name}.',
                'See [[../../missing/x]] and [[../../missing/y]].',
            ),
            encoding='utf-8',
        )
    _set_links_external(root, ['../src', '../missing', '../missing/'])
    wiki = Wiki(root)

    # one note for the absent entry, none for the present one or the links
    notices = _capture_notices(wiki)
    assert wiki.lint() == []
    assert not any('[[' in event.description for event in notices)
    missing = [
        event for event in notices if type(event).__name__ == 'LinkFolderMissingEvent'
    ]
    assert [event.description for event in missing] == [
        "links.external entry '../missing' names no folder on this machine;"
        ' links into it are not checked'
    ]
    assert missing[0].folder == '../missing'
    # a scoped run names it once again
    notices.clear()
    assert wiki.lint('notes') == []
    assert sum(1 for event in notices if 'names no folder' in event.description) == 1


# ------ root boundary


@pytest.mark.parametrize(
    argnames='operation',
    argvalues=['read', 'match', 'map', 'search', 'update', 'lint', 'new'],
)
def test_links_external_never_widens_root_boundary(
    tmp_path: pathlib.Path,
    operation: str,
) -> None:
    """An allowlisted folder is a lint rule, never a grant to the operations.

    With the whole parent allowlisted, every name-taking operation still
    refuses a target outside the root -- the allowlist changes what lint
    checks, not what the wiki may read, write, or index -- and the
    outside tree is untouched.
    """
    root = tmp_path / 'wiki'
    _make_wiki(root)
    outside = tmp_path / 'outside'
    outside.mkdir()
    secret = outside / 'secret.md'
    secret.write_text('Secret content.\n', encoding='utf-8')
    _set_links_external(root, ['..'])
    wiki = Wiki(root)

    calls = {
        'read': lambda: wiki.read('../outside/secret'),
        'match': lambda: wiki.match('Secret', name='../outside'),
        'map': lambda: wiki.map('../outside'),
        'search': lambda: wiki.search('Secret', name='../outside'),
        'update': lambda: wiki.update('../outside'),
        'lint': lambda: wiki.lint('../outside'),
        'new': lambda: wiki.new('../outside/page', desc='A page.', content='Body.'),
    }
    with pytest.raises(ValueError, match='outside wiki root'):
        calls[operation]()
    assert sorted(path.name for path in outside.iterdir()) == ['secret.md']
    assert secret.read_text(encoding='utf-8') == 'Secret content.\n'


def test_links_external_never_admits_index_rows(tmp_path: pathlib.Path) -> None:
    """Generated rows stay filesystem-derived under any allowlist.

    An injected row escaping the root is a broken link to lint and is
    pruned by update without its target ever being read: the allowlist
    admits prose links, never a foreign file's ``desc:`` into a tracked
    index.
    """
    root = tmp_path / 'wiki'
    _make_wiki(root, folders={'sub': ['child']})
    (tmp_path / 'secret.md').write_text(
        '---\nname: secret\ndesc: LEAKED SECRET\n---\n\n# secret\n\nx.\n',
        encoding='utf-8',
    )
    sub_index = root / 'sub' / '_index.md'
    text = sub_index.read_text(encoding='utf-8')
    text = text.replace(
        '[[sub/child|child]]',
        '[[../secret|leaked]]: ...\n[[sub/child|child]]',
        1,
    )
    sub_index.write_text(text, encoding='utf-8')
    _set_links_external(root, ['..'])
    wiki = Wiki(root)

    issues = wiki.lint()
    assert 'sub/_index.md: Broken link [[../secret|leaked]]' in issues
    wiki.update()
    updated = sub_index.read_text(encoding='utf-8')
    assert '../secret' not in updated
    assert 'LEAKED SECRET' not in updated


# ------ helpers


def _plant(base: pathlib.Path, entries: set[str]) -> None:
    """Create the files, and folders (trailing ``/``), that ``entries`` name."""
    for entry in sorted(entries):
        target = base / entry
        if entry.endswith('/'):
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('x\n', encoding='utf-8')


def _link_from(root: pathlib.Path, page: str, link: str) -> str:
    """Write ``[[link]]`` into the root index or the nested page; return its relpath."""
    if page == 'root':
        path = root / '_index.md'
        marker = 'Root overview.'
    else:
        path = root / 'notes' / 'meeting.md'
        marker = 'Content for meeting.'
    path.write_text(
        path.read_text(encoding='utf-8').replace(marker, f'See [[{link}]] now.'),
        encoding='utf-8',
    )
    return path.relative_to(root).as_posix()


def _make_sibling_wiki(path: pathlib.Path) -> None:
    """Create a wiki beside the one under test: a page, an indexed folder, an excluded one."""
    _make_wiki(path, folders={'g2': ['topic'], 'vendor': ['v']})
    (path / 'lemmas.md').write_text(
        '---\nname: lemmas\ndesc: Lemmas.\n---\n\n# lemmas\n\nText.\n',
        encoding='utf-8',
    )
    _set_exclude_patterns(path, ['vendor'])
    Wiki(path).update()
