"""Functions for copy-on-write snapshots of a checkout."""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import typing
from collections.abc import Mapping
from typing import Literal, Optional

__all__ = []


def snapshot(checkout: pathlib.Path, target: pathlib.Path) -> None:
    """Snapshot the checkout at ``checkout`` to the new path ``target``.

    Raises:
        ValueError: If :func:`validate` refuses the checkout or the target.

    """
    validate(checkout, target)
    clone(checkout, target)


def validate(checkout: pathlib.Path, target: pathlib.Path) -> None:
    """Refuse a checkout that cannot be snapshotted to ``target``; copy nothing.

    Raises:
        ValueError: If the checkout is not a folder (a symlink to one is
            copied as a link to the live folder), if its ``.git`` is not a
            folder (a linked worktree shares the live repository's state,
            so a copy of one is no snapshot), or if the checkout is no
            repository and ``target`` lies inside one (git would answer
            from that repository).

    """
    if os.path.islink(checkout) or not os.path.isdir(checkout):
        raise ValueError(f'Refusing to snapshot {checkout}: it is not a folder.')
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


def confine(path: pathlib.Path, tree: pathlib.Path) -> None:
    """Refuse a path that leads out of the tree at ``tree``.

    A clone keeps the target of a symlink as it is written, so a write
    through a link that points out of the clone changes another tree: the
    checkout the clone was taken from, where the target is absolute.

    Raises:
        ValueError: If ``path`` lies outside ``tree`` once every symlink on
            it is resolved.

    """
    held = pathlib.Path(os.path.realpath(tree))
    resolved = pathlib.Path(os.path.realpath(path))
    if not resolved.is_relative_to(held):
        raise ValueError(
            f'Refusing to write {path}: it leads out of {tree}, to {resolved}.'
        )


def refresh(tree: pathlib.Path, *, env: Mapping[str, str]) -> None:
    """Refresh the git index of the clone at ``tree``, if it is a repository.

    A copy has new inodes, so every index entry is stale, and git would
    hash every tracked file again on each call a side makes.

    Raises:
        RuntimeError: If git cannot refresh the index.

    """
    if not os.path.isdir(tree / '.git'):
        return
    _git(['update-index', '-q', '--refresh'], cwd=tree, env=env)


# ------ helper functions


@typing.overload
def _git(
    cmd: list[str],
    *,
    cwd: Optional[pathlib.Path] = None,
    env: Optional[Mapping[str, str]] = None,
    check: Literal[True] = True,
) -> str: ...


@typing.overload
def _git(
    cmd: list[str],
    *,
    cwd: Optional[pathlib.Path] = None,
    env: Optional[Mapping[str, str]] = None,
    check: Literal[False],
) -> Optional[str]: ...


def _git(
    cmd: list[str],
    *,
    cwd: Optional[pathlib.Path] = None,
    env: Optional[Mapping[str, str]] = None,
    check: bool = True,
) -> Optional[str]:
    """Run a git command and return stripped stdout.

    Args:
        cmd: Git subcommand and arguments (without ``git`` prefix).
        cwd: Working directory for the command.
        env: Full environment for the command: the side's built one for a
            clone's index refresh, so git reads the config that side's own
            git calls read. ``None`` runs under the caller's environment,
            as a query about the host does.
        check: Raise ``RuntimeError`` on non-zero exit.

    Returns:
        Stripped stdout string, or ``None`` on non-zero exit or a missing
        git binary when ``check`` is ``False``.

    """
    full_cmd = ['git']
    if cwd:
        full_cmd.extend(['-C', f'{cwd}'])
    full_cmd.extend(cmd)
    # a missing git binary is treated like a failed command, so a query that
    # passes check=False degrades to a clean no-op
    try:
        result = subprocess.run(full_cmd, env=env, capture_output=True, text=True)
    except FileNotFoundError as e:
        if not check:
            return None
        cmd_string = ' '.join(cmd)
        raise RuntimeError(f'git {cmd_string} failed: {e}') from e
    if result.returncode != 0:
        if check:
            cmd_string = ' '.join(cmd)
            error = result.stderr.strip()
            raise RuntimeError(
                f'git {cmd_string} failed (exit {result.returncode}): {error!r}'
            )
        return None
    return result.stdout.strip()
