"""Functions for the SQLite FTS5 search index."""

from __future__ import annotations

import pathlib
import re
import sqlite3
import unicodedata
from collections.abc import Iterable

import wiki.util
from wiki.constants import WIKI_CACHE

from . import format

__all__ = []

# one derived database per wiki, beside the other caches under the
# self-ignored .wiki/cache folder
_CACHE_NAME = 'search.db'
# any change to the notes_fts columns, tokenizer, or field extraction
# must bump this so stale indexes drop and rebuild on the next query
_SCHEMA_VERSION = '2'
# ATX headings at any depth, harvested into the weighted headings field
_HEADING = re.compile(r'^#{1,6}\s+(.*)$', re.MULTILINE)
# BM25 weights for (title, desc, headings, tags, body): curated metadata
# names a page, so title outranks desc, desc outranks tags and headings,
# and every field outranks body prose
_BM25_WEIGHTS = '10.0, 8.0, 4.0, 6.0, 1.0'
# snippet framing: matched terms render as >>term<< inside a 12-token
# body window, with '...' marking elided context
_SNIPPET_START = '>>'
_SNIPPET_END = '<<'
_SNIPPET_ELLIPSIS = '...'
_SNIPPET_TOKENS = 12
# parallel wiki commands share the derived index, and a full rebuild
# after a schema bump can hold the write lock well past sqlite3's 5s
# default, so a peer outwaits the rebuild instead of erroring
_BUSY_TIMEOUT_MS = 30_000
# raised at both the raw and safe query gates, so the text stays identical
_EMPTY_QUERY = 'Please provide a non-empty query.'


def search(
    root: pathlib.Path,
    files: Iterable[pathlib.Path],
    query: str,
    *,
    folder: pathlib.Path,
    limit: int = 10,
    prefix: bool = False,
    tag: str = '',
    raw: bool = False,
) -> list[tuple[str, str, float]]:
    """Refresh the derived index and return ranked full-text matches.

    Args:
        root: Wiki root.
        files: Markdown files visible to the wiki walk.
        query: Search terms, or an FTS5 expression when ``raw`` is set.
        folder: Resolved subtree scope.
        limit: Maximum number of matches.
        prefix: Treat the final safe-query term as a prefix.
        tag: Require this frontmatter tag token.
        raw: Pass ``query`` through as FTS5 syntax.

    Returns:
        ``(relative_path, snippet, score)`` tuples ordered by relevance.

    Raises:
        RuntimeError: If SQLite was built without FTS5.
        ValueError: If the query or limit is invalid.

    """
    # validate the environment, limit, and query
    if not _has_fts5():
        raise RuntimeError("This Python's sqlite3 module lacks FTS5 support.")
    if limit < 1:
        raise ValueError(f'Limit must be at least 1, got {limit!r} instead.')
    expression = _match_expression(query, prefix=prefix, tag=tag, raw=raw)

    # refresh the index, then rank matches within the scope
    connection = _open(root)
    try:
        _refresh(connection, root, files)
        # select weighted-BM25-ranked rows with a snippet of the body
        # (only trusted module constants interpolate; values bind via ?)
        sql = (
            'SELECT path, '
            f"snippet(notes_fts, 4, '{_SNIPPET_START}', '{_SNIPPET_END}', "
            f"'{_SNIPPET_ELLIPSIS}', {_SNIPPET_TOKENS}), "
            f'bm25(notes_fts, {_BM25_WEIGHTS}) '
            'FROM notes_fts WHERE notes_fts MATCH ?'
        )
        parameters: list[object] = [expression]
        # scope to the subtree: the folder itself or any folder below it
        if folder != root:
            relpath = _relative(folder, root)
            prefix_path = relpath.rstrip('/') + '/'
            sql += ' AND (folder = ? OR substr(folder, 1, ?) = ?)'
            parameters.extend((relpath, len(prefix_path), prefix_path))
        sql += f' ORDER BY bm25(notes_fts, {_BM25_WEIGHTS}) LIMIT ?'
        parameters.append(limit)
        # execute the ranked query
        try:
            rows = connection.execute(sql, parameters).fetchall()
        except sqlite3.OperationalError as e:
            raise ValueError(f'Invalid FTS5 query {expression!r}: {e}') from e
    finally:
        connection.close()

    # collapse snippet whitespace and negate rank into a descending score
    return [(path, ' '.join(snippet.split()), -rank) for path, snippet, rank in rows]


# ------ helper functions


def _has_fts5() -> bool:
    """Return ``True`` if the running Python's SQLite exposes FTS5."""
    connection = sqlite3.connect(':memory:')
    try:
        connection.execute('CREATE VIRTUAL TABLE probe USING fts5(text)')
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        connection.close()


def _open(root: pathlib.Path) -> sqlite3.Connection:
    """Open the self-ignored derived index under ``.wiki/cache``."""
    cache = root / WIKI_CACHE
    cache.mkdir(parents=True, exist_ok=True)
    gitignore = cache / '.gitignore'
    if not gitignore.exists():
        wiki.util.fs.write_atomic(gitignore, '*\n')
    try:
        return _connect(cache / _CACHE_NAME)
    except sqlite3.DatabaseError:
        # a corrupt index is derived state: discard it and rebuild once,
        # the same contract the word-counts cache honors
        for suffix in ('', '-wal', '-shm'):
            (cache / (_CACHE_NAME + suffix)).unlink(missing_ok=True)
        return _connect(cache / _CACHE_NAME)


def _connect(path: pathlib.Path) -> sqlite3.Connection:
    """Open the index database and apply its connection pragmas."""
    connection = sqlite3.connect(path)
    # close the handle when a pragma rejects the file (corruption), so
    # _open's discard-and-rebuild path never unlinks under an open handle
    try:
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute(f'PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}')
    except sqlite3.DatabaseError:
        connection.close()
        raise
    return connection


def _refresh(
    connection: sqlite3.Connection,
    root: pathlib.Path,
    files: Iterable[pathlib.Path],
) -> None:
    """Incrementally refresh rows whose mtime or size changed."""
    # take the write lock up front: the schema gate, the signature diff,
    # and the row writes are one read-decide-write transition, so two
    # concurrent refreshes serialize instead of interleaving
    connection.execute('BEGIN IMMEDIATE')
    with connection:
        _ensure_schema(connection)
        # stat every visible page
        present = {}
        for path in sorted(set(files)):
            try:
                stat = path.stat()
            except OSError:
                continue
            relpath = _relative(path, root)
            present[relpath] = (path, stat.st_mtime_ns, stat.st_size)
        # diff the indexed signatures against disk
        indexed = {
            path: (mtime_ns, size)
            for path, mtime_ns, size in connection.execute(
                'SELECT path, mtime_ns, size FROM files'
            )
        }
        removed = set(indexed) - set(present)
        changed = {
            path
            for path, (_, mtime_ns, size) in present.items()
            if indexed.get(path) != (mtime_ns, size)
        }
        # drop rows for removed pages
        for relpath in removed:
            connection.execute('DELETE FROM notes_fts WHERE path = ?', (relpath,))
            connection.execute('DELETE FROM files WHERE path = ?', (relpath,))
        # reindex changed pages
        for relpath in changed:
            path, mtime_ns, size = present[relpath]
            # read before mutating: a failed read leaves an indexed page
            # serving its stale row, to be retried on the next refresh
            try:
                text = path.read_text(encoding='utf-8')
            except (OSError, UnicodeDecodeError):
                # only a never-indexed page earns a tombstone signature, so
                # the unreadable file is not re-read on every query
                if relpath not in indexed:
                    connection.execute(
                        'INSERT INTO files(path, mtime_ns, size) VALUES (?, ?, ?)',
                        (relpath, mtime_ns, size),
                    )
                continue
            title, desc, headings, tags, body = _read_fields(path, text)
            folder = str(pathlib.PurePath(relpath).parent)
            connection.execute('DELETE FROM notes_fts WHERE path = ?', (relpath,))
            connection.execute(
                'INSERT INTO files(path, mtime_ns, size) VALUES (?, ?, ?) '
                'ON CONFLICT(path) DO UPDATE SET '
                'mtime_ns=excluded.mtime_ns, size=excluded.size',
                (relpath, mtime_ns, size),
            )
            connection.execute(
                'INSERT INTO notes_fts'
                '(title, desc, headings, tags, body, path, folder) '
                'VALUES (?, ?, ?, ?, ?, ?, ?)',
                (title, desc, headings, tags, body, relpath, folder),
            )


def _ensure_schema(connection: sqlite3.Connection) -> None:
    """Create the FTS schema, rebuilding derived rows after a version change."""
    # read the recorded schema version (creating meta on first contact)
    connection.execute(
        'CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)'
    )
    cursor = connection.execute("SELECT value FROM meta WHERE key = 'schema_version'")
    rows = cursor.fetchall()
    version = rows[0][0] if rows else None
    # a version change invalidates every derived row: drop and rebuild
    if (version is not None) and (version != _SCHEMA_VERSION):
        connection.execute('DROP TABLE IF EXISTS notes_fts')
        connection.execute('DROP TABLE IF EXISTS files')
    # create the signature and FTS tables, then stamp the running version
    connection.execute(
        'CREATE TABLE IF NOT EXISTS files ('
        'path TEXT PRIMARY KEY, mtime_ns INTEGER NOT NULL, size INTEGER NOT NULL)'
    )
    connection.execute(
        'CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5('
        'title, desc, headings, tags, body, path UNINDEXED, folder UNINDEXED, '
        'tokenize="unicode61 remove_diacritics 2", prefix="2 3")'
    )
    connection.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
        'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
        (_SCHEMA_VERSION,),
    )


def _read_fields(path: pathlib.Path, text: str) -> tuple[str, str, str, str, str]:
    """Extract weighted fields from one Markdown page."""
    frontmatter, body = format.parse_page(text)
    values = [path.stem]
    if frontmatter:
        name = format.read_frontmatter_name(frontmatter)
        title = format.read_frontmatter_title(frontmatter)
        for value in (name, title):
            if value and (value not in values):
                values.append(value)
        desc = format.read_frontmatter_desc(frontmatter) or ''
        tags = format.read_frontmatter_field(frontmatter, 'tags') or ''
    else:
        desc = ''
        tags = ''
    headings = ' '.join(_HEADING.findall(body))
    return ' '.join(values), desc, headings, tags, body


def _match_expression(
    query: str,
    *,
    prefix: bool,
    tag: str,
    raw: bool,
) -> str:
    """Build an FTS5 expression, quoting ordinary user terms."""
    if raw:
        if prefix:
            raise ValueError('--prefix and --raw are mutually exclusive.')
        expression = query.strip()
        if not expression:
            raise ValueError(_EMPTY_QUERY)
    else:
        terms = query.split()
        if not terms:
            raise ValueError(_EMPTY_QUERY)
        quoted = ['"' + term.replace('"', '""') + '"' for term in terms]
        if prefix:
            quoted[-1] += '*'
        expression = ' '.join(quoted)
    if tag:
        value = tag.replace('"', '""')
        expression = f'({expression}) AND tags:"{value}"'
    return expression


def _relative(path: pathlib.Path, root: pathlib.Path) -> str:
    """Return an NFC-normalized POSIX path relative to the wiki root."""
    relpath = path.relative_to(root).as_posix()
    return unicodedata.normalize('NFC', relpath)
