"""Functions for copy-on-write snapshots of a checkout."""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
from collections.abc import Mapping

__all__ = [
    'snapshot',
    'clone',
    'refresh',
]


def snapshot(checkout: pathlib.Path, target: pathlib.Path) -> None:
    """Snapshot the checkout at ``checkout`` to the new path ``target``.

    Raises:
        ValueError: If the checkout's ``.git`` is not a folder (a linked
            worktree shares the live repository's state, so a copy of one
            is no snapshot), or if the checkout is no repository and
            ``target`` lies inside one (git would answer from that
            repository).

    """
    marker = checkout / '.git'
    if os.path.lexists(marker):
        if os.path.islink(marker) or not os.path.isdir(marker):
            raise ValueError(f'Refusing to snapshot {checkout}: .git is not a folder.')
    else:
        for parent in target.resolve().parents:
            if os.path.lexists(parent / '.git'):
                raise ValueError(
                    f'Refusing to snapshot {checkout} into {target}:'
                    f' {parent} is a repository and the checkout is not.'
                )
    clone(checkout, target)


def clone(source: pathlib.Path, target: pathlib.Path) -> None:
    """Clone the tree at ``source`` to the new path ``target``.

    Files are cloned copy-on-write where the filesystem allows. Files and
    folders keep their modes and modification times either way, so every
    clone of one snapshot shows a side the same tree.

    Raises:
        RuntimeError: If the copy fails.

    """
    # clonefile on macOS, a reflink where Linux has one, a plain copy otherwise
    if sys.platform == 'darwin':
        cmd = ['cp', '-Rpc', f'{source}', f'{target}']
    else:
        cmd = ['cp', '-a', '--reflink=auto', f'{source}', f'{target}']
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        error = result.stderr.strip()
        raise RuntimeError(f'cp failed (exit {result.returncode}): {error!r}')


def refresh(tree: pathlib.Path, *, env: Mapping[str, str]) -> None:
    """Refresh the git index of the clone at ``tree``, if it is a repository.

    A copy has new inodes, so every index entry is stale, and git would
    hash every tracked file again on each call a side makes.

    Raises:
        RuntimeError: If git cannot refresh the index.

    """
    if not os.path.isdir(tree / '.git'):
        return
    cmd = ['git', '-C', f'{tree}', 'update-index', '-q', '--refresh']
    result = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        error = result.stderr.strip()
        raise RuntimeError(
            f'git update-index failed (exit {result.returncode}): {error!r}'
        )
