"""Functions for the CLI suite: v1's CLI tests, run through the dispatcher.

v1's CLI tests drive the console script as a subprocess and assert on what
it prints, returns and writes. They run where v1 runs them, in the oracle
checkout under the oracle side's interpreter, with one change: every call
goes through the dispatcher, which sends the calls of the ported commands to
a second engine. The tests' own assertions are the verdict.
"""

from __future__ import annotations

import collections
import dataclasses
import json
import os
import pathlib
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Optional

from . import dispatch, oracle
from .dispatch import Dispatcher
from .oracle import Oracle
from .runner import WIKI_SETTINGS

__all__ = []

#: the v1 side the suite runs in: the one whose venv holds pytest
REFERENCE = 'oracle'

# v1's CLI tests, relative to its checkout, and the module they take the
# console script from
_TESTS = 'tests/test_cli'
_HELPER = 'tests.test_cli.conftest'
# the plugin that points the tests at the dispatcher, and the folder that
# holds it and nothing else
_PLUGIN = 'parity_suite'
_PLUGINS = pathlib.Path(__file__).parent / 'plugins'
# starts pytest with the plugin's folder at the end of the module path: the
# folder is named first, and no variable carries it on to the tests' commands.
# A caller that ignores interrupts, as a shell does for a detached job, hands
# that on to every process below it, and a test that interrupts a command
# would then interrupt nothing: the interrupt is taken back first
_BOOT = (
    'import signal, sys\n'
    'signal.signal(signal.SIGINT, signal.default_int_handler)\n'
    'sys.path.append(sys.argv.pop(1))\n'
    'import pytest\n'
    'sys.exit(pytest.main())'
)
# the exit codes pytest ends with when every test ran: all passed, or some
# did not
_RAN = (0, 1)
# the outcomes that make pytest end with the second of them
_FAILED = ('failed', 'error')
# seconds the tests may run -- a run this long has hung
_TIMEOUT = 3_600
# how many lines of pytest's output an error of the run quotes
_TAIL = 20


@dataclasses.dataclass(frozen=True)
class Source:
    """Where v1's CLI tests are, and what runs them.

    Attributes:
        python: The interpreter that runs the tests; its venv holds pytest.
        checkout: The checkout the tests run from.
        folder: The folder of the tests, relative to the checkout.
        helper: Name of the module the tests take the console script from.

    """

    python: pathlib.Path
    checkout: pathlib.Path
    folder: str = _TESTS
    helper: str = _HELPER


@dataclasses.dataclass(frozen=True)
class Item:
    """One test of the suite, as it ran.

    Attributes:
        id: The test's id, as pytest names it.
        outcome: ``passed``, ``failed`` (its own assertion), ``error`` (its
            setup or teardown), ``skipped``, ``xfailed`` or ``xpassed``. A
            test file that was not loaded is an item too, with the file as
            its id: ``error`` if it failed, ``skipped`` if it skipped
            itself.
        calls: How many calls of the test each side took, by name.
        commands: How many of its calls of each ported command the second
            engine took.
        identified: Whether every call left the identity record of the
            side it went to.

    """

    id: str
    outcome: str
    calls: Mapping[str, int]
    commands: Mapping[str, int]
    identified: bool


@dataclasses.dataclass(frozen=True, order=True)
class Finding:
    """One way a run of the tests is not the expected one.

    Attributes:
        kind: A test's outcome other than ``passed``; ``identity`` (a call
            of the test did not prove the side it went to); ``untraced``
            (calls reached the dispatcher outside every test); or
            ``unused`` (a side, or a ported command, that no test called).
        subject: The test's id, the number of calls, or the side or
            command.

    """

    kind: str
    subject: str


@dataclasses.dataclass(frozen=True)
class Result:
    """What one run of the tests found.

    Attributes:
        findings: Every way the run is not the expected one.
        report: The run as JSON data: every test with its outcome and its
            calls, the tests left out, and the counts by side and by
            ported command.
        log: What pytest printed.

    """

    findings: tuple[Finding, ...]
    report: Mapping[str, Any]
    log: bytes


def source(named: Oracle) -> Source:
    """Return v1's CLI tests as the oracle checkout holds them."""
    python = oracle._python(named, named.builds[REFERENCE])
    return Source(python=python, checkout=named.checkout)


def validate(work: pathlib.Path) -> None:
    """Refuse a work folder that :func:`run` cannot use; make nothing.

    Raises:
        ValueError: If a folder above ``work`` declares a wiki (the tests
            make their wikis below ``work``, and each would be refused as
            nested in it), if ``work`` lies inside a repository (``init``
            registers the merge driver in the repository it finds, and that
            must be one a test made), or if ``work`` exists.

    """
    for parent in work.resolve().parents:
        if (parent / WIKI_SETTINGS).is_file():
            raise ValueError(
                f'Work folder {work} lies below {parent}, which holds'
                f' {WIKI_SETTINGS}; choose one outside it.'
            )
        if os.path.lexists(parent / '.git'):
            raise ValueError(
                f'Work folder {work} lies inside the repository {parent};'
                f' choose one outside it.'
            )
    if os.path.lexists(work):
        raise ValueError(f'Work folder {work} exists; the harness makes it new.')


def run(
    source: Source,
    dispatcher: Dispatcher,
    *,
    work: pathlib.Path,
    only: Sequence[str] = (),
    jobs: Optional[int] = None,
    options: Sequence[str] = (),
) -> Result:
    """Run v1's CLI tests with every call sent through ``dispatcher``.

    Args:
        source: The tests, from :func:`source`.
        dispatcher: The dispatcher of the run, which sends each call to
            the side that has its command.
        work: New folder for the tests' trees and the trace files.
        only: Tests to run in place of the whole folder, each as pytest
            names a file or a test, relative to the checkout.
        jobs: How many processes run the tests; ``None`` leaves the count
            to v1's own pytest settings.
        options: Further options for pytest, which load another plugin of
            the harness beside this one.

    Returns:
        The findings and the report.

    Raises:
        ValueError: If :func:`validate` refuses ``work``.
        RuntimeError: If pytest ends without running the tests: it found
            none, it could not start them, or it ends as failed with no
            test and no file that failed.

    """
    validate(work)
    home = work / 'home'
    traces = work / 'traces'
    for folder in (home, traces):
        folder.mkdir(parents=True)
    (home / '.gitconfig').write_bytes(b'')
    # tell the plugin where the dispatcher and the files of the run are
    plan = work / 'plan.json'
    result = work / 'result.json'
    document = {
        'helper': source.helper,
        'wiki': f'{dispatcher.path}',
        'traces': f'{traces}',
        'result': f'{result}',
    }
    plan.write_text(json.dumps(document), encoding='utf-8')
    # run the tests as v1 runs them, under its own pytest settings
    cmd = [f'{source.python}', '-P', '-c', _BOOT, f'{_PLUGINS}']
    cmd += ['-p', _PLUGIN, '--parity-plan', f'{plan}']
    # nothing is written into the checkout, and every tree lies below work
    cmd += ['-p', 'no:cacheprovider', '--basetemp', f'{work / "tmp"}']
    if jobs is not None:
        cmd += ['-n', f'{jobs}']
    cmd += options
    cmd += only or [source.folder]
    done = subprocess.run(
        cmd,
        cwd=source.checkout,
        env=dispatch.environment(dispatcher, home),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=_TIMEOUT,
    )
    data = None
    if (done.returncode in _RAN) and result.is_file():
        data = json.loads(result.read_text(encoding='utf-8'))
        # a run that failed owes a row that says what failed
        failed = any(row['outcome'] in _FAILED for row in data['tests'])
        if (done.returncode != 0) and not failed:
            data = None
    if data is None:
        tail = done.stdout.decode('utf-8', 'replace').splitlines()[-_TAIL:]
        raise RuntimeError(
            f'pytest did not run the tests (exit {done.returncode}): {tail!r}'
        )
    # read every test's outcome, and its calls from its trace file
    items = []
    for row in data['tests']:
        tally = dispatch.tally(dispatcher, traces / row['trace'])
        item = Item(
            id=row['id'],
            outcome=row['outcome'],
            calls=tally.calls,
            commands=tally.commands,
            identified=tally.identified,
        )
        items.append(item)
    untraced = 0
    if dispatcher.untraced.is_file():
        untraced = len(dispatcher.untraced.read_bytes().splitlines())
    findings = _findings(items, dispatcher, untraced=untraced)
    report = {
        'only': list(only),
        'tests': [dataclasses.asdict(item) for item in items],
        'deselected': data['deselected'],
        'counts': _counts(items, dispatcher, untraced=untraced),
        'findings': [dataclasses.asdict(finding) for finding in findings],
    }
    return Result(findings=tuple(findings), report=report, log=done.stdout)


# ------ helper functions


def _findings(
    items: Sequence[Item],
    dispatcher: Dispatcher,
    *,
    untraced: int,
) -> list[Finding]:
    """Return every way a run of the tests is not the expected one.

    A test that does not pass is one, whatever its outcome: a skipped test
    and an expected failure ran nothing against the engine. So is a call
    that did not prove its side, and a call no test owns. And a run in
    which a side, or a ported command, took no call proved nothing about
    it.
    """
    findings = []
    for item in items:
        if item.outcome != 'passed':
            findings.append(Finding(item.outcome, item.id))
        if not item.identified:
            findings.append(Finding('identity', item.id))
    if untraced:
        s = 's' if untraced != 1 else ''
        findings.append(Finding('untraced', f'{untraced} call{s}'))
    calls = _total(item.calls for item in items)
    for name in dispatcher.sides:
        if not calls[name]:
            findings.append(Finding('unused', f'side {name}'))
    commands = _total(item.commands for item in items)
    for command in dispatcher.commands:
        if not commands[command]:
            findings.append(Finding('unused', f'command {command}'))
    return findings


def _counts(
    items: Sequence[Item],
    dispatcher: Dispatcher,
    *,
    untraced: int,
) -> dict[str, Any]:
    """Return how much of the suite ran on each side, as JSON data."""
    outcomes = collections.Counter(item.outcome for item in items)
    calls = _total(item.calls for item in items)
    commands = _total(item.commands for item in items)
    return {
        'tests': len(items),
        'outcomes': dict(sorted(outcomes.items())),
        'sides': {
            name: {
                'tests': sum(1 for item in items if item.calls.get(name)),
                'calls': calls[name],
            }
            for name in dispatcher.sides
        },
        'commands': {
            command: {
                'tests': sum(1 for item in items if item.commands.get(command)),
                'calls': commands[command],
            }
            for command in dispatcher.commands
        },
        'uncalled': sum(1 for item in items if not item.calls),
        'untraced': untraced,
    }


def _total(counts: Iterable[Mapping[str, int]]) -> collections.Counter[str]:
    """Return the sum of several counts by name."""
    total: collections.Counter[str] = collections.Counter()
    for count in counts:
        total.update(count)
    return total
