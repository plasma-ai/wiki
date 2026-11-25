"""Test the ``parity.manifest`` module.

File bytes, modes, extra files and rewrites are covered through the
self-check, which doctors each; this module carries what no doctored
check reaches: each half of the touched rule by itself, a deleted path,
a symlink's target and the digest of a file that cannot be opened.
"""

from __future__ import annotations

import os
import pathlib

from parity import manifest

from .._helpers import _needs_unprivileged

__all__ = [
    'test_path_is_touched_by_a_new_time_a_new_inode_or_a_deletion',
    'test_symlink_is_compared_by_its_target',
    'test_file_that_cannot_be_opened_is_listed_as_unreadable',
]

# when a file was first written and when it was written again, in nanoseconds
_WRITTEN = 1_700_000_000_000_000_000
_LATER = 1_700_000_001_000_000_000


def test_path_is_touched_by_a_new_time_a_new_inode_or_a_deletion(
    tmp_path: pathlib.Path,
) -> None:
    """A file is touched by its time alone, by its inode alone, or by going away.

    One file is written in place with the same bytes at a later time, one
    is replaced by a copy that carries the old time, and one is deleted.
    All three are touched; only the deleted one changes the manifest, as
    a path that is gone.
    """
    # write three files at one fixed time
    for name in ('written.md', 'replaced.md', 'deleted.md'):
        (tmp_path / name).write_bytes(b'text\n')
        os.utime(tmp_path / name, ns=(_WRITTEN, _WRITTEN))
    before = manifest.scan(tmp_path)
    # write the same bytes in place at a later time: same inode, new mtime
    (tmp_path / 'written.md').write_bytes(b'text\n')
    os.utime(tmp_path / 'written.md', ns=(_LATER, _LATER))
    # move a copy over a file with its old time: new inode, same mtime
    copy = tmp_path / 'copy.md'
    copy.write_bytes(b'text\n')
    os.utime(copy, ns=(_WRITTEN, _WRITTEN))
    os.replace(copy, tmp_path / 'replaced.md')
    # delete the third file
    (tmp_path / 'deleted.md').unlink()
    after = manifest.scan(tmp_path)
    touched = manifest.touched(before, after)
    assert touched == ['deleted.md', 'replaced.md', 'written.md']
    assert manifest.changes(before, after) == {'deleted.md': None}


def test_symlink_is_compared_by_its_target(tmp_path: pathlib.Path) -> None:
    """A symlink pointed elsewhere is a changed and a touched path.

    The manifest lists a link by its target and never follows it, so two
    sides that leave one link at different targets differ at that path.
    """
    for name in ('first.md', 'second.md'):
        (tmp_path / name).write_bytes(b'text\n')
    link = tmp_path / 'link.md'
    link.symlink_to('first.md')
    before = manifest.scan(tmp_path)
    link.unlink()
    link.symlink_to('second.md')
    after = manifest.scan(tmp_path)
    changed = manifest.changes(before, after)
    assert list(changed) == ['link.md']
    kind, _, _, digest, target = after['link.md'].content
    assert (kind, digest, target) == ('link', None, 'second.md')
    assert manifest.touched(before, after) == ['link.md']


@_needs_unprivileged
def test_file_that_cannot_be_opened_is_listed_as_unreadable(
    tmp_path: pathlib.Path,
) -> None:
    """A file no side can open still stands in the manifest.

    Its digest reads ``unreadable`` in place of a hash, so a scan does not
    fail on it, and two sides agree on the path only while neither can
    open it.
    """
    page = tmp_path / 'page.md'
    page.write_bytes(b'text\n')
    page.chmod(0)
    entries = manifest.scan(tmp_path)
    assert entries['page.md'].sha256 == 'unreadable'
