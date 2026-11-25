"""Test the ``parity.snapshot`` module.

A clone's fidelity is covered by every matrix run, whose manifests start
equal; this module carries the refusal that keeps a snapshot from
sharing a live repository's state.
"""

from __future__ import annotations

import pathlib

import pytest

from parity import snapshot

__all__ = ['test_linked_worktree_is_not_snapshotted']


def test_linked_worktree_is_not_snapshotted(tmp_path: pathlib.Path) -> None:
    """A checkout whose ``.git`` is a file is refused, and nothing is copied.

    A linked worktree keeps its index and refs in the main repository, so
    a git command in a copy of one would read and write live state.
    """
    checkout = tmp_path / 'worktree'
    checkout.mkdir()
    (checkout / '.git').write_text('gitdir: /elsewhere/.git/worktrees/x\n')
    with pytest.raises(ValueError, match=r'\.git is not a folder'):
        snapshot.snapshot(checkout, tmp_path / 'copy')
    assert not (tmp_path / 'copy').exists()
