"""Test the ``parity.side`` module.

The notice rule is covered end-to-end by the matrix runs of
``test_main`` and ``test_selfcheck``; this module carries what a side
runs under: the built environment and the null stdin.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys

import pytest

from parity import side

from .conftest import NOW

__all__ = ['test_side_runs_under_the_built_environment_alone']

# a side that reports the variables it sees, what it reads from stdin and the
# offset of its local time
_PROBE = (
    'import json, os, sys, time\n'
    "report = {'env': dict(os.environ), 'stdin': sys.stdin.read()}\n"
    "report['offset'] = time.strftime('%z')\n"
    'print(json.dumps(report))\n'
)
# the variables every side is given, before its own
_BUILT = {
    'PATH',
    'HOME',
    'WIKI_CONFIG_DIR',
    'OFFLINE_MODE',
    'GIT_CONFIG_GLOBAL',
    'GIT_CONFIG_NOSYSTEM',
    'LANG',
    'TZ',
    'WIKI_FREEZE_TIME',
}


def test_side_runs_under_the_built_environment_alone(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A side sees the built variables, its own, and nothing of the caller's.

    A variable or a line of input that reached a side from the caller
    would reach both sides alike, so two engines could agree on a run
    that depends on the shell it was started from. The zone sits off
    UTC by an odd offset, so a stamp an engine takes in local time
    shows.
    """
    monkeypatch.setenv('PARITY_AMBIENT', 'leaked')
    argv = (sys.executable, '-c', _PROBE)
    probe = side.Side(name='probe', argv=argv, env={'PARITY_OWN': 'own'}, notice=False)
    home = tmp_path / 'home'
    home.mkdir()
    # the built variables are the fixed ones and the side's own
    env = side.environment(probe, home, now=NOW)
    assert set(env) == {*_BUILT, 'PARITY_OWN'}
    assert env['WIKI_FREEZE_TIME'] == NOW
    # run the side with text waiting on the caller's stdin
    waiting = tmp_path / 'stdin'
    waiting.write_text('text the side must not read\n', encoding='utf-8')
    saved = os.dup(0)
    try:
        with open(waiting, 'rb') as handle:
            os.dup2(handle.fileno(), 0)
            outcome = side.run(probe, [], cwd=home, env=env)
    finally:
        os.dup2(saved, 0)
        os.close(saved)
    assert outcome.code == 0, outcome.stderr
    report = json.loads(outcome.stdout)
    # the side sees each built variable as built, and not the caller's
    seen = report['env']
    assert {key: seen.get(key) for key in env} == env
    assert 'PARITY_AMBIENT' not in seen
    assert report['stdin'] == ''
    assert report['offset'] == '+0545'
