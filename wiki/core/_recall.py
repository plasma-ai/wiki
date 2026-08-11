"""SQLite FTS5 index for ranked wiki recall."""

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

_CACHE_NAME = 'recall.db'
_SCHEMA_VERSION = '1'
_HEADING = re.compile(r'^#{1,6}\s+(.*)$', re.MULTILINE)


def fts5_available() -> bool:
    """Return whether the running Python's SQLite exposes FTS5."""
    try:
        connection = sqlite3.connect(':memory:')
        connection.execute('CREATE VIRTUAL TABLE probe USING fts5(text)')
        connection.close()
        return True
    except sqlite3.OperationalError:
        return False


def recall(
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
    if not fts5_available():
        raise RuntimeError("This Python's sqlite3 module lacks FTS5 support.")
    if limit < 1:
        raise ValueError('limit must be at least 1')
    expression = _match_expression(query, prefix=prefix, tag=tag, raw=raw)

    connection = _open(root)
    try:
        _refresh(connection, root, files)
        sql = (
            'SELECT path, '
            "snippet(notes_fts, 3, '>>', '<<', '...', 12), "
            'bm25(notes_fts, 10.0, 4.0, 6.0, 1.0) '
            'FROM notes_fts WHERE notes_fts MATCH ?'
        )
        parameters: list[object] = [expression]
        if folder != root:
            relative = _relative(folder, root)
            prefix_path = relative.rstrip('/') + '/'
            sql += ' AND (folder = ? OR substr(folder, 1, ?) = ?)'
            parameters.extend((relative, len(prefix_path), prefix_path))
        sql += ' ORDER BY bm25(notes_fts, 10.0, 4.0, 6.0, 1.0) LIMIT ?'
        parameters.append(limit)
        try:
            rows = connection.execute(sql, parameters).fetchall()
        except sqlite3.OperationalError as e:
            raise ValueError(str(e)) from e
    finally:
        connection.close()

    return [
        (path, ' '.join(snippet.split()), round(-rank, 3))
        for path, snippet, rank in rows
    ]


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
    connection.execute('PRAGMA journal_mode=WAL')
    connection.execute('PRAGMA busy_timeout=5000')
    return connection


def _refresh(
    connection: sqlite3.Connection,
    root: pathlib.Path,
    files: Iterable[pathlib.Path],
) -> None:
    """Incrementally refresh rows whose mtime or size changed."""
    _ensure_schema(connection)
    present = {}
    for path in sorted(set(files)):
        try:
            stat = path.stat()
        except OSError:
            continue
        relative = _relative(path, root)
        present[relative] = (path, stat.st_mtime_ns, stat.st_size)

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

    with connection:
        for relative in removed:
            connection.execute('DELETE FROM notes_fts WHERE path = ?', (relative,))
            connection.execute('DELETE FROM files WHERE path = ?', (relative,))
        for relative in changed:
            path, mtime_ns, size = present[relative]
            # read before mutating: a failed read leaves an indexed page
            # serving its stale row, to be retried on the next refresh
            try:
                text = path.read_text(encoding='utf-8')
            except (OSError, UnicodeDecodeError):
                # only a never-indexed page earns a tombstone signature, so
                # the unreadable file is not re-read on every query
                if relative not in indexed:
                    connection.execute(
                        'INSERT INTO files(path, mtime_ns, size) VALUES (?, ?, ?)',
                        (relative, mtime_ns, size),
                    )
                continue
            title, headings, tags, body = _fields(path, text)
            folder = str(pathlib.PurePath(relative).parent)
            connection.execute('DELETE FROM notes_fts WHERE path = ?', (relative,))
            connection.execute(
                'INSERT INTO files(path, mtime_ns, size) VALUES (?, ?, ?) '
                'ON CONFLICT(path) DO UPDATE SET '
                'mtime_ns=excluded.mtime_ns, size=excluded.size',
                (relative, mtime_ns, size),
            )
            connection.execute(
                'INSERT INTO notes_fts(title, headings, tags, body, path, folder) '
                'VALUES (?, ?, ?, ?, ?, ?)',
                (title, headings, tags, body, relative, folder),
            )


def _ensure_schema(connection: sqlite3.Connection) -> None:
    """Create the FTS schema, rebuilding derived rows after a version change."""
    connection.execute(
        'CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)'
    )
    version = connection.execute(
        "SELECT value FROM meta WHERE key = 'schema_version'"
    ).fetchone()
    if version and version[0] != _SCHEMA_VERSION:
        connection.execute('DROP TABLE IF EXISTS notes_fts')
        connection.execute('DROP TABLE IF EXISTS files')
    connection.execute(
        'CREATE TABLE IF NOT EXISTS files ('
        'path TEXT PRIMARY KEY, mtime_ns INTEGER NOT NULL, size INTEGER NOT NULL)'
    )
    connection.execute(
        'CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5('
        'title, headings, tags, body, path UNINDEXED, folder UNINDEXED, '
        'tokenize="unicode61 remove_diacritics 2", prefix="2 3")'
    )
    connection.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
        'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
        (_SCHEMA_VERSION,),
    )
    connection.commit()


def _fields(path: pathlib.Path, text: str) -> tuple[str, str, str, str]:
    """Extract weighted fields from one Markdown page."""
    frontmatter, body = format.parse_page(text)
    values = [path.stem]
    if frontmatter:
        name = format.read_frontmatter_name(frontmatter)
        title = format.read_frontmatter_title(frontmatter)
        for value in (name, title):
            if value and value not in values:
                values.append(value)
        tags = format.read_frontmatter_field(frontmatter, 'tags') or ''
    else:
        tags = ''
    headings = ' '.join(_HEADING.findall(body))
    return ' '.join(values), headings, tags, body


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
            raise ValueError('prefix cannot be combined with a raw query')
        expression = query.strip()
        if not expression:
            raise ValueError('empty query')
    else:
        terms = query.split()
        if not terms:
            raise ValueError('empty query')
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
    relative = path.relative_to(root).as_posix()
    return unicodedata.normalize('NFC', relative)
