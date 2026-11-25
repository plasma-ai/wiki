"""Test the ``parity.cache`` module.

The self-check doctors a real engine's caches for a file mode, a counts
entry, the index rows and the index schema; this module carries the
parts of the cache rule no doctored check reaches, on caches built by
hand.
"""

from __future__ import annotations

import json
import os
import pathlib
import sqlite3

import pytest

from parity import cache, manifest

__all__ = ['test_cache_rule_reports_its_difference']

# when each side wrote its page, in nanoseconds: two different moments
_STAMPS = (1_700_000_000_000_000_000, 1_700_000_001_000_000_000)


@pytest.mark.parametrize(
    argnames=('rule', 'raw', 'expected'),
    argvalues=[
        ('none', False, []),
        ('none', True, ['word_counts.json: bytes']),
        ('gitignore', False, ['.gitignore']),
        ('form', False, ['word_counts.json: form']),
        (
            'json',
            False,
            [
                'word_counts.json: ',
                'word_counts.json: page.md',
                'word_counts.json: form',
            ],
        ),
        ('meta', False, ['search.db: meta']),
        (
            'database',
            False,
            [
                'search.db: meta',
                'search.db: schema: files',
                'search.db: schema: meta',
                'search.db: schema: notes_fts',
                'search.db: files: page.md',
                'search.db: notes_fts: page.md',
            ],
        ),
    ],
    ids=['current', 'bytes', 'gitignore', 'form', 'json', 'meta', 'database'],
)
def test_cache_rule_reports_its_difference(
    tmp_path: pathlib.Path,
    rule: str,
    raw: bool,
    expected: list[str],
) -> None:
    """Each part of the cache rule reports its own difference, and no other.

    Two sides wrote one page at different moments, and each cache records
    its own side's time, so the caches agree until their bytes are
    compared. One side then changes the bytes of ``.gitignore``, writes
    the same counts in another JSON form, writes counts that are no JSON,
    changes a ``meta`` row, or holds a ``search.db`` that is no database.
    """
    first = _side(tmp_path / 'first', stamp=_STAMPS[0])
    second = _side(tmp_path / 'second', stamp=_STAMPS[1])
    _change(second, rule)
    lines = cache.compare(_view(first), _view(second), raw=raw)
    assert lines == expected


# ------ helpers


def _side(root: pathlib.Path, *, stamp: int) -> pathlib.Path:
    """Build a wiki with one page and both caches, written at ``stamp``."""
    folder = root / cache.CACHE
    folder.mkdir(parents=True)
    page = root / 'page.md'
    page.write_bytes(b'One two three.\n')
    os.utime(page, ns=(stamp, stamp))
    status = page.stat()
    (folder / '.gitignore').write_bytes(b'*\n')
    counts = {'page.md': {'mtime': status.st_mtime, 'words': 3}}
    text = json.dumps(counts, indent=2, sort_keys=True) + '\n'
    (folder / 'word_counts.json').write_text(text, encoding='utf-8')
    connection = sqlite3.connect(folder / 'search.db')
    try:
        with connection:
            connection.execute('CREATE TABLE meta(key, value)')
            connection.execute('CREATE TABLE files(path, mtime_ns, size)')
            connection.execute(
                'CREATE TABLE notes_fts(title, desc, headings, tags, body, path, folder)'
            )
            connection.execute('INSERT INTO meta VALUES (?, ?)', ('schema', '1'))
            connection.execute(
                'INSERT INTO files VALUES (?, ?, ?)',
                ('page.md', status.st_mtime_ns, status.st_size),
            )
            connection.execute(
                'INSERT INTO notes_fts(body, path, folder) VALUES (?, ?, ?)',
                ('One two three.', 'page.md', '.'),
            )
    finally:
        connection.close()
    return root


def _change(root: pathlib.Path, rule: str) -> None:
    """Change the one thing ``rule`` names in the caches of the wiki at ``root``."""
    folder = root / cache.CACHE
    counts = folder / 'word_counts.json'
    if rule == 'gitignore':
        (folder / '.gitignore').write_bytes(b'*\n# changed\n')
    elif rule == 'form':
        data = json.loads(counts.read_text(encoding='utf-8'))
        counts.write_text(json.dumps(data), encoding='utf-8')
    elif rule == 'json':
        counts.write_bytes(b'{no json')
    elif rule == 'meta':
        connection = sqlite3.connect(folder / 'search.db')
        try:
            with connection:
                connection.execute('UPDATE meta SET value = ?', ('2',))
        finally:
            connection.close()
    elif rule == 'database':
        (folder / 'search.db').write_bytes(b'no database\n')


def _view(root: pathlib.Path) -> cache.View:
    """Read the caches of the wiki at ``root`` from a fresh manifest."""
    entries = manifest.scan(root)
    return cache.view(root, entries)
