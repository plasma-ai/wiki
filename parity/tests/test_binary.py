"""Test the ``parity.binary`` module.

The binary cargo builds runs through the harness in the ``suite`` job, which
builds it first. This module carries the rule that makes that run mean
something: the Rust side states the checkout's version and commit, so a
binary that was not built from the checkout as it stands proves nothing.
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

from parity import binary, side

from .conftest import NOW, _git, _needs_git

__all__ = [
    'test_rust_side_holds_the_binary_to_the_checkout',
    'test_checkout_with_no_built_binary_has_no_rust_side',
]

pytestmark = _needs_git

# the version the checkout's manifest states
_VERSION = '2.0.0-dev.1'
# a binary that leaves the identity record of its build, which a file beside
# it holds, and prints its version, as the Rust engine does
_BINARY = f"""#!{sys.executable}
import json, os, pathlib
record = json.loads(pathlib.Path(__file__).with_name('build.json').read_text())
with open(os.environ[{side.TRACE!r}], 'a') as handle:
    handle.write(json.dumps(record) + chr(10))
print(record['version'])
"""


def test_rust_side_holds_the_binary_to_the_checkout(tmp_path: pathlib.Path) -> None:
    """A binary proves the Rust side only while the checkout is what it was built from.

    The side's identity comes from the checkout: the version its manifest
    states and the commit it is at. A binary built there proves it on a
    command. After one more commit, or a change of the manifest's version,
    the same binary is a build of something else, and its command no longer
    proves the side.
    """
    repo = _checkout(tmp_path)
    commit = _git(repo, 'rev-parse', 'HEAD')
    _build(repo, version=_VERSION, build=commit)
    home = tmp_path / 'home'
    home.mkdir()
    # the binary is the build of this commit and this version
    rust = binary.side(repo)
    assert rust.name == binary.NAME
    assert rust.identity == {'engine': 'rust', 'version': _VERSION, 'build': commit}
    env = side.environment(rust, home, now=NOW)
    outcome = side.run(rust, ['--version'], cwd=home, env=env)
    assert (outcome.stdout, outcome.code) == (f'{_VERSION}\n'.encode(), 0)
    assert outcome.identity is True
    # the manifest states another version than the binary was built under
    manifest = repo / 'Cargo.toml'
    text = manifest.read_text(encoding='utf-8')
    manifest.write_text(text.replace(_VERSION, '2.0.0-rc.1'), encoding='utf-8')
    rust = binary.side(repo)
    assert rust.identity['version'] == '2.0.0-rc.1'
    assert side.run(rust, ['--version'], cwd=home, env=env).identity is False
    manifest.write_text(text, encoding='utf-8')
    # the checkout moves on, and the binary is not built again
    _git(repo, 'commit', '-q', '--allow-empty', '-m', 'later')
    rust = binary.side(repo)
    assert rust.identity['build'] == _git(repo, 'rev-parse', 'HEAD') != commit
    assert side.run(rust, ['--version'], cwd=home, env=env).identity is False


def test_checkout_with_no_built_binary_has_no_rust_side(tmp_path: pathlib.Path) -> None:
    """A checkout that was not built is refused, with the command that builds it.

    A debug build is no stand-in: the side is the release build, which is
    what ships.
    """
    repo = _checkout(tmp_path)
    debug = repo / 'target' / 'debug' / 'wiki'
    debug.parent.mkdir(parents=True)
    debug.write_text('', encoding='utf-8')
    with pytest.raises(ValueError, match='cargo build --release --locked'):
        binary.side(repo)


# ------ helpers


def _checkout(tmp_path: pathlib.Path) -> pathlib.Path:
    """Make a committed checkout whose workspace manifest states a version."""
    repo = tmp_path / 'checkout'
    repo.mkdir()
    manifest = f'[workspace.package]\nversion = "{_VERSION}"\n'
    (repo / 'Cargo.toml').write_text(manifest, encoding='utf-8')
    (repo / '.gitignore').write_text('/target\n', encoding='utf-8')
    _git(repo, 'init', '-q', '-b', 'main')
    _git(repo, 'add', '-A')
    _git(repo, 'commit', '-q', '-m', 'workspace')
    return repo


def _build(repo: pathlib.Path, *, version: str, build: str) -> None:
    """Write the release binary of ``repo`` as a build of ``version`` at ``build``."""
    path = repo / 'target' / 'release' / 'wiki'
    path.parent.mkdir(parents=True)
    record = {'engine': 'rust', 'version': version, 'build': build}
    path.with_name('build.json').write_text(json.dumps(record), encoding='utf-8')
    path.write_text(_BINARY, encoding='utf-8')
    path.chmod(0o755)
