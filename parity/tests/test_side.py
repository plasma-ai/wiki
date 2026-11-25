"""Test the ``parity.side`` module.

A side that runs its commands is covered by every matrix test; this module
carries the rule for the identity record a command must leave.
"""

from __future__ import annotations

import dataclasses
import pathlib
import shutil
import sys

import pytest

from parity import side

from .conftest import NOW

__all__ = ['test_command_proves_its_identity_by_one_record']

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
