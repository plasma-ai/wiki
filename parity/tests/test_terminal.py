"""Test the ``parity.terminal`` module.

The table runs real sides on a real pseudo-terminal. This module carries
the clean run, v1 against itself through the dispatcher, and one doctored
engine for each thing the table compares.
"""

from __future__ import annotations

import dataclasses
import pathlib
import signal
import sys

import pytest

from parity import dispatch, side, terminal

from .conftest import _needs_git

__all__ = [
    'test_table_shows_no_difference_between_v1_and_itself',
    'test_engine_that_differs_on_a_terminal_is_reported',
]

pytestmark = _needs_git

# the names of the table's cases, and the exit code v1 ends each with
_CODES = {'sequences': 0, 'error': 2, 'interrupt': 130}
# an engine that runs the command it is given and changes one thing: it
# ignores interrupts, it writes a byte of its own at the end, or it ends with
# another exit code. Or it runs nothing: it writes one line and ends
_DOCTOR = """
import os, signal, subprocess, sys
mode, argv = sys.argv[1], sys.argv[2:]
if mode in ('deaf', 'short'):
    signal.signal(signal.SIGINT, signal.SIG_IGN)
if mode == 'short':
    os.write(1, b'error: no' + bytes([10]))
    sys.exit(2)
if mode in ('deaf', 'plain'):
    os.execv(argv[0], argv)
# outlive the interrupt of the command
signal.signal(signal.SIGINT, lambda *_: None)
code = subprocess.run(argv).returncode
if mode == 'byte':
    os.write(1, b'!')
elif mode == 'code':
    code += 1
sys.exit(code)
"""


@pytest.mark.parametrize('ignored', [False, True], ids=['attached', 'detached'])
def test_table_shows_no_difference_between_v1_and_itself(
    tmp_path: pathlib.Path,
    engine: side.Side,
    ignored: bool,
) -> None:
    """v1 through the dispatcher shows a terminal what v1 shows it directly.

    Each case ends as it is built to: the page prints, the missing page is
    an error, and the typed Ctrl-C interrupts the long page. v1 strips
    terminal sequences from what it writes to a pipe, so the whole page in
    the output shows that the command saw a terminal. A caller that
    ignores interrupts, as a shell does for a detached job, changes none
    of it.
    """
    work = tmp_path / 'work'
    dispatcher = dispatch.build(work / 'bin', reference=engine)
    held = signal.getsignal(signal.SIGINT)
    if ignored:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        result = terminal.run(engine, dispatcher, work=work / 'terminal')
    finally:
        signal.signal(signal.SIGINT, held)
    assert result.differences == ()
    assert result.streams == {}
    assert [row['name'] for row in result.report] == list(_CODES)
    for row in result.report:
        assert row['sent'] == {engine.name: 1}
        runs = row['runs']
        assert set(runs) == {engine.name, terminal.DISPATCHED}
        assert {run['exit'] for run in runs.values()} == {_CODES[row['name']]}
    shown = result.report[0]['runs'][engine.name]['output']['bytes']
    assert shown == len(terminal._STYLED_TEXT)


@pytest.mark.parametrize(
    argnames=('mode', 'build', 'expected'),
    argvalues=[
        ('byte', None, {(case, 'output', '') for case in _CODES}),
        (
            'code',
            None,
            {(case, 'exit', '') for case in _CODES}
            | {
                (case, 'unexpected', f'exit {code + 1}')
                for case, code in _CODES.items()
            },
        ),
        (
            'deaf',
            None,
            {
                ('interrupt', 'output', ''),
                ('interrupt', 'exit', ''),
                ('interrupt', 'unexpected', 'exit 0'),
            },
        ),
        ('plain', 'another', {(case, 'identity', '') for case in _CODES}),
        (
            'short',
            None,
            {(case, aspect, '') for case in _CODES for aspect in ('output', 'identity')}
            | {
                (case, aspect, detail)
                for case in ('sequences', 'interrupt')
                for aspect, detail in (('exit', ''), ('unexpected', 'exit 2'))
            },
        ),
    ],
    ids=['output', 'exit code', 'interrupt', 'identity', 'ended early'],
)
def test_engine_that_differs_on_a_terminal_is_reported(
    tmp_path: pathlib.Path,
    engine: side.Side,
    mode: str,
    build: str,
    expected: set[tuple[str, str, str]],
) -> None:
    """Each thing the table compares is reported when a second engine changes it.

    The second engine is v1 behind a wrapper that takes the ``read``
    command and changes one thing: a byte of the output, the exit code,
    the answer to an interrupt, or the build it is expected to be. The
    table reports exactly that, on the dispatcher's run, and keeps both
    outputs where they differ. An engine that ends after one line, before
    the Ctrl-C of its case is typed, is reported the same way: the key it
    no longer takes is no error of the table.
    """
    doctor = tmp_path / 'doctor.py'
    doctor.write_text(_DOCTOR, encoding='utf-8')
    argv = (sys.executable, f'{doctor}', mode, *engine.argv)
    identity = dict(engine.identity)
    if build is not None:
        identity['build'] = build
    second = dataclasses.replace(engine, name='second', argv=argv, identity=identity)
    work = tmp_path / 'work'
    dispatcher = dispatch.build(
        work / 'bin',
        reference=engine,
        engine=second,
        commands=['read'],
    )
    result = terminal.run(engine, dispatcher, work=work / 'terminal')
    sent = {row['name']: row['sent'] for row in result.report}
    assert sent == {case: {'second': 1} for case in _CODES}
    found = {
        (difference.case, difference.aspect, difference.detail)
        for difference in result.differences
    }
    assert found == expected
    runs = {difference.run for difference in result.differences}
    assert runs == {terminal.DISPATCHED}
    differing = {case for case, aspect, _ in expected if aspect == 'output'}
    assert set(result.streams) == {
        f'{case} {name}.out'
        for case in differing
        for name in (engine.name, terminal.DISPATCHED)
    }
    if mode == 'byte':
        assert result.streams[f'error {terminal.DISPATCHED}.out'].endswith(b'!')
