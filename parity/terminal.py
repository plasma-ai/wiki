"""Functions for the terminal table: commands compared on a pseudo-terminal.

An engine prints other bytes to a terminal than to a pipe, and only a
terminal turns a typed Ctrl-C into an interrupt. Each case of the table runs
one command twice with a pseudo-terminal as its three streams, once on the
v1 side and once through the dispatcher, and compares what the terminal was
sent.
"""

from __future__ import annotations

import contextlib
import dataclasses
import errno
import hashlib
import os
import pathlib
import pty
import select
import signal
import subprocess
import sys
import termios
import time
from collections.abc import Mapping, Sequence
from typing import Any

from . import dispatch, side
from .compare import Difference
from .dispatch import Dispatcher
from .side import TRACE, Side

__all__ = []

#: the name of the table in a difference, where a matrix names a stage
STAGE = 'terminal'
#: the name of the run that goes through the dispatcher
DISPATCHED = 'dispatcher'

# the pages of the table's wiki: one whose body holds what a terminal acts on
# (a color sequence, a title sequence, a bell, a unit separator and a delete),
# one too long for any terminal to take whole, so the command that prints it
# is still printing when a key is typed, and one the wiki does not hold
_STYLED = 'styled'
_LONG = 'long'
_ABSENT = 'absent'
_STYLED_TEXT = (
    b'---\nname: styled\ndesc: Styled.\n---\n\n# styled\n\n'
    b'\x1b[31mred\x1b[0m \x1b]0;title\x07 bell \x07 unit \x1f delete \x7f end\n'
)
_LONG_HEAD = b'---\nname: long\ndesc: Long.\n---\n\n# long\n\n'
_LONG_LINE = b'line %07d of the long page\n'
# how many lines the long page holds: several megabytes, where the largest
# terminal buffer in use holds less than one
_LONG_LINES = 150_000
# the key a terminal turns into an interrupt of the command in its foreground
_INTERRUPT = b'\x03'
# the terminal every command sees: its type, its rows and its columns
_TERM = 'xterm-256color'
_SIZE = (24, 80)
# how much of what a command shows is read at once
_CHUNK = 65_536
# seconds one command may run -- a command that runs this long has hung
_TIMEOUT = 300
# makes the terminal on the given descriptor the controlling terminal and the
# three streams of the process, then becomes the command. A caller that
# ignores interrupts, as a shell does for a detached job, hands that on to
# every process below it: the interrupt is taken back first
_LOGIN = (
    'import os, signal, sys\n'
    'signal.signal(signal.SIGINT, signal.SIG_DFL)\n'
    'os.login_tty(int(sys.argv[1]))\n'
    'os.execvp(sys.argv[2], sys.argv[2:])'
)


@dataclasses.dataclass(frozen=True)
class Case:
    """One wiki command of the table.

    Attributes:
        name: Label of the case.
        args: The wiki arguments, without ``--path``.
        codes: The exit codes the command may end with, as for a case of
            the matrix.
        keys: What is typed once the command has shown its first bytes;
            empty types nothing.
        page: With ``keys``, the page the command is printing when they are
            typed. How much of it a side shows before it acts on the keys
            turns on timing, so what a side shows is compared from where
            it leaves the page's text.

    """

    name: str
    args: tuple[str, ...]
    codes: frozenset[int]
    keys: bytes = b''
    page: str = ''


@dataclasses.dataclass(frozen=True)
class Shown:
    """What one command sent to its terminal, and how it ended.

    Attributes:
        output: The bytes the command wrote, on either stream.
        code: The exit code, or the negated number of the signal that
            ended the command.

    """

    output: bytes
    code: int


@dataclasses.dataclass(frozen=True)
class Result:
    """What one run of the table found.

    Attributes:
        differences: Every difference between the v1 side and the
            dispatcher, and every rule a run broke by itself.
        report: The run as JSON data: each case's exit code and the size
            and digest of its output on both runs, and how many calls the
            dispatcher sent to each side.
        streams: The output of each case whose output differs, by a file
            name that carries the case and the run.

    """

    differences: tuple[Difference, ...]
    report: Sequence[Mapping[str, Any]]
    streams: Mapping[str, bytes]


#: the cases of the table: a page with terminal sequences, an error, and an
#: interrupt typed while a page is printing
CASES = (
    Case(name='sequences', args=('read', _STYLED), codes=frozenset({0})),
    Case(name='error', args=('read', _ABSENT), codes=frozenset({2})),
    Case(
        name='interrupt',
        args=('read', _LONG),
        codes=frozenset({130}),
        keys=_INTERRUPT,
        page=_LONG,
    ),
)


def run(
    reference: Side,
    dispatcher: Dispatcher,
    *,
    work: pathlib.Path,
    cases: Sequence[Case] = CASES,
) -> Result:
    """Run the table on the v1 side and through the dispatcher, and compare.

    Both runs read one wiki at one path, which the v1 side makes first. The
    table's commands write nothing, so the second run finds the tree the
    first one found.

    Args:
        reference: The v1 side, which the dispatcher is compared against.
        dispatcher: The dispatcher of the run, which sends each case to
            the side that has its command.
        work: New folder for the wiki, the home and the trace files.
        cases: The cases to run.

    Returns:
        The differences and the report.

    Raises:
        RuntimeError: If the v1 side cannot make the wiki, or if a command
            does not end.

    """
    home = work / 'home'
    root = work / 'tree' / 'wiki'
    traces = work / 'traces'
    for folder in (home, root.parent, traces):
        folder.mkdir(parents=True)
    (home / '.gitconfig').write_bytes(b'')
    built = dispatch.environment(dispatcher, home)
    # make the wiki with the v1 side, then write the pages
    made = side.run(reference, ['init', '--path', f'{root}'], cwd=home, env=built)
    if made.code != 0:
        error = made.stderr.decode('utf-8', 'replace').strip()
        raise RuntimeError(f'init failed (exit {made.code}): {error!r}')
    lines = (_LONG_LINE % number for number in range(_LONG_LINES))
    pages = {_STYLED: _STYLED_TEXT, _LONG: _LONG_HEAD + b''.join(lines)}
    for name, text in pages.items():
        (root / f'{name}.md').write_bytes(text)
    env = {'TERM': _TERM, **built}
    found: list[tuple[str, str, str, str]] = []
    streams = {}
    report = []
    for case in cases:
        args = [*case.args, '--path', f'{root}']
        # run the case on the v1 side
        trace = traces / f'{case.name} {reference.name}'
        argv = [*reference.argv, *args]
        first = show(argv, cwd=home, env={**env, TRACE: f'{trace}'}, keys=case.keys)
        proved = (not reference.identity) or side._identified(trace, reference.identity)
        # run it through the dispatcher, which must send it to one side
        trace = traces / f'{case.name} {DISPATCHED}'
        argv = [f'{dispatcher.path}', *args]
        second = show(argv, cwd=home, env={**env, TRACE: f'{trace}'}, keys=case.keys)
        tally = dispatch.tally(dispatcher, trace)
        sent = tally.identified and (sum(tally.calls.values()) == 1)
        # compare what the two runs showed, and how they ended
        page = pages.get(case.page, b'')
        if _compared(first.output, page) != _compared(second.output, page):
            found.append((DISPATCHED, case.name, 'output', ''))
            streams[f'{case.name} {reference.name}.out'] = first.output
            streams[f'{case.name} {DISPATCHED}.out'] = second.output
        if first.code != second.code:
            found.append((DISPATCHED, case.name, 'exit', ''))
        # check each run by itself: it ended as its case is built to, on the
        # engine its side names
        runs = {reference.name: (first, proved), DISPATCHED: (second, sent)}
        row: dict[str, Any] = {'name': case.name, 'args': list(case.args), 'runs': {}}
        for name, (shown, identified) in runs.items():
            if shown.code not in case.codes:
                found.append((name, case.name, 'unexpected', f'exit {shown.code}'))
            if not identified:
                found.append((name, case.name, 'identity', ''))
            row['runs'][name] = {
                'exit': shown.code,
                'output': {
                    'bytes': len(shown.output),
                    'sha256': hashlib.sha256(shown.output).hexdigest(),
                },
            }
        row['sent'] = dict(tally.calls)
        report.append(row)
    return Result(
        differences=tuple(Difference(STAGE, *item) for item in found),
        report=report,
        streams=streams,
    )


def show(
    argv: Sequence[str],
    *,
    cwd: pathlib.Path,
    env: Mapping[str, str],
    keys: bytes = b'',
) -> Shown:
    """Run one command with a pseudo-terminal as its three streams.

    The terminal is the command's controlling terminal, so a typed
    interrupt key reaches the command, and every process it started, as a
    signal. The terminal passes output on as written, echoes nothing that
    is typed and drops no output on an interrupt, so what is read is every
    byte the command wrote, in order.

    Args:
        argv: The whole command.
        cwd: Working directory of the command.
        env: Full environment of the command.
        keys: Bytes to type once the command has written its first bytes.

    Returns:
        What the command wrote and how it ended.

    Raises:
        RuntimeError: If the command does not end within the time limit.

    """
    master, slave = pty.openpty()
    try:
        termios.tcsetwinsize(slave, _SIZE)
        attributes = termios.tcgetattr(slave)
        attributes[1] &= ~termios.OPOST
        attributes[3] = (attributes[3] & ~termios.ECHO) | termios.NOFLSH
        termios.tcsetattr(slave, termios.TCSANOW, attributes)
        # NOTE: the terminal becomes the controlling one in a process that
        #   then becomes the command, so no code of the harness runs between
        #   a fork and an exec
        login = [sys.executable, '-I', '-S', '-c', _LOGIN, f'{slave}', *argv]
        process = subprocess.Popen(
            login,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            pass_fds=[slave],
        )
    except BaseException:
        os.close(master)
        raise
    finally:
        os.close(slave)
    output = b''
    deadline = time.monotonic() + _TIMEOUT
    try:
        while True:
            left = max(0.0, deadline - time.monotonic())
            ready, _, _ = select.select([master], [], [], left)
            if not ready:
                raise RuntimeError(
                    f'{list(argv)!r} did not end within {_TIMEOUT} seconds.'
                )
            try:
                chunk = os.read(master, _CHUNK)
            except OSError as e:
                # Linux ends a terminal no process holds with an error
                if e.errno != errno.EIO:
                    raise
                chunk = b''
            if not chunk:
                break
            output += chunk
            if keys:
                os.write(master, keys)
                keys = b''
        code = process.wait(timeout=max(0.0, deadline - time.monotonic()))
    finally:
        # the command leads a session of its own: end whatever it left
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        os.close(master)
    return Shown(output=output, code=code)


# ------ helper functions


def _compared(output: bytes, page: bytes) -> bytes:
    """Return the part of ``output`` that two runs must agree on.

    A command that was interrupted while it printed ``page`` shows a head
    of the page whose length turns on timing, then what it prints for the
    interrupt. The head it shares with the page is left out. An empty page
    leaves the output whole.

    >>> _compared(b'a long paInterrupted.', b'a long page')
    b'Interrupted.'
    >>> _compared(b'a long page', b'a long page')
    b''
    >>> _compared(b'Error: no page', b'')
    b'Error: no page'
    """
    shared = 0
    for one, other in zip(output, page):
        if one != other:
            break
        shared += 1
    return output[shared:]
