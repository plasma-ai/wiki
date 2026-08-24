"""Behavioral tests for ``Wiki.search``, the ranked FTS5 search."""

from __future__ import annotations

import pathlib
import shutil
import sqlite3
from typing import Optional

import pytest

from wiki.core.wiki import Wiki

from ._helpers import _capture_notices, _make_wiki, _set_exclude_patterns

__all__ = [
    'test_search_ranks_metadata_and_refreshes_incrementally',
    'test_search_ranks_desc_matches_above_body_prose',
    'test_search_never_returns_index_pages',
    'test_search_skips_excluded_paths',
    'test_search_supports_scope_tags_prefixes_and_raw_queries',
    'test_search_propagates_non_query_operational_errors',
    'test_search_scopes_to_exact_subtree',
    'test_search_and_match_canonicalize_scope_casing',
    'test_search_does_not_reread_unchanged_unreadable_pages',
    'test_search_notices_unreadable_pages_per_read_attempt',
    'test_search_retries_indexed_pages_after_a_transient_read_failure',
    'test_search_rebuilds_a_corrupt_index',
    'test_search_rebuilds_an_outdated_schema_cache',
]


def test_search_ranks_metadata_and_refreshes_incrementally(
    tmp_path: pathlib.Path,
) -> None:
    """Search ranks metadata first and never serves stale derived rows.

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
    # drop the cache update() seeded, so the first query's own cache write
    # is what the assertions below see
    shutil.rmtree(tmp_path / '.wiki' / 'cache')

    # the first query ranks the title match first and seeds the ignored cache
    matches = wiki.search('mnemosyne')
    assert matches[0][0] == 'core/title-hit.md'
    assert (tmp_path / '.wiki' / 'cache' / 'search.db').is_file()
    gitignore = (tmp_path / '.wiki' / 'cache' / '.gitignore').read_text(
        encoding='utf-8'
    )
    assert gitignore.strip() == '*'

    # a new page is detected from filesystem signatures
    fresh = tmp_path / 'core' / 'fresh.md'
    fresh.write_text('# fresh\n\nA newly authored quasarlex page.\n', encoding='utf-8')
    assert wiki.search('quasarlex')[0][0] == 'core/fresh.md'

    # a removed page leaves the results
    title_hit.unlink()
    assert all(path != 'core/title-hit.md' for path, _, _ in wiki.search('mnemosyne'))


def test_search_ranks_desc_matches_above_body_prose(tmp_path: pathlib.Path) -> None:
    """A frontmatter-desc match outranks the same term in body prose."""
    wiki = _make_wiki(tmp_path, folders={'core': []})
    desc_hit = tmp_path / 'core' / 'desc-hit.md'
    desc_hit.write_text(
        '---\nname: core/desc-hit\ndesc: Chronotrope timing utilities.\n'
        'tags: []\n---\n\n# desc-hit\n\nShort body.\n',
        encoding='utf-8',
    )
    body_hit = tmp_path / 'core' / 'body-hit.md'
    body_hit.write_text(
        '---\nname: core/body-hit\ndesc: A page.\ntags: []\n---\n\n'
        '# body-hit\n\nChronotrope appears in ordinary prose.\n',
        encoding='utf-8',
    )

    matches = wiki.search('chronotrope')
    assert [path for path, _, _ in matches] == [
        'core/desc-hit.md',
        'core/body-hit.md',
    ]


def test_search_never_returns_index_pages(tmp_path: pathlib.Path) -> None:
    """Index pages stay out of results even when their link blocks match."""
    wiki = _make_wiki(tmp_path, folders={'core': ['glimmerfax']})
    # the generated link block carries the child's name and desc, so the
    # index would shadow the page itself in ranked results
    index_text = (tmp_path / 'core' / '_index.md').read_text(encoding='utf-8')
    assert 'glimmerfax' in index_text

    matches = wiki.search('glimmerfax')
    assert [path for path, _, _ in matches] == ['core/glimmerfax.md']


def test_search_skips_excluded_paths(tmp_path: pathlib.Path) -> None:
    """Excluded pages never surface in ranked results.

    Search indexes through the same walk update and match enumerate
    with, so an ``exclude.patterns`` subtree never reaches the FTS
    index.
    """
    _make_wiki(tmp_path, folders={'core': []})
    (tmp_path / 'vendor').mkdir()
    (tmp_path / 'vendor' / 'lib.md').write_text(
        '---\nname: lib\ndesc: A page.\n---\n\n# lib\n\nneedle prose\n',
        encoding='utf-8',
    )
    (tmp_path / 'core' / 'keep.md').write_text(
        '---\nname: keep\ndesc: A page.\n---\n\n# keep\n\nneedle kept\n',
        encoding='utf-8',
    )
    _set_exclude_patterns(tmp_path, ['vendor'])
    wiki = Wiki(tmp_path)

    # only the indexed sibling reaches the ranked index
    matches = wiki.search('needle')
    assert [path for path, _, _ in matches] == ['core/keep.md']


def test_search_supports_scope_tags_prefixes_and_raw_queries(
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

    # a tag filter composes with prefix matching
    tagged = wiki.search('zettel', prefix=True, tag='agents')
    assert [path for path, _, _ in tagged] == ['core/agents.md']
    # a subtree scope narrows results to its pages
    scoped = wiki.search('zettelkasten', name='guides')
    assert [path for path, _, _ in scoped] == ['guides/notes.md']
    # raw FTS syntax is opt-in
    raw = wiki.search('zettel* OR autonomous', raw=True)
    assert {path for path, _, _ in raw} == {
        'core/agents.md',
        'guides/notes.md',
    }
    # a safe query is sanitized, never parsed as FTS syntax
    assert wiki.search('zettelkasten" OR title:escape AND (') == []
    # an invalid raw query raises instead of matching nothing
    with pytest.raises(ValueError, match='fts5'):
        wiki.search('[', raw=True)


def test_search_propagates_non_query_operational_errors(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only query errors become ``ValueError``; a locking fault propagates."""
    wiki = _make_wiki(tmp_path, folders={'core': []})
    (tmp_path / 'core' / 'page.md').write_text(
        '# page\n\nLockedtoken prose.\n',
        encoding='utf-8',
    )

    class LockedConnection(sqlite3.Connection):
        """Fail the ranked MATCH query with a lock, not a query error."""

        def execute(self, sql: str, *args: object) -> sqlite3.Cursor:
            if 'MATCH' in sql:
                error = sqlite3.OperationalError('database is locked')
                error.sqlite_errorcode = sqlite3.SQLITE_BUSY
                raise error
            return super().execute(sql, *args)

    original_connect = sqlite3.connect

    def connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        return original_connect(*args, factory=LockedConnection, **kwargs)

    monkeypatch.setattr(sqlite3, 'connect', connect)
    with pytest.raises(sqlite3.OperationalError, match='database is locked'):
        wiki.search('lockedtoken')


def test_search_scopes_to_exact_subtree(tmp_path: pathlib.Path) -> None:
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

    matches = wiki.search('sharedtoken', name='my_notes')
    assert [path for path, _, _ in matches] == ['my_notes/inside.md']


def test_search_and_match_canonicalize_scope_casing(tmp_path: pathlib.Path) -> None:
    """A mis-cased subtree scope canonicalizes to the on-disk spelling.

    A case-insensitive filesystem resolves ``CORE`` to the on-disk
    ``core``, so the scope must reach the SQL prefix filter and match's
    reported paths in true casing: the spelled form would scope search
    to nothing while match reported paths under the caller's spelling.
    """
    # scope casing only diverges where the filesystem folds case; probe
    # the tmp_path filesystem rather than assume the platform
    case_probe = tmp_path / 'CaseProbe'
    case_probe.mkdir()
    insensitive = (tmp_path / 'caseprobe').exists()
    case_probe.rmdir()
    if not insensitive:
        pytest.skip('requires a case-insensitive filesystem')
    wiki = _make_wiki(tmp_path, folders={'core': []})
    (tmp_path / 'core' / 'page.md').write_text(
        '# page\n\nScopetoken prose.\n',
        encoding='utf-8',
    )

    # search scopes through the SQL prefix filter, keyed on true casing
    matches = wiki.search('scopetoken', name='CORE')
    assert [path for path, _, _ in matches] == ['core/page.md']
    # match reports paths in the on-disk casing
    reported = wiki.match('Scopetoken', name='CORE')
    assert [relpath for relpath, _, _ in reported] == ['core/page.md']


def test_search_does_not_reread_unchanged_unreadable_pages(
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
        encoding: Optional[str] = None,
        errors: Optional[str] = None,
    ) -> str:
        nonlocal attempts
        if path == unreadable:
            attempts += 1
            raise OSError('unreadable')
        return original_read_text(path, encoding=encoding, errors=errors)

    monkeypatch.setattr(pathlib.Path, 'read_text', read_text)
    assert wiki.search('hidden') == []
    assert wiki.search('hidden') == []
    assert attempts == 1


def test_search_notices_unreadable_pages_per_read_attempt(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed refresh read fires the typed skip notice, once per attempt.

    The notice rides the read attempt itself: a query over a standing
    tombstone never re-reads the page, so it draws no repeat notice.
    """
    wiki = _make_wiki(tmp_path, folders={'core': []})
    unreadable = tmp_path / 'core' / 'unreadable.md'
    unreadable.write_text('# unreadable\n\nHidden token.\n', encoding='utf-8')
    original_read_text = pathlib.Path.read_text

    def read_text(
        path: pathlib.Path,
        encoding: Optional[str] = None,
        errors: Optional[str] = None,
    ) -> str:
        if path == unreadable:
            raise OSError('unreadable')
        return original_read_text(path, encoding=encoding, errors=errors)

    monkeypatch.setattr(pathlib.Path, 'read_text', read_text)
    notices = _capture_notices(wiki)

    # the attempted-and-failed read narrates the skip with its path
    assert wiki.search('hidden') == []
    skips = [event for event in notices if type(event).__name__ == 'ReadSkipEvent']
    assert [event.path for event in skips] == ['core/unreadable.md']
    assert 'core/unreadable.md' in skips[0].description
    # the tombstone keeps the next query off the read path -- no repeat notice
    notices.clear()
    assert wiki.search('hidden') == []
    assert not any(type(event).__name__ == 'ReadSkipEvent' for event in notices)


def test_search_retries_indexed_pages_after_a_transient_read_failure(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed re-read keeps serving the stale row and retries next query."""
    wiki = _make_wiki(tmp_path, folders={'core': []})
    page = tmp_path / 'core' / 'page.md'
    page.write_text('# page\n\nFirsttoken draft.\n', encoding='utf-8')
    assert wiki.search('firsttoken')[0][0] == 'core/page.md'

    page.write_text('# page\n\nSecondtoken revision.\n', encoding='utf-8')
    original_read_text = pathlib.Path.read_text

    def read_text(
        path: pathlib.Path,
        encoding: Optional[str] = None,
        errors: Optional[str] = None,
    ) -> str:
        if path == page:
            raise OSError('transient')
        return original_read_text(path, encoding=encoding, errors=errors)

    monkeypatch.setattr(pathlib.Path, 'read_text', read_text)
    assert wiki.search('firsttoken')[0][0] == 'core/page.md'
    assert wiki.search('secondtoken') == []

    monkeypatch.undo()
    assert wiki.search('secondtoken')[0][0] == 'core/page.md'


def test_search_rebuilds_a_corrupt_index(tmp_path: pathlib.Path) -> None:
    """A corrupt index file is discarded and rebuilt, never fatal."""
    wiki = _make_wiki(tmp_path, folders={'core': []})
    (tmp_path / 'core' / 'page.md').write_text(
        '# page\n\nCorrupttoken prose.\n',
        encoding='utf-8',
    )
    assert wiki.search('corrupttoken')[0][0] == 'core/page.md'

    cache = tmp_path / '.wiki' / 'cache' / 'search.db'
    cache.write_bytes(b'junk')
    assert wiki.search('corrupttoken')[0][0] == 'core/page.md'


def test_search_rebuilds_an_outdated_schema_cache(tmp_path: pathlib.Path) -> None:
    """A cache stamped with an older schema version rebuilds on the next query."""
    wiki = _make_wiki(tmp_path, folders={'core': []})
    (tmp_path / 'core' / 'page.md').write_text(
        '# page\n\nVintagetoken prose.\n',
        encoding='utf-8',
    )
    assert wiki.search('vintagetoken')[0][0] == 'core/page.md'

    connection = sqlite3.connect(tmp_path / '.wiki' / 'cache' / 'search.db')
    with connection:
        connection.execute("UPDATE meta SET value = '1' WHERE key = 'schema_version'")
    connection.close()
    assert wiki.search('vintagetoken')[0][0] == 'core/page.md'
