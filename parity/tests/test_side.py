"""Test the ``parity.side`` module.

A side that runs its commands is covered by every matrix test; this module
carries the rule for the identity record a command must leave, and the rule
for the clock notice on a command that reads no clock.
"""

from __future__ import annotations

import dataclasses
import pathlib
import shutil
import sys

import pytest

from parity import side

from .conftest import NOW

__all__ = [
    'test_command_proves_its_identity_by_one_record',
    'test_notice_is_owed_only_where_the_clock_is_read',
]

pytestmark = pytest.mark.skipif(shutil.which('git') is None, reason='requires git')

# a side that runs the command it is given and then leaves the identity
# record a second time, or not at all
_RELAY = f"""
import os, pathlib, subprocess, sys
result = subprocess.run(sys.argv[2:])
trace = pathlib.Path(os.environ[{side.TRACE!r}])
if sys.argv[1] == 'twice':
    trace.write_bytes(trace.read_bytes() * 2)
elif sys.argv[1] == 'never':
    trace.unlink()
sys.exit(result.returncode)
"""


@pytest.mark.parametrize(
    argnames=('wrote', 'identified'),
    argvalues=[('once', True), ('twice', False), ('never', False)],
)
def test_command_proves_its_identity_by_one_record(
    tmp_path: pathlib.Path,
    engine: side.Side,
    wrote: str,
    identified: bool,
) -> None:
    """A command is its side's own when it leaves the expected record, once.

    The engine writes the record on every command. A command that leaves
    it twice ran the engine twice, or something else beside it, and one
    that leaves none did not run it; neither is the one run of the engine
    that a result stands for.
    """
    relay = tmp_path / 'relay.py'
    relay.write_text(_RELAY, encoding='utf-8')
    argv = (sys.executable, f'{relay}', wrote, *engine.argv)
    relayed = dataclasses.replace(engine, argv=argv)
    home = tmp_path / 'home'
    home.mkdir()
    env = side.environment(relayed, home, now=NOW)
    outcome = side.run(relayed, ['--version'], cwd=home, env=env)
    assert outcome.code == 0
    assert outcome.identity is identified


@pytest.mark.parametrize(
    argnames=('args', 'reads', 'kept'),
    argvalues=[
        (['--version'], False, True),
        (['--version'], True, False),
        (['lint', '--path', 'absent'], True, True),
        (['lint', '--path', 'absent'], False, False),
    ],
    ids=['none owed', 'owed and absent', 'owed', 'printed and not owed'],
)
def test_notice_is_owed_only_where_the_clock_is_read(
    tmp_path: pathlib.Path,
    engine: side.Side,
    args: list[str],
    reads: bool,
    kept: bool,
) -> None:
    """The clock notice is expected on a command exactly when it reads the clock.

    The engine prints the notice when a command starts, and the argument
    parser ends ``--version`` before that. A notice that is owed is taken
    off stderr. One that is printed where none is owed breaks the rule and
    stays in stderr, so it still shows against a side that printed none.
    """
    home = tmp_path / 'home'
    home.mkdir()
    env = side.environment(engine, home, now=NOW)
    outcome = side.run(engine, args, cwd=home, env=env, notice=reads)
    assert outcome.notice is kept
    assert outcome.stderr.startswith(side.NOTICE) is (not reads and not kept)
