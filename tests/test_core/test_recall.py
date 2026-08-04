"""Behavioral tests for ranked FTS5 wiki recall."""

from __future__ import annotations

import pathlib

import pytest

from ._helpers import _make_wiki

__all__ = [
    'test_recall_ranks_metadata_and_refreshes_incrementally',
    'test_recall_supports_scope_tags_prefixes_and_raw_queries',
    'test_recall_scopes_to_exact_subtree',
    'test_recall_does_not_reread_unchanged_unreadable_pages',
]


def test_recall_ranks_metadata_and_refreshes_incrementally(
    tmp_path: pathlib.Path,
) -> None:
    """Recall ranks metadata first and never serves stale derived rows.

    The first query creates the ignored cache. Later queries detect a new page
    and a removed page directly from filesystem signatures, without requiring
    ``wiki update`` or an explicit index command.
    """
    wiki = _make_wiki(tmp_path, folders={'core': []})
    title_hit = tmp_path / 'core' / 'title-hit.md'
    title_hit.write_text(
        '---\nname: core/title-hit\ntitle: Mnemosyne\ndesc: A page.\n'
        'tags: [memory]\n---\n\n# title-hit\n\nShort body.\n',
        encoding='utf-8',
    )
    body_hit = tmp_path / 'core' / 'body-hit.md'
    body_hit.write_text(
        '---\nname: core/body-hit\ndesc: A page.\ntags: []\n---\n\n'
        '# body-hit\n\nMnemosyne appears in ordinary prose.\n',
        encoding='utf-8',
    )

    matches = wiki.recall('mnemosyne')
    assert matches[0][0] == 'core/title-hit.md'
    assert (tmp_path / '.wiki' / 'cache' / 'recall.db').is_file()
    assert (tmp_path / '.wiki' / 'cache' / '.gitignore').read_text(
        encoding='utf-8'
    ).strip() == '*'

    fresh = tmp_path / 'core' / 'fresh.md'
    fresh.write_text('# fresh\n\nA newly authored quasarlex page.\n', encoding='utf-8')
    assert wiki.recall('quasarlex')[0][0] == 'core/fresh.md'

    title_hit.unlink()
    assert all(path != 'core/title-hit.md' for path, _, _ in wiki.recall('mnemosyne'))


def test_recall_supports_scope_tags_prefixes_and_raw_queries(
    tmp_path: pathlib.Path,
) -> None:
    """Safe queries compose with tag and subtree filters; raw FTS stays opt-in."""
    wiki = _make_wiki(tmp_path, folders={'core': [], 'guides': []})
    (tmp_path / 'core' / 'agents.md').write_text(
        '---\nname: core/agents\ndesc: A page.\ntags: [agents]\n---\n\n'
        '# agents\n\nZettelkasten context for autonomous work.\n',
        encoding='utf-8',
    )
    (tmp_path / 'guides' / 'notes.md').write_text(
        '---\nname: guides/notes\ndesc: A page.\ntags: [writing]\n---\n\n'
        '# notes\n\nZettelkasten context for authors.\n',
        encoding='utf-8',
    )

    tagged = wiki.recall('zettel', prefix=True, tag='agents')
    assert [path for path, _, _ in tagged] == ['core/agents.md']
    scoped = wiki.recall('zettelkasten', name='guides')
    assert [path for path, _, _ in scoped] == ['guides/notes.md']
    raw = wiki.recall('zettel* OR autonomous', raw=True)
    assert {path for path, _, _ in raw} == {
        'core/agents.md',
        'guides/notes.md',
    }
    assert wiki.recall('zettelkasten" OR title:escape AND (') == []
    with pytest.raises(ValueError, match='fts5'):
        wiki.recall('[', raw=True)


def test_recall_scopes_to_exact_subtree(tmp_path: pathlib.Path) -> None:
    """Subtree names treat SQL wildcard characters as literal path text."""
    wiki = _make_wiki(
        tmp_path,
        folders={'my_notes': [], 'myxnotes': [], 'myxnotes/sub': []},
    )
    (tmp_path / 'my_notes' / 'inside.md').write_text(
        '# inside\n\nSharedtoken belongs here.\n',
        encoding='utf-8',
    )
    (tmp_path / 'myxnotes' / 'sub' / 'outside.md').write_text(
        '# outside\n\nSharedtoken belongs elsewhere.\n',
        encoding='utf-8',
    )

    matches = wiki.recall('sharedtoken', name='my_notes')
    assert [path for path, _, _ in matches] == ['my_notes/inside.md']


def test_recall_does_not_reread_unchanged_unreadable_pages(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unchanged read failure stays off the incremental refresh path."""
    wiki = _make_wiki(tmp_path, folders={'core': []})
    unreadable = tmp_path / 'core' / 'unreadable.md'
    unreadable.write_text('# unreadable\n\nHidden token.\n', encoding='utf-8')
    original_read_text = pathlib.Path.read_text
    attempts = 0

    def read_text(
        path: pathlib.Path,
        encoding: str | None = None,
        errors: str | None = None,
    ) -> str:
        nonlocal attempts
        if path == unreadable:
            attempts += 1
            raise OSError('unreadable')
        return original_read_text(path, encoding=encoding, errors=errors)

    monkeypatch.setattr(pathlib.Path, 'read_text', read_text)
    assert wiki.recall('hidden') == []
    assert wiki.recall('hidden') == []
    assert attempts == 1
