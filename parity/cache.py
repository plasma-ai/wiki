"""Functions for comparing the two caches under ``.wiki/cache``."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import pathlib
import sqlite3
import tempfile
import unicodedata
from collections.abc import Mapping
from typing import Any, Optional

from . import snapshot
from .manifest import Entry

__all__ = [
    'CACHE',
    'View',
    'view',
    'compare',
]

#: the cache folder, relative to a wiki root
CACHE = '.wiki/cache'

_COUNTS = 'word_counts.json'
_SEARCH = 'search.db'
# the database and the write-ahead files SQLite keeps beside it
_SEARCH_FAMILY = (_SEARCH, f'{_SEARCH}-wal', f'{_SEARCH}-shm')
# stands for a recorded mtime that equals the file's mtime on its own side
_CURRENT = 'current'


@dataclasses.dataclass(frozen=True)
class View:
    """The caches of one wiki on one side, in the form two sides must share.

    A recorded mtime that equals the page's current mtime on its own side
    reads as ``current``, so two sides that wrote a page at different
    moments still compare equal; any other recorded mtime reads as itself.

    Attributes:
        files: Kind of the cache folder (under the empty name) and of each
            path below it.
        gitignore: Bytes of the folder's ``.gitignore``, if it has one.
        counts: Each key of ``word_counts.json`` mapped to its entry as
            JSON text; ``None`` when the file is absent.
        counts_form: Whether the file's bytes are the JSON form v1 writes
            for the value they hold.
        counts_raw: Digest of the file's bytes.
        search_signature: Identity of the ``search.db`` files the dump was
            read from, so an untouched database is not dumped twice.
        search_meta: Rows of ``meta``; ``None`` when the database is
            absent, and the error text for one that cannot be read.
        search_files: Each path of ``files`` mapped to its mtime and size
            as SQLite holds them.
        search_stamps: The same paths mapped to the compared mtime and size.
        search_rows: Each path of ``notes_fts`` mapped to the digest of its
            rows, without rowids.

    """

    files: Mapping[str, str]
    gitignore: Optional[bytes]
    counts: Optional[Mapping[str, str]]
    counts_form: Optional[bool]
    counts_raw: Optional[str]
    search_signature: tuple
    search_meta: Any
    search_files: Mapping[str, tuple]
    search_stamps: Mapping[str, tuple]
    search_rows: Mapping[str, str]


def view(
    wiki: pathlib.Path,
    entries: Mapping[str, Entry],
    *,
    previous: Optional[View] = None,
) -> View:
    """Read the caches of the wiki at ``wiki``.

    Args:
        wiki: Wiki root on disk.
        entries: Manifest of the wiki root, keyed by path relative to it.
        previous: The view read before the last command, whose dump of an
            untouched ``search.db`` is kept.

    Returns:
        The view of both caches.

    """
    cache = wiki / CACHE
    below = f'{CACHE}/'
    # the folder itself is listed, so an empty one differs from none
    files = {
        relpath[len(below) :]: entry.kind
        for relpath, entry in entries.items()
        if relpath.startswith(below) or (relpath == CACHE)
    }
    # cache keys are NFC, so pages are looked up by their NFC path
    pages = {
        unicodedata.normalize('NFC', relpath): entry
        for relpath, entry in entries.items()
        if entry.kind == 'file'
    }
    gitignore = None
    if files.get('.gitignore') == 'file':
        gitignore = (cache / '.gitignore').read_bytes()
    # word counts
    counts = None
    counts_form = None
    counts_raw = None
    if files.get(_COUNTS) == 'file':
        raw = (cache / _COUNTS).read_bytes()
        counts, counts_form = _read_counts(raw, pages)
        counts_raw = hashlib.sha256(raw).hexdigest()
    # search index, dumped again only when one of its files changed
    signature = tuple(
        (name, entry.inode, entry.size, entry.mtime_ns, entry.ctime_ns)
        for name in _SEARCH_FAMILY
        if (entry := entries.get(f'{below}{name}')) is not None
    )
    if (previous is not None) and (previous.search_signature == signature):
        search_meta = previous.search_meta
        search_files = previous.search_files
        search_rows = previous.search_rows
    elif files.get(_SEARCH) == 'file':
        search_meta, search_files, search_rows = _read_search(cache)
    else:
        search_meta, search_files, search_rows = None, {}, {}
    search_stamps = {}
    for relpath, (mtime_ns, size) in search_files.items():
        page = pages.get(relpath)
        if (page is not None) and (page.mtime_ns == mtime_ns):
            mtime_ns = _CURRENT
        search_stamps[relpath] = (mtime_ns, size)
    return View(
        files=files,
        gitignore=gitignore,
        counts=counts,
        counts_form=counts_form,
        counts_raw=counts_raw,
        search_signature=signature,
        search_meta=search_meta,
        search_files=search_files,
        search_stamps=search_stamps,
        search_rows=search_rows,
    )


def compare(first: View, second: View, *, raw: bool) -> list[str]:
    """Return what differs between the caches of two sides.

    Args:
        first: One side's view.
        second: The other side's view.
        raw: Whether to compare the bytes of ``word_counts.json`` where
            its entries and form agree, which holds only while neither
            side has written a page.

    Returns:
        One line per difference, naming the cache and the key.

    """
    result = []
    for name in _unequal(first.files, second.files):
        result.append(f'file list: {name or "."}')
    if first.gitignore != second.gitignore:
        result.append('.gitignore')
    # word counts: keys and entries, then the form; the bytes only where
    # those agree, since bytes that hold different entries always differ
    keys = _unequal(first.counts or {}, second.counts or {})
    counts = [f'{_COUNTS}: {key}' for key in keys]
    if first.counts_form != second.counts_form:
        counts.append(f'{_COUNTS}: form')
    if raw and (not counts) and (first.counts_raw != second.counts_raw):
        counts.append(f'{_COUNTS}: bytes')
    result.extend(counts)
    # search index: meta as it is, then both tables by path
    if first.search_meta != second.search_meta:
        result.append(f'{_SEARCH}: meta')
    for relpath in _unequal(first.search_stamps, second.search_stamps):
        result.append(f'{_SEARCH}: files: {relpath}')
    for relpath in _unequal(first.search_rows, second.search_rows):
        result.append(f'{_SEARCH}: notes_fts: {relpath}')
    return result


# ------ helper functions


def _read_counts(
    raw: bytes,
    pages: Mapping[str, Entry],
) -> tuple[dict[str, str], bool]:
    """Return the compared entries of ``word_counts.json`` and its form check."""
    try:
        data = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        # no value to compare: the bytes stand in for it
        return {'': hashlib.sha256(raw).hexdigest()}, False
    form = raw == (json.dumps(data, indent=2, sort_keys=True) + '\n').encode('utf-8')
    if not isinstance(data, dict):
        return {'': json.dumps(data, sort_keys=True)}, form
    result = {}
    for key, entry in data.items():
        page = pages.get(key)
        if isinstance(entry, dict) and (page is not None):
            if entry.get('mtime') == page.mtime:
                entry = {**entry, 'mtime': _CURRENT}
        result[key] = json.dumps(entry, sort_keys=True)
    return result, form


def _read_search(cache: pathlib.Path) -> tuple[Any, dict[str, tuple], dict[str, str]]:
    """Return the ``meta`` rows, ``files`` rows and row digests of ``search.db``."""
    # read a copy: opening the database itself would write beside it
    with tempfile.TemporaryDirectory() as scratch:
        for name in _SEARCH_FAMILY:
            if os.path.isfile(cache / name):
                snapshot.clone(cache / name, pathlib.Path(scratch) / name)
        connection = sqlite3.connect(os.path.join(scratch, _SEARCH))
        try:
            meta = connection.execute('SELECT key, value FROM meta').fetchall()
            files = {
                relpath: (mtime_ns, size)
                for relpath, mtime_ns, size in connection.execute(
                    'SELECT path, mtime_ns, size FROM files'
                )
            }
            # a path may hold several rows, so each digest covers a sorted run
            rows: dict[str, list] = {}
            cursor = connection.execute(
                'SELECT path, title, desc, headings, tags, body, folder FROM notes_fts'
            )
            for row in cursor:
                rows.setdefault(row[0], []).append(row)
        except sqlite3.DatabaseError as e:
            return f'{type(e).__name__}: {e}', {}, {}
        finally:
            connection.close()
    digests = {}
    for relpath, group in rows.items():
        text = repr(sorted(group, key=repr))
        digest = hashlib.sha256(text.encode('utf-8', 'surrogatepass'))
        digests[relpath] = digest.hexdigest()
    return sorted(meta), files, digests


def _unequal(first: Mapping[str, Any], second: Mapping[str, Any]) -> list[str]:
    """Return the sorted keys the two mappings hold differently."""
    keys = set(first) | set(second)
    missing = object()
    return sorted(
        key for key in keys if first.get(key, missing) != second.get(key, missing)
    )
