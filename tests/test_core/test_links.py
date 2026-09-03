"""Behavioral tests for the ``links.external`` allowlist.

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
issue is covered beside the link tests in ``test_lint``. The other-wiki tests
assume no ``.wiki/settings.json`` sits above pytest's base temporary
directory, where a marker would read as an enclosing wiki (the CLI suite
assumes the same of its root resolver).
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
    bare_anchored,
    page_index,
)

__all__ = [
    'test_links_policy_rejects_invalid_settings',
    'test_init_rejects_invalid_links_seed',
    'test_links_policy_accepts_missing_and_ancestor_folders',
    'test_lint_judges_external_targets_by_the_allowlist',
    'test_lint_reads_external_links_alike_in_pages_and_indexes',
    'test_lint_relative_prefix_names_both_readings',
    'test_lint_stale_external_link_suggests_page_relative_spelling',
    'test_lint_external_links_spare_samples_and_regions',
    'test_lint_external_symlink_probe_follows_its_text',
    'test_links_resolve_from_the_real_root',
    'test_lint_external_probe_never_raises',
    'test_lint_other_wiki_targets_follow_its_rules',
    'test_lint_other_wiki_unreadable_settings_fail_loudly',
    'test_lint_other_wiki_broken_settings_spare_unrelated_links',
    'test_links_other_wiki_runs_no_hook',
    'test_links_other_wiki_notices_ride_the_host_funnel',
    'test_links_home_marker_is_not_a_wiki',
    'test_links_home_symlink_loop_never_raises',
    'test_lint_missing_external_folder_notes_once',
    'test_links_external_never_widens_root_boundary',
    'test_links_external_never_admits_index_rows',
]


# ------ policy validation


@pytest.mark.parametrize(
    argnames=('links', 'match'),
    argvalues=[
        # a wrong-typed block or entry
        ('vendor', r'links block must be a JSON object'),
        ({'external': '../src'}, r'links\.external must be a list'),
        ({'external': [5]}, r'entry must be a string'),
        # an entry naming nothing
        ({'external': ['']}, r'empty or whitespace-only'),
        ({'external': ['   ']}, r'empty or whitespace-only'),
        ({'external': ['/']}, r'empty or whitespace-only'),
        # absolute and home paths, and a foreign separator
        ({'external': ['/etc']}, r'absolute'),
        ({'external': ['~/x']}, r'absolute'),
        ({'external': ['..\\x']}, r'separator'),
        # characters no wikilink target can carry
        ({'external': ['../c#']}, r'no wikilink target can carry'),
        ({'external': ['../a|b']}, r'no wikilink target can carry'),
        ({'external': ['../a[b']}, r'no wikilink target can carry'),
        ({'external': ['../a]b']}, r'no wikilink target can carry'),
        ({'external': ['../a\x00b']}, r'no wikilink target can carry'),
        # empty, '.', and interior '..' segments
        ({'external': ['..//x']}, r'empty segment'),
        ({'external': ['../x/./y']}, r"'\.' segments"),
        ({'external': ['.']}, r"'\.' segments"),
        ({'external': ['../a/../b']}, r'only at the start'),
        ({'external': ['core/..']}, r'only at the start'),
        # the filesystem root, and any spelling of a folder inside the wiki
        ({'external': ['{filesystem_root}']}, r'whole filesystem'),
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
        'open-bracket-char',
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
                entry = entry.format(root=root.name, filesystem_root=climbs)
            external.append(entry)
        links = {'external': external}
    settings = root / '.wiki' / 'settings.json'
    settings.write_text(json.dumps({'links': links}), encoding='utf-8')

    # a fresh instance fails loudly, naming the settings file
    with pytest.raises(ValueError, match=match) as excinfo:
        Wiki(root).lint()
    message = str(excinfo.value)
    assert 'links' in message
    assert 'settings.json' in message


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
        (['..', '../missing'], set(), 'root', '../missing/x', 'skip:../missing'),
        (['../missing', '..'], set(), 'root', '../missing/x', 'skip:../missing'),
        # an entry naming a file names no folder, and shadows an ancestor
        (
            ['../README.md'],
            {'README.md'},
            'root',
            '../README.md/x',
            'skip:../README.md',
        ),
        (
            ['..', '../README.md'],
            {'README.md'},
            'root',
            '../README.md/x',
            'skip:../README.md',
        ),
        # a trailing slash never names a page: the fix drops it, at any depth
        (
            ['../docs'],
            {'docs/guide.md'},
            'root',
            '../docs/guide/',
            'stale-fix:../docs/guide',
        ),
        (
            ['../docs'],
            {'docs/guide.md'},
            'nested',
            '../../docs/guide/',
            'stale-fix:../../docs/guide',
        ),
        # a stray bracket is junk no name carries: stale as written, never read
        # from the page's folder
        (['../src'], {'src/main.py'}, 'nested', '[../../src/main.py', 'stale'),
        # surrounding spaces and tabs are not part of the target
        (['../src'], {'src/main.py'}, 'nested', ' ../../src/main.py', 'live'),
        (['../src'], {'src/main.py'}, 'nested', '../../src/main.py ', 'live'),
        (['../src'], {'src/main.py'}, 'nested', '\t../../src/main.py\t', 'live'),
        # a missing file under a present folder, however deep the path
        (['../src'], {'src/'}, 'root', '../src/gone.py', 'stale'),
        (['../src'], {'src/'}, 'root', '../src/{deep}gone.py', 'stale'),
        # a real file, folder, or normalized path under no entry
        (['../src'], {'docs/guide.md'}, 'root', '../docs/guide', 'outside:../docs'),
        (['../src'], {'docs/'}, 'root', '../docs', 'outside:../docs'),
        (
            ['../src'],
            {'docs/_index.md'},
            'root',
            '../docs',
            'outside:../docs:../docs/_index',
        ),
        # a folder name spelling an entry the policy refuses: stale, not the note
        (['../src'], {'we\\ird/x.md'}, 'root', '../we\\ird/x', 'stale'),
        (['../src'], {'docs/x.md'}, 'root', '../src/../docs/x', 'outside:../docs'),
        # nothing at all under no entry
        (['../src'], set(), 'root', '../docs/guide', 'stale'),
        # no block: a real file is the outside note, a missing one stale
        ([], {'src/main.py'}, 'root', '../src/main.py', 'outside:../src'),
        ([], {'README.md'}, 'root', '../README.md', 'outside:..'),
        ([], set(), 'root', '../src/main.py', 'stale'),
        # an absolute target is never live; under an entry its fix is spelled
        (
            ['../src'],
            {'src/main.py'},
            'nested',
            '{absolute}/src/main.py',
            'stale-fix:../../src/main.py',
        ),
        ([], {'src/main.py'}, 'root', '{absolute}/src/main.py', 'stale'),
        # an absolute target inside the wiki that misses keeps its in-wiki hint
        (
            [],
            set(),
            'root',
            '{absolute}/wiki/notes/meeting/',
            'stale-fix:notes/meeting',
        ),
        # a '..' chain clamped at the filesystem root: no entry could admit it
        (['../src'], set(), 'root', '{filesystem_root}', 'stale'),
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
        'page-trailing-slash',
        'page-trailing-slash-nested',
        'stray-bracket',
        'leading-space',
        'trailing-space',
        'tabs-both',
        'missing-file',
        'deep-missing-file',
        'not-allowlisted-exists',
        'not-allowlisted-folder-target',
        'not-allowlisted-indexed-folder',
        'backslash-folder',
        'normalizes-out',
        'not-allowlisted-missing',
        'no-block-exists',
        'no-block-parent-file',
        'no-block-missing',
        'absolute-under-entry',
        'absolute-not-allowlisted',
        'absolute-inside-trailing-slash',
        'clamped-at-filesystem-root',
    ],
)
def test_lint_judges_external_targets_by_the_allowlist(
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
    otherwise; under an entry naming no folder here -- absent, or a file
    -- it is skipped, and only that entry's note fires; under no entry a
    real file draws the note naming the entry to add, and a missing one
    the stale note, and a folder holding an index file is noted with the
    index page a wiki would link it by; an absolute target leaving the wiki
    is never live, but is steered to its page-relative spelling when it
    lands under an entry; a ``..`` chain clamped at the filesystem root is
    stale, since no entry could admit it; surrounding whitespace is not part
    of the target, a stray bracket makes the text junk (stale as written),
    and a path of thousands of segments is judged like any other. Nothing
    here is a hard issue.
    """
    root = tmp_path / 'wiki'
    _make_wiki(root, folders={'notes': ['meeting']})
    _plant(tmp_path, tree)
    # the placeholders spell paths only the layout knows: the wiki's real
    # location, enough climbs to reach the filesystem root, and a path deep
    # enough to expose a walk that pays per segment
    climbs = '/'.join(['..'] * len(root.resolve().parts))
    deep = 'a/' * 3000
    link = link.format(absolute=tmp_path.resolve(), filesystem_root=climbs, deep=deep)
    relpath = _link_from(root, page, link)
    _set_links_external(root, external)
    wiki = Wiki(root)

    # nothing is a hard issue; the link notes carry the verdict, and a skipped
    # link leaves only its entry's folder note behind
    notices = _capture_notices(wiki)
    assert wiki.lint() == []
    folder_notes = [
        event.description for event in notices if 'names no folder' in event.description
    ]
    notes = [
        event.description
        for event in notices
        if 'names no folder' not in event.description
    ]
    if verdict == 'live':
        assert notes == []
        assert folder_notes == []
    elif verdict.startswith('skip:'):
        entry = verdict.partition(':')[2]
        assert notes == []
        assert folder_notes == [
            f'links.external entry {entry!r} names no folder on this machine;'
            ' links into it are not checked'
        ]
    elif verdict == 'stale':
        assert notes == [f'{relpath}: Stale link [[{link}]]']
    elif verdict.startswith('stale-fix:'):
        fix = verdict.partition(':')[2]
        assert notes == [f'{relpath}: Stale link [[{link}]] (use [[{fix}]])']
        assert folder_notes == []
    else:
        folder, _sep, index = verdict.partition(':')[2].partition(':')
        hint = ''
        if index:
            hint = f', and link [[{index}]] if a wiki indexes the folder'
        assert notes == [
            f'{relpath}: Link [[{link}]] points outside the wiki (add {folder!r}'
            f' to links.external in .wiki/settings.json to allow it{hint})'
        ]


@page_index
@bare_anchored
def test_lint_reads_external_links_alike_in_pages_and_indexes(
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
        f' [[../../src/gone.py{anchor}|Gone]] again,'
        f' [[../../docs/y{anchor}|Doc]], and [[../../docs/y{anchor}|Doc]].\n\n'
        f'| a | b |\n|---|---|\n| [[../../src/gone2.py{anchor}\\|Gone]] | c |'
    )
    text = page.read_text(encoding='utf-8').replace(marker, body)
    page.write_text(text, encoding='utf-8')
    _set_links_external(root, ['../src'])
    wiki = Wiki(root)

    # the live link is silent; stale and outside targets note once each
    notices = _capture_notices(wiki)
    assert wiki.lint() == []
    notes = sorted(event.description for event in notices if '[[' in event.description)
    expected = [
        f'notes/{name}: Stale link [[../../src/gone.py{anchor}|Gone]]',
        f'notes/{name}: Stale link [[../../src/gone2.py{anchor}\\|Gone]]',
        f'notes/{name}: Link [[../../docs/y{anchor}|Doc]] points outside the'
        " wiki (add '../docs' to links.external in .wiki/settings.json to"
        ' allow it)',
    ]
    assert notes == sorted(expected)


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
    body = 'See [[../folder_b/file_b]] and [[../folder_b/file_b.py]].'
    text = page.read_text(encoding='utf-8').replace('Content for file_a.', body)
    page.write_text(text, encoding='utf-8')
    _set_links_external(root, ['..'])
    wiki = Wiki(root)

    # each reading that exists is offered: two for file_b, one for file_b.py
    notices = _capture_notices(wiki)
    issues = wiki.lint()
    assert issues == [
        'folder_a/file_a.md: Link [[../folder_b/file_b]] points inside the wiki'
        " through './' or '../' (use [[folder_b/file_b]], or"
        ' [[../../folder_b/file_b]] for the path outside the wiki)',
        'folder_a/file_a.md: Link [[../folder_b/file_b.py]] points inside the'
        " wiki through './' or '../' (use [[../../folder_b/file_b.py]] for the"
        ' path outside the wiki)',
    ]
    canonicals = [issue.fields.get('canonical') for issue in issues]
    assert canonicals == ['folder_b/file_b', None]
    externals = [issue.fields['external'] for issue in issues]
    assert externals == ['../../folder_b/file_b', '../../folder_b/file_b.py']
    assert not any('[[' in event.description for event in notices)


def test_lint_stale_external_link_suggests_page_relative_spelling(
    tmp_path: pathlib.Path,
) -> None:
    """A link that misses its allowlisted file is steered to the right spelling.

    Read from the wiki root, a target written one folder short reaches
    the file the author meant, so the note names its page-relative
    spelling; so does an absolute target under an allowlisted folder. A
    target reaching nothing either way -- missing, or climbed one folder
    too far -- stays a bare stale note.
    """
    root = tmp_path / 'ws' / 'wiki'
    _make_wiki(root, folders={'notes': ['meeting']})
    _plant(tmp_path, {'src/main.py'})
    absolute = tmp_path.resolve() / 'src' / 'main.py'
    page = root / 'notes' / 'meeting.md'
    body = (
        f'See [[../../src/main.py]], [[{absolute}]], [[../../../../src/main.py]],'
        ' and [[../../../src/gone.py]].'
    )
    text = page.read_text(encoding='utf-8').replace('Content for meeting.', body)
    page.write_text(text, encoding='utf-8')
    _set_links_external(root, ['../../src'])
    wiki = Wiki(root)

    # the short and absolute targets are steered; the missing and over-climbed
    # ones stay bare
    notices = _capture_notices(wiki)
    assert wiki.lint() == []
    notes = sorted(event.description for event in notices if '[[' in event.description)
    expected = [
        'notes/meeting.md: Stale link [[../../src/main.py]] (use [[../../../src/main.py]])',
        'notes/meeting.md: Stale link [[../../../../src/main.py]]',
        'notes/meeting.md: Stale link [[../../../src/gone.py]]',
        f'notes/meeting.md: Stale link [[{absolute}]] (use [[../../../src/main.py]])',
    ]
    assert notes == sorted(expected)


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

    # the three rules fire from prose alone
    notices = _capture_notices(wiki)
    issues = [issue for issue in wiki.lint() if 'points inside the wiki' in issue]
    stale = [event for event in notices if 'Stale link' in event.description]
    outside = [event for event in notices if 'points outside' in event.description]
    expected = 1 if noted else 0
    counts = [len(issues), len(stale), len(outside)]
    assert counts == [expected] * 3


def test_lint_external_symlink_probe_follows_its_text(tmp_path: pathlib.Path) -> None:
    """Containment reads the link's text; existence follows the symlink.

    A symlink under an allowlisted folder counts as inside by its text
    and is live when its destination exists, a dangling one notes stale,
    and a symlinked page inside the wiki pointing outside is live too --
    the posture every link probe already takes, since only a stat
    follows. A symlink loop, under the entry or as a marker folder on the
    way up, reads as missing on every interpreter; a link back into the
    wiki through a symlink alias of it stays stale, since no entry could
    admit the alias.
    """
    root = tmp_path / 'wiki'
    _make_wiki(root)
    _plant(tmp_path, {'src/', 'elsewhere/real.md', 'outside/real.md'})
    (tmp_path / 'src' / 'alias.md').symlink_to(tmp_path / 'elsewhere' / 'real.md')
    (tmp_path / 'src' / 'dangling.md').symlink_to(tmp_path / 'elsewhere' / 'nope.md')
    (tmp_path / 'src' / 'loop').symlink_to('loop')
    (tmp_path / '.wiki').symlink_to('.wiki')
    (tmp_path / 'alias').symlink_to(root, target_is_directory=True)
    (root / 'alias.md').symlink_to(tmp_path / 'outside' / 'real.md')
    links = (
        '../src/alias]], [[../src/dangling]], [[../src/loop/x]],'
        ' [[../alias/_index]], and [[alias'
    )
    _link_from(root, 'root', links)
    _set_links_external(root, ['../src'])
    wiki = Wiki(root)

    # the dangling symlink, the loop, and the alias into the wiki note stale
    notices = _capture_notices(wiki)
    assert wiki.lint() == []
    notes = [event.description for event in notices if '[[' in event.description]
    assert notes == [
        '_index.md: Stale link [[../src/dangling]]',
        '_index.md: Stale link [[../src/loop/x]]',
        '_index.md: Stale link [[../alias/_index]]',
    ]


def test_links_resolve_from_the_real_root(tmp_path: pathlib.Path) -> None:
    """Entries join onto the wiki's real location, not the path it was opened by.

    The root is resolved at construction, so ``..`` in an entry is the
    real parent: a wiki opened through a symlink elsewhere still finds
    the folder beside its real self, and nothing beside the alias.
    """
    real = tmp_path / 'ws' / 'wiki'
    _make_wiki(real)
    _plant(tmp_path / 'ws', {'src/main.py'})
    # an empty src beside the alias would draw the stale note or no folder note
    _plant(tmp_path / 'other', {'src/'})
    (tmp_path / 'other' / 'wiki').symlink_to(real, target_is_directory=True)
    _link_from(real, 'root', '../src/main.py')
    _set_links_external(real, ['../src'])
    wiki = Wiki(tmp_path / 'other' / 'wiki')

    # the entry joins the real parent, so the link is live and nothing notes
    notices = _capture_notices(wiki)
    assert wiki.lint() == []
    assert notices == []


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
    # the probes run against locked paths, restored after the lint
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
    # a target under an unsearchable folder or carrying a NUL reads as missing,
    # an unreadable file is live, and the entry under the locked parent is noted
    assert issues == []
    notes = [event.description for event in notices if 'link' in event.description]
    assert sorted(notes) == [
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
        # an entry naming the wiki itself
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
        (['../math'], '{absolute}/math/g2', 'stale-fix:../math/g2/_index'),
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
    link = link.format(absolute=tmp_path.resolve())
    _link_from(root, 'root', link)
    _set_links_external(root, external)
    wiki = Wiki(root)

    # the other wiki's own rules decide the verdict
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
        issue, *_ = issues
        assert issue.kind == 'directory_link'
        assert notes == []


@pytest.mark.parametrize(
    argnames='fault',
    argvalues=[
        'malformed',
        'looped-marker',
        pytest.param('unreadable-file', marks=_needs_unprivileged),
        pytest.param('unreadable-folder', marks=_needs_unprivileged),
    ],
)
def test_lint_other_wiki_unreadable_settings_fail_loudly(
    tmp_path: pathlib.Path,
    fault: str,
) -> None:
    """A settings file lint cannot read in an allowlisted wiki fails naming it.

    Another wiki's settings are read as the host's are -- user-editable
    input that fails loudly, malformed or unreadable alike, a looped
    marker symlink and the marker folder itself included -- and the error
    names which wiki's file it is, so the two ``.wiki/settings.json`` are
    never confused, without that file's absolute path.
    """
    root = tmp_path / 'wiki'
    _make_wiki(root)
    _make_sibling_wiki(tmp_path / 'math')
    settings = tmp_path / 'math' / '.wiki' / 'settings.json'
    locked = None
    if fault == 'malformed':
        settings.write_text('{', encoding='utf-8')
    elif fault == 'looped-marker':
        settings.unlink()
        settings.symlink_to(settings.name)
    elif fault == 'unreadable-file':
        locked = settings
    else:
        locked = settings.parent
    if locked is not None:
        os.chmod(locked, 0o000)
    _link_from(root, 'root', '../math/g2')
    _set_links_external(root, ['../math'])

    # the error names the other wiki's settings file
    match = r"links\.external wiki '\.\./math'"
    try:
        with pytest.raises(ValueError, match=match) as excinfo:
            Wiki(root).lint()
    finally:
        if locked is not None:
            os.chmod(locked, 0o700)
    message = str(excinfo.value)
    assert 'settings.json' in message
    assert str(tmp_path.resolve()) not in message


def test_lint_other_wiki_broken_settings_spare_unrelated_links(
    tmp_path: pathlib.Path,
) -> None:
    """Only a link whose verdict needs the other wiki fails on its settings.

    A prefixed link that lands inside the host is the relative-link issue
    whatever the other wiki says, and an absolute target is never live;
    their suggestions would consult that wiki for the spelling a folder
    there links by, and are dropped when its settings will not read,
    rather than failing the run -- while a page there by stem is still
    named, since that needs none of its rules.
    """
    root = tmp_path / 'wiki'
    _make_wiki(root, folders={'notes': ['meeting']})
    _make_sibling_wiki(tmp_path / 'math')
    (tmp_path / 'math' / '.wiki' / 'settings.json').write_text('{', encoding='utf-8')
    absolute = tmp_path.resolve() / 'math' / 'g2'
    links = f'../math/g2]], [[../math/lemmas]], and [[{absolute}'
    _link_from(root, 'nested', links)
    _set_links_external(root, ['../math'])
    wiki = Wiki(root)

    # the verdicts stand, with no spelling that needs the unreadable wiki
    notices = _capture_notices(wiki)
    issues = wiki.lint()
    assert issues == [
        'notes/meeting.md: Link [[../math/g2]] points inside the wiki through'
        " './' or '../'",
        'notes/meeting.md: Link [[../math/lemmas]] points inside the wiki through'
        " './' or '../' (use [[../../math/lemmas]] for the path outside the wiki)",
    ]
    notes = [event.description for event in notices if '[[' in event.description]
    assert notes == [f'notes/meeting.md: Stale link [[{absolute}]]']


def test_links_other_wiki_runs_no_hook(tmp_path: pathlib.Path) -> None:
    """Judging another wiki's folders never runs that wiki's code.

    A ``.wiki/wiki.py`` hook loads only through the CLI's trust check;
    the instance lint builds for an allowlisted wiki is the plain class,
    so a hook planted there is never imported; the folder it judges
    reports once however often the prose repeats it.
    """
    root = tmp_path / 'wiki'
    _make_wiki(root)
    _make_sibling_wiki(tmp_path / 'math')
    hook = tmp_path / 'math' / '.wiki' / 'wiki.py'
    hook.write_text(
        "import pathlib\npathlib.Path(__file__).with_name('EXECUTED').touch()\n",
        encoding='utf-8',
    )
    _link_from(root, 'root', '../math/g2]] and again [[../math/g2')
    _set_links_external(root, ['../math'])

    # lint judges the folder once, without importing the hook
    issues = Wiki(root).lint()
    folder_issues = [issue for issue in issues if 'targets a folder' in issue]
    folder_issue_count = len(folder_issues)
    assert folder_issue_count == 1
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

    # the fence note lands in the host's capture, not on the guest's logger
    notices = _capture_notices(wiki)
    issues = wiki.lint()
    assert any('targets a folder' in issue for issue in issues)
    fence = [
        event for event in notices if type(event).__name__ == 'GitFenceUnavailableEvent'
    ]
    fence_count = len(fence)
    assert fence_count == 1


@pytest.mark.parametrize(
    argnames=('exemption', 'readable'),
    argvalues=[
        ('home', True),
        ('config-home', True),
        pytest.param('home', False, marks=_needs_unprivileged),
        pytest.param('config-home', False, marks=_needs_unprivileged),
    ],
    ids=['home', 'config-home', 'home-unreadable', 'config-home-unreadable'],
)
def test_links_home_marker_is_not_a_wiki(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    exemption: str,
    readable: bool,
) -> None:
    """A settings marker at the home or config home never makes a wiki.

    The trust store lives at ``~/.wiki/settings.json`` (or under
    ``WIKI_CONFIG_DIR``), so a wiki beneath the home directory with its
    parent allowlisted would otherwise judge every neighbor by a wiki
    rooted at home: an indexed-looking folder beside it stays a plain
    folder, live in the bare form -- whether or not that ``.wiki`` folder
    can be read.
    """
    home = tmp_path / 'home'
    root = home / 'wiki'
    _make_wiki(root)
    # a marker and an indexed-looking folder beside the wiki, at 'home'
    _plant(home, {'sub/_index.md'})
    (home / '.wiki').mkdir()
    (home / '.wiki' / 'settings.json').write_text('{}\n', encoding='utf-8')
    if exemption == 'home':
        monkeypatch.setenv('HOME', str(home))
    else:
        monkeypatch.setenv('WIKI_CONFIG_DIR', str(home / '.wiki'))
    if not readable:
        os.chmod(home / '.wiki', 0o000)
    _link_from(root, 'root', '../sub')
    _set_links_external(root, ['..'])
    wiki = Wiki(root)

    # the marker makes no wiki, so the bare folder link is live
    notices = _capture_notices(wiki)
    try:
        issues = wiki.lint()
    finally:
        os.chmod(home / '.wiki', 0o700)
    assert issues == []
    assert not any('[[' in event.description for event in notices)


def test_links_home_symlink_loop_never_raises(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A home directory that is a symlink loop leaves the guest lookup intact.

    The exemptions resolve the home and config home through ``realpath``,
    which returns a looped path where ``Path.resolve()`` raises before
    3.13, so another wiki's rules still apply on every interpreter.
    """
    root = tmp_path / 'wiki'
    _make_wiki(root)
    _make_sibling_wiki(tmp_path / 'math')
    (tmp_path / 'loop').symlink_to('loop')
    monkeypatch.setenv('HOME', str(tmp_path / 'loop'))
    _link_from(root, 'root', '../math/g2')
    _set_links_external(root, ['../math'])

    # the sibling wiki's folder is still the directory-link issue
    issues = Wiki(root).lint()
    assert issues == [
        '_index.md: Link [[../math/g2]] targets a folder, not a page'
        ' (use [[../math/g2/_index]])'
    ]


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
    body = 'See [[../../missing/x]] and [[../../missing/y]].'
    for name in ('meeting', 'other'):
        page = root / 'notes' / f'{name}.md'
        text = page.read_text(encoding='utf-8').replace(f'Content for {name}.', body)
        page.write_text(text, encoding='utf-8')
    _set_links_external(root, ['../src', '../missing', '../missing/'])
    wiki = Wiki(root)

    # one note for the absent entry, none for the present one or the links
    notices = _capture_notices(wiki)
    assert wiki.lint() == []
    assert not any('[[' in event.description for event in notices)
    missing = [
        event for event in notices if type(event).__name__ == 'LinkFolderMissingEvent'
    ]
    notes = [event.description for event in missing]
    assert notes == [
        "links.external entry '../missing' names no folder on this machine;"
        ' links into it are not checked'
    ]
    note, *_ = missing
    assert note.folder == '../missing'
    # a scoped run names it once again
    notices.clear()
    assert wiki.lint('notes') == []
    note_count = sum(1 for event in notices if 'names no folder' in event.description)
    assert note_count == 1


# ------ root boundary


# the name-taking operations, by name; each callable takes the wiki
_OUTSIDE_CALLS = {
    'read': lambda wiki: wiki.read('../outside/secret'),
    'match': lambda wiki: wiki.match('Secret', name='../outside'),
    'map': lambda wiki: wiki.map('../outside'),
    'search': lambda wiki: wiki.search('Secret', name='../outside'),
    'update': lambda wiki: wiki.update('../outside'),
    'lint': lambda wiki: wiki.lint('../outside'),
    'new': lambda wiki: wiki.new('../outside/page', desc='A page.', content='Body.'),
}


@pytest.mark.parametrize('operation', sorted(_OUTSIDE_CALLS))
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

    # every name-taking operation refuses the outside target, touching nothing
    with pytest.raises(ValueError, match='outside wiki root'):
        _OUTSIDE_CALLS[operation](wiki)
    names = sorted(path.name for path in outside.iterdir())
    assert names == ['secret.md']
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

    # the row is a broken link to lint, and update prunes it unread
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
    text = path.read_text(encoding='utf-8').replace(marker, f'See [[{link}]] now.')
    path.write_text(text, encoding='utf-8')
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


def _set_links_external(path: pathlib.Path, folders: list[str]) -> None:
    """Write ``links.external`` into an existing wiki's ``settings.json``.

    Policies are cached per instance, so construct a fresh ``Wiki``
    after calling this.
    """
    settings = path / '.wiki' / 'settings.json'
    data = json.loads(settings.read_text(encoding='utf-8'))
    data['links'] = {'external': folders}
    settings.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
