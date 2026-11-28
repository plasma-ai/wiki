"""Functions for the Rust side: the binary cargo builds from the tree.

The Rust engine is the ``wiki`` binary of a release build of the checkout
that holds the harness. Nothing here builds it: ``cargo build --release
--locked`` does, once, before a run. :func:`side` hands the binary to the
harness with the identity it must prove on every command: the version the
workspace's manifest states and the commit the checkout is at.
"""

from __future__ import annotations

import pathlib
import tomllib

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


def side(repo: pathlib.Path) -> Side:
    """Return the Rust side of the checkout ``repo`` for the harness to run.

    The side runs the binary by its resolved path. Its identity is read
    from the checkout and not from the binary, so a binary built at another
    commit, or under another version, fails every command it is given.

    Raises:
        ValueError: If the checkout holds no built binary.

    """
    binary = repo / _BINARY
    if not binary.is_file():
        raise ValueError(
            f'Side {NAME!r} is not built: {binary} is no file; run'
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
