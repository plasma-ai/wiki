"""Test the ``parity.manifest`` module.

File bytes, modes, extra files and rewrites are covered through the
self-check, which doctors each; this module carries the two manifest
fields no doctored check reaches: a symlink's target and the digest of a
file that cannot be opened.
"""

from __future__ import annotations

import pathlib

from parity import manifest

from .._helpers import _needs_unprivileged

__all__ = [
    'test_symlink_is_compared_by_its_target',
    'test_file_that_cannot_be_opened_is_listed_as_unreadable',
]


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
