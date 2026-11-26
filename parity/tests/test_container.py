"""Test the ``parity.container`` module.

Drives the command as a session does: a command runs in a clone of the
repository on a file system that keeps case and bytes, and its exit code
comes back. The container stands in for Linux on a host that is none, so
the run itself is tested there, where a Docker daemon answers.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys

import pytest

from parity import __main__ as cli

from .conftest import REPO_ROOT, _needs_git

__all__ = [
    'test_container_runs_a_command_in_a_clone_that_keeps_case_and_bytes',
    'test_container_that_cannot_start_is_refused',
]

# what the container's command leaves in the output folder: the commit of
# its clone, and what the clone's file system lacks of what a family needs
_PROBE = (
    'git rev-parse HEAD > /out/head'
    ' && uv run --no-sync python -c "'
    'import pathlib; from parity import fixtures;'
    " print(sorted(fixtures.lacking(pathlib.Path('.'))))"
    '" > /out/lacking'
    ' && exit 7'
)


def _answers() -> bool:
    """Return whether a Docker daemon answers on this host."""
    if shutil.which('docker') is None:
        return False
    return subprocess.run(['docker', 'version'], capture_output=True).returncode == 0


# gates the test that starts a container
_needs_docker = pytest.mark.skipif(
    (sys.platform == 'linux') or not _answers(),
    reason='needs a Docker daemon on a host that is not Linux',
)


@_needs_git
@_needs_docker
def test_container_runs_a_command_in_a_clone_that_keeps_case_and_bytes(
    tmp_path: pathlib.Path,
) -> None:
    """A command runs in a clone at the named commit, and its exit code returns.

    The clone lies on the container's own file system, which keeps two
    names that differ in case and a name that is no valid UTF-8, so every
    fixture family can be written there. The harness's environment is
    installed, and the output folder is the host's.
    """
    head = subprocess.run(
        ['git', '-C', f'{REPO_ROOT}', 'rev-parse', 'HEAD'],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    out = tmp_path / 'out'
    argv = ['container', '--commit', head, '--out', f'{out}', '--', 'sh', '-c', _PROBE]
    assert cli.main(argv) == 7
    assert (out / 'head').read_text(encoding='utf-8').strip() == head
    assert (out / 'lacking').read_text(encoding='utf-8').strip() == '[]'


@_needs_git
@pytest.mark.parametrize(
    argnames=('argv', 'message'),
    argvalues=[
        ([], 'needs a command'),
        (['--commit', 'no-such-commit', 'true'], 'names no commit'),
        (['true'], 'no docker is on PATH'),
    ],
    ids=['command', 'commit', 'docker'],
)
def test_container_that_cannot_start_is_refused(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    message: str,
) -> None:
    """A run with no command, no such commit or no Docker exits 2 with a message.

    Exit code 2 from the command is then the harness's own, never the
    code of a command that ran.
    """
    # a PATH that holds git and nothing else
    git = shutil.which('git')
    assert git is not None
    (tmp_path / 'bin').mkdir()
    (tmp_path / 'bin' / 'git').symlink_to(git)
    monkeypatch.setenv('PATH', f'{tmp_path / "bin"}')
    with pytest.raises(SystemExit) as raised:
        cli.main(['container', *argv])
    assert raised.value.code == 2
    assert message in capsys.readouterr().err
