"""Test the ``parity.binary`` module.

The binary cargo builds runs through the harness in the ``suite`` job, which
builds it first. This module carries the rules that make such a run mean
something. The Rust side states the checkout's version and commit, so a
binary of another commit or version proves nothing. The commit says nothing
of an edit that is not committed, so a binary older than a file it is built
from is refused, and what differs from the commit is listed.
"""

from __future__ import annotations

import pathlib
import time

import pytest

from parity import binary, side

from .conftest import NOW, VERSION, _build, _git, _needs_git, _workspace

__all__ = [
    'test_rust_side_holds_the_binary_to_the_checkout',
    'test_rust_side_holds_the_binary_to_the_tree_as_it_stands',
    'test_checkout_with_no_built_binary_has_no_rust_side',
]

pytestmark = _needs_git

# the source file, the test file and the embedded file of the checkout
_SOURCE = 'crates/wiki-cli/src/lib.rs'
_TEST = 'crates/wiki-cli/tests/it/main.rs'
_EMBEDDED = 'wiki/skills/wiki/SKILL.md'
# seconds no file system's clock stands still for
_TICK = 0.05


def test_rust_side_holds_the_binary_to_the_checkout(tmp_path: pathlib.Path) -> None:
    """A binary proves the Rust side only while the checkout is what it was built from.

    The side's identity comes from the checkout: the version its manifest
    states and the commit it is at. A binary built there proves it on a
    command. After one more commit, or under another version in the
    manifest, the same binary is a build of something else, and its command
    does not prove the side.
    """
    repo = _workspace(tmp_path)
    commit = _git(repo, 'rev-parse', 'HEAD')
    _build(repo)
    home = tmp_path / 'home'
    home.mkdir()
    # the binary is the build of this commit and this version
    rust = binary.side(repo)
    assert rust.name == binary.NAME
    assert rust.identity == {'engine': 'rust', 'version': VERSION, 'build': commit}
    env = side.environment(rust, home, now=NOW)
    outcome = side.run(rust, ['--version'], cwd=home, env=env)
    assert (outcome.stdout, outcome.code) == (f'{VERSION}\n'.encode(), 0)
    assert outcome.identity is True
    # the manifest states another version than the binary was built under
    manifest = repo / 'Cargo.toml'
    text = manifest.read_text(encoding='utf-8')
    manifest.write_text(text.replace(VERSION, '2.0.0-rc.1'), encoding='utf-8')
    rust = binary.side(repo)
    assert rust.identity['version'] == '2.0.0-rc.1'
    assert side.run(rust, ['--version'], cwd=home, env=env).identity is False
    manifest.write_text(text, encoding='utf-8')
    # the checkout moves on, and the binary is not built again
    _git(repo, 'commit', '-q', '--allow-empty', '-m', 'later')
    rust = binary.side(repo)
    assert rust.identity['build'] == _git(repo, 'rev-parse', 'HEAD') != commit
    assert side.run(rust, ['--version'], cwd=home, env=env).identity is False


def test_rust_side_holds_the_binary_to_the_tree_as_it_stands(
    tmp_path: pathlib.Path,
) -> None:
    """An edit that is not committed is seen, though the commit stays the same.

    The build id is the commit, so it cannot tell a build of the commit
    from a build over an edit. Two rules can. A binary older than a file
    cargo built it from is refused, with the command that builds it: a
    source file or an embedded file written since, an input that is gone,
    or no list of inputs at all. A test file is no input, so writing one
    refuses nothing. And whatever differs from the commit among the paths a
    build reads is listed, changed or new, whether or not the binary holds
    it.
    """
    repo = _workspace(tmp_path)
    _build(repo)
    assert binary.side(repo).name == binary.NAME
    assert binary.changed(repo) == []
    # a test file is written after the build: the binary is still its build
    _later(repo, _TEST)
    assert binary.side(repo).name == binary.NAME
    assert binary.changed(repo) == [_TEST]
    # a source file and an embedded file are, and each makes the binary stale
    for name in (_SOURCE, _EMBEDDED):
        _later(repo, name)
        with pytest.raises(ValueError, match=r'written after .*cargo build --release'):
            binary.side(repo)
        # a build over the edit is no build of the commit, and is listed
        _build(repo)
        assert binary.side(repo).name == binary.NAME
        assert name in binary.changed(repo)
    # a file is added below the embedded folder
    added = 'wiki/skills/wiki/more.md'
    _later(repo, added)
    with pytest.raises(ValueError, match='written after'):
        binary.side(repo)
    _build(repo)
    assert binary.changed(repo) == sorted([_SOURCE, _TEST, added, _EMBEDDED])
    # the edits are committed, and the binary of the edits is built again
    _git(repo, 'add', '-A')
    _git(repo, 'commit', '-q', '-m', 'edits')
    _build(repo)
    assert binary.side(repo).name == binary.NAME
    assert binary.changed(repo) == []
    # an input is gone, and then the list of inputs
    (repo / _SOURCE).unlink()
    with pytest.raises(ValueError, match='is gone'):
        binary.side(repo)
    (repo / 'target' / 'release' / 'wiki.d').unlink()
    with pytest.raises(ValueError, match='the list of its inputs, is no file'):
        binary.side(repo)


def test_checkout_with_no_built_binary_has_no_rust_side(tmp_path: pathlib.Path) -> None:
    """A checkout that was not built is refused, with the command that builds it.

    A debug build is no stand-in: the side is the release build, which is
    what ships.
    """
    repo = _workspace(tmp_path)
    debug = repo / 'target' / 'debug' / 'wiki'
    debug.parent.mkdir(parents=True)
    debug.write_text('', encoding='utf-8')
    with pytest.raises(ValueError, match='cargo build --release --locked'):
        binary.side(repo)


# ------ helpers


def _later(repo: pathlib.Path, name: str) -> None:
    """Write the file ``name`` of ``repo`` at a later time than its binary."""
    # NOTE: a file system may stamp two writes of one clock tick alike
    time.sleep(_TICK)
    (repo / name).write_text('written later\n', encoding='utf-8')
