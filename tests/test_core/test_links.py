"""Behavioral tests for the external link allowlist.

``links.external`` (``.wiki/settings.json``): policy validation and the
init-seed refusal, the entries accepted without a folder on disk, and
the once-per-run note for an entry naming no folder on this machine.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from wiki.core.wiki import Wiki

from ._helpers import (
    _capture_notices,
    _make_wiki,
    _set_links_external,
)

__all__ = [
    'test_links_policy_rejects_invalid_settings',
    'test_init_rejects_invalid_links_seed',
    'test_links_policy_accepts_missing_and_ancestor_folders',
    'test_lint_missing_external_folder_notes_once',
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
        ({'external': ['../alias']}, r'aliases the wiki root'),
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
    if isinstance(links, dict):
        climbs = '/'.join(['..'] * len(root.parts))
        links = {
            'external': [
                entry.format(root=root.name, fsroot=climbs)
                if isinstance(entry, str)
                else entry
                for entry in links['external']
            ]
            if isinstance(links['external'], list)
            else links['external']
        }
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


# ------ missing entries


def test_lint_missing_external_folder_notes_once(tmp_path: pathlib.Path) -> None:
    """An entry naming no folder on this machine draws one note per lint run.

    The note names the entry as written, fires whether or not any link
    reaches under it, collapses repeated spellings of one folder, and
    repeats once for a scoped run -- an environment condition reported
    once, not a stale link reported per page.
    """
    root = tmp_path / 'wiki'
    wiki = _make_wiki(root, folders={'notes': ['meeting', 'other']})
    (tmp_path / 'src').mkdir()
    _set_links_external(root, ['../src', '../missing', '../missing/'])
    wiki = Wiki(root)

    # one note for the absent entry, none for the present one
    notices = _capture_notices(wiki)
    assert wiki.lint() == []
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
