"""Functions for the Rust side: the binary cargo builds from the tree.

The Rust engine is the ``wiki`` binary of a release build of the checkout
that holds the harness. Nothing here builds it: ``cargo build --release
--locked`` does, once, before a run. :func:`side` hands the binary to the
harness with the identity it must prove on every command: the version the
workspace's manifest states and the commit the checkout is at.

The commit says nothing of an edit that is not committed, so two more rules
hold the binary to the tree as it stands. :func:`side` refuses a binary that
is older than a file cargo built it from. :func:`changed` lists what differs
from the commit among the paths a build reads: a gate refuses a checkout
that has any, and every other run records and prints them.
"""

from __future__ import annotations

import pathlib
import re
import tomllib
from typing import Optional

from . import oracle
from .side import Side

__all__ = []

#: the name of the Rust side, which no sides file may declare
NAME = 'rust'
#: the name of the engine in the identity record the binary writes
ENGINE = 'rust'

# the binary of a release build, relative to the checkout
_BINARY = pathlib.PurePath('target', 'release', 'wiki')
# the workspace's manifest, which states the version the binary prints
_MANIFEST = 'Cargo.toml'
# the paths of the checkout that a build of the binary reads
_SOURCES = (
    'crates',
    'Cargo.toml',
    'Cargo.lock',
    'rust-toolchain.toml',
    'wiki/_assets',
    'wiki/skills',
)
# the space that parts two paths of cargo's list, which escapes one in a path
_BREAK = re.compile(r'(?<!\\) ')


def side(repo: pathlib.Path) -> Side:
    """Return the Rust side of the checkout ``repo`` for the harness to run.

    The side runs the binary by its resolved path. Its identity is read
    from the checkout and not from the binary, so a binary built at another
    commit, or under another version, fails every command it is given.

    Raises:
        ValueError: If the checkout holds no built binary, or one that is
            older than a file cargo built it from.

    """
    binary = repo / _BINARY
    if not binary.is_file():
        raise ValueError(
            f'Side {NAME!r} is not built: {binary} is no file; run'
            f' `cargo build --release --locked` in {repo}.'
        )
    stale = _stale(binary)
    if stale is not None:
        raise ValueError(
            f'Side {NAME!r} is no build of the tree as it stands: {stale}; run'
            f' `cargo build --release --locked` in {repo}.'
        )
    manifest = tomllib.loads((repo / _MANIFEST).read_text(encoding='utf-8'))
    return Side(
        name=NAME,
        argv=(f'{binary.resolve()}',),
        identity={
            'engine': ENGINE,
            'version': manifest['workspace']['package']['version'],
            'build': oracle._git(['rev-parse', 'HEAD'], cwd=repo),
        },
    )


def changed(repo: pathlib.Path) -> list[str]:
    """Return the paths a build reads that differ from the commit of ``repo``.

    The binary's build id is the commit of ``HEAD``, whatever the tree held
    when cargo built it. So the id names what the binary is built from only
    while no file of the crates, of the workspace's manifest, lock and
    toolchain files, or of the embedded folders is changed, added or
    deleted.
    """
    tracked = ['--no-optional-locks', 'diff', '--name-only', 'HEAD']
    added = ['--no-optional-locks', 'ls-files', '--others', '--exclude-standard']
    paths: set[str] = set()
    for cmd in (tracked, added):
        paths.update(oracle._git([*cmd, '--', *_SOURCES], cwd=repo).splitlines())
    return sorted(paths)


# ------ helper functions


def _stale(binary: pathlib.Path) -> Optional[str]:
    """Return why ``binary`` is no build of its inputs as they stand, if it is none.

    cargo lists the files it built the binary from in a file beside it: the
    sources of the workspace's crates, their build scripts, the embedded
    files, and the files of the git folder that hold the commit. A test file
    is not among them. The binary is stale when the list is missing, or
    when a listed path is gone or was written after the binary: those are
    the cases in which cargo builds again, so a build always clears them.
    """
    listed = binary.with_name(f'{binary.name}.d')
    if not listed.is_file():
        return f'{listed}, the list of its inputs, is no file'
    built = binary.stat().st_mtime_ns
    _, *inputs = _BREAK.split(listed.read_text(encoding='utf-8').strip())
    for name in inputs:
        path = pathlib.Path(name.replace('\\ ', ' '))
        if not path.exists():
            return f'its input {path} is gone'
        if _written(path) > built:
            return f'its input {path} was written after {binary}'
    return None


def _written(path: pathlib.Path) -> int:
    """Return when ``path`` was last written, in nanoseconds.

    A folder stands for everything below it, as it does for cargo.
    """
    stamps = [path.stat().st_mtime_ns]
    if path.is_dir():
        stamps.extend(item.lstat().st_mtime_ns for item in path.rglob('*'))
    return max(stamps)
