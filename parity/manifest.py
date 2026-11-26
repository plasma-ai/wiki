"""Functions for tree manifests and touched sets."""

from __future__ import annotations

import concurrent.futures
import dataclasses
import hashlib
import os
import pathlib
import stat
from collections.abc import Mapping
from typing import Optional

__all__ = []

# files hashed at once: the digest releases the interpreter lock, so the
# hashes run in parallel; the count is a tuning choice with no constraint
# behind it -- more threads shorten a scan until the cores or the disk bound it
_HASH_THREADS = 8


@dataclasses.dataclass(frozen=True, slots=True)
class Entry:
    """One path of a tree, as a manifest lists it.

    The first five fields are the manifest: what two sides must agree on.
    The rest say whether the path was written since an earlier scan.

    Attributes:
        kind: ``file``, ``dir``, ``link`` or ``other``.
        mode: Permission bits.
        size: Byte size of a file, else ``0``.
        sha256: Digest of a file's content; ``None`` for another kind, and
            ``unreadable`` for a file that cannot be opened.
        target: Target of a symlink, else ``None``.
        inode: Inode number.
        mtime_ns: Modification time in nanoseconds.
        mtime: Modification time in seconds, as ``os.stat`` reports it.
        ctime_ns: Status-change time in nanoseconds.

    """

    kind: str
    mode: int
    size: int
    sha256: Optional[str]
    target: Optional[str]
    inode: int
    mtime_ns: int
    mtime: float
    ctime_ns: int

    @property
    def content(self: Entry) -> tuple:
        """Return the fields two sides must agree on."""
        return (self.kind, self.mode, self.size, self.sha256, self.target)


def scan(
    root: pathlib.Path,
    *,
    skip: frozenset[str] = frozenset(),
    previous: Optional[Mapping[str, Entry]] = None,
    origin: Optional[Mapping[str, Entry]] = None,
) -> dict[str, Entry]:
    """Return the manifest of every path below ``root``.

    With neither ``previous`` nor ``origin``, every file is hashed.

    Args:
        root: Folder to walk; symlinks are listed, never followed.
        skip: Paths relative to ``root`` to leave out, with all below them.
        previous: An earlier manifest of the same tree. A file whose inode,
            size, mode and both times are unchanged keeps its digest, since
            no write leaves the status-change time as it was.
        origin: The manifest of the tree this one was just cloned from. A
            file whose size, mode and modification time are the origin's
            keeps the origin's digest: a clone holds its source's bytes.

    Returns:
        Dict mapping each path relative to ``root`` to its entry.

    """
    result = {}
    pending = []
    # walk the tree
    stack = ['']
    while stack:
        folder = stack.pop()
        try:
            listing = list(os.scandir(os.path.join(root, folder)))
        except OSError:
            # an unlistable folder stands in the manifest with nothing below
            continue
        for item in listing:
            relpath = f'{folder}/{item.name}' if folder else item.name
            if relpath in skip:
                continue
            # build the entry
            status = item.stat(follow_symlinks=False)
            kind = _kind(status.st_mode)
            target = None
            if kind == 'link':
                target = os.readlink(item.path)
            elif kind == 'dir':
                stack.append(relpath)
            size = status.st_size if kind == 'file' else 0
            entry = Entry(
                kind=kind,
                mode=stat.S_IMODE(status.st_mode),
                size=size,
                sha256=None,
                target=target,
                inode=status.st_ino,
                mtime_ns=status.st_mtime_ns,
                mtime=status.st_mtime,
                ctime_ns=status.st_ctime_ns,
            )
            # keep a known digest
            if kind == 'file':
                digest = None
                if previous is not None:
                    known = previous.get(relpath)
                    if (known is not None) and (_signature(known) == _signature(entry)):
                        digest = known.sha256
                elif origin is not None:
                    known = origin.get(relpath)
                    if (known is not None) and (_cloned(known) == _cloned(entry)):
                        digest = known.sha256
                if digest is None:
                    pending.append(relpath)
                else:
                    entry = dataclasses.replace(entry, sha256=digest)
            result[relpath] = entry
    # hash the files that are new or were written since the previous scan
    with concurrent.futures.ThreadPoolExecutor(max_workers=_HASH_THREADS) as pool:
        paths = [os.path.join(root, relpath) for relpath in pending]
        digests = pool.map(_digest, paths)
        for relpath, digest in zip(pending, digests):
            result[relpath] = dataclasses.replace(result[relpath], sha256=digest)
    return result


def touched(before: Mapping[str, Entry], after: Mapping[str, Entry]) -> list[str]:
    """Return the paths a command touched between two scans.

    A path is touched when it was created or deleted, or when it is no
    folder and its inode or modification time changed. A folder's times
    move with every entry made in it, so they say nothing of their own.
    """
    result = []
    for relpath in sorted(set(before) | set(after)):
        old = before.get(relpath)
        new = after.get(relpath)
        if (old is None) or (new is None):
            result.append(relpath)
        elif (old.kind == 'dir') and (new.kind == 'dir'):
            continue
        elif (old.inode, old.mtime_ns) != (new.inode, new.mtime_ns):
            result.append(relpath)
    return result


def changes(
    before: Mapping[str, Entry],
    after: Mapping[str, Entry],
) -> dict[str, Optional[tuple]]:
    """Return the manifest content that differs between two scans.

    Returns:
        Dict mapping each path whose content fields changed to the new
        fields, or to ``None`` for a path that is gone.

    """
    result = {}
    for relpath in set(before) | set(after):
        old = before.get(relpath)
        new = after.get(relpath)
        if new is None:
            result[relpath] = None
        elif (old is None) or (old.content != new.content):
            result[relpath] = new.content
    return result


# ------ helper functions


def _kind(mode: int) -> str:
    """Return the manifest kind of a path with file mode ``mode``."""
    if stat.S_ISLNK(mode):
        return 'link'
    if stat.S_ISDIR(mode):
        return 'dir'
    if stat.S_ISREG(mode):
        return 'file'
    return 'other'


def _signature(entry: Entry) -> tuple:
    """Return the fields that move whenever a file is written."""
    return (entry.inode, entry.size, entry.mode, entry.mtime_ns, entry.ctime_ns)


def _cloned(entry: Entry) -> tuple:
    """Return the fields a clone of a file shares with its source."""
    return (entry.kind, entry.size, entry.mode, entry.mtime_ns)


def _digest(path: str) -> str:
    """Return the sha256 of the file at ``path``, or ``unreadable``."""
    try:
        with open(path, 'rb') as handle:
            return hashlib.file_digest(handle, 'sha256').hexdigest()
    except OSError:
        return 'unreadable'
