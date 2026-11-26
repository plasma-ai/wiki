"""Functions for recording the calls v1's tests make.

v1's core tests call the engine in process, its CLI tests call the console
script through one helper, and its format tests call the format functions.
A run of those tests under the recorder (``parity/plugins``) leaves each
call of the first two kinds as a command line beside copies of the trees it
ran on, and each call of the third kind as its arguments and its outcome.
:func:`run` makes that run and reads the records back, for the replay.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
from collections.abc import Mapping, Sequence
from typing import Any, Optional

from . import suite
from .dispatch import Dispatcher
from .suite import Source

__all__ = []

#: the tests the recorder runs, relative to v1's checkout
TESTS = ('tests/test_core', 'tests/test_cli')

# the plugin that records the calls, and the modules it reaches into: the
# engine class, the format functions, and the tests whose format calls count
_PLUGIN = 'parity_record'
_WIKI = 'wiki.core.wiki:Wiki'
_FORMAT = 'wiki.core.format'
_FORMATS = ('tests/test_core/test_format.py',)
# the files one process of the run leaves in its folder of the store
_CALLS = 'calls.jsonl'
_FORMAT_CALLS = 'formats.jsonl'
_TESTS = 'tests.jsonl'


@dataclasses.dataclass(frozen=True)
class Copy:
    """The trees of one call as they stood at one moment, in the store.

    Attributes:
        folder: The folder that holds one copy per tree, under its name.
        names: The trees that existed at that moment.
        sealed: The paths, relative to ``folder``, that their owner could
            not read, each with its true mode. Their copies are readable.

    """

    folder: pathlib.Path = pathlib.Path()
    names: tuple[str, ...] = ()
    sealed: tuple[tuple[str, int], ...] = ()


@dataclasses.dataclass(frozen=True)
class Call:
    """One call a test made, as the recorder left it.

    Attributes:
        test: The id of the test that made the call.
        index: The call's number among the calls of its process.
        worker: The process of the run that made the call.
        source: ``core`` for a call of an engine method, ``cli`` for a
            command the CLI tests' helper ran.
        command: The command the call is one of.
        unformed: Why no command line states the call; empty for a call
            that has one.
        argv: The wiki arguments of the command line.
        cwd: The working directory of the call.
        env: The environment of the call, with the clock it ran under.
        base: The temporary folder of the call's process.
        trees: The top folders of ``base`` that the call names.
        root: The wiki root of an engine call; ``None`` for a command.
        outcome: What the call did. An engine call: ``returned`` with the
            result as far as a command line shows it, or ``raised`` with
            the exception's type and ``text``. A command: its ``code``,
            both streams as the helper read them, and whether stderr
            opened with the clock ``notice``.
        notices: The descriptions of the notices an engine call emitted,
            in order.
        before: The trees before the call.
        after: The trees after it.

    """

    test: str
    index: int
    worker: str
    source: str
    command: str
    unformed: str = ''
    argv: tuple[str, ...] = ()
    cwd: str = ''
    env: Mapping[str, str] = dataclasses.field(default_factory=dict)
    base: pathlib.Path = pathlib.Path()
    trees: tuple[str, ...] = ()
    root: Optional[str] = None
    outcome: Mapping[str, Any] = dataclasses.field(default_factory=dict)
    notices: tuple[str, ...] = ()
    before: Copy = Copy()
    after: Copy = Copy()


@dataclasses.dataclass(frozen=True)
class Recording:
    """What one run of the tests under the recorder left.

    Attributes:
        tests: The run of the tests: every test's outcome and the findings
            of the run.
        calls: Every recorded call, in the order of its process.
        kinds: For each test that ran, how often it met each notice kind
            (``notices``) and each issue kind (``issues``).
        formats: Every distinct call of a format function: the
            ``function``, its ``args`` and ``kwargs`` and its ``outcome``
            as JSON data, how often it was made (``count``) and the
            ``tests`` that made it.
        store: The folder that holds the copies.

    """

    tests: suite.Result
    calls: tuple[Call, ...]
    kinds: Mapping[str, Mapping[str, Mapping[str, int]]]
    formats: tuple[Mapping[str, Any], ...]
    store: pathlib.Path


def run(
    source: Source,
    dispatcher: Dispatcher,
    *,
    work: pathlib.Path,
    only: Sequence[str] = (),
    jobs: Optional[int] = None,
) -> Recording:
    """Run v1's tests under the recorder and return what they called.

    Args:
        source: v1's tests, from :func:`parity.suite.source`.
        dispatcher: A dispatcher that sends every call to v1: the record
            of a call is what v1 did in the test.
        work: New folder for the tests' trees and the store.
        only: Tests to run in place of :data:`TESTS`, each as pytest names
            a file or a test, relative to the checkout.
        jobs: How many processes run the tests; ``None`` leaves the count
            to v1's own pytest settings.

    Returns:
        The recording.

    Raises:
        ValueError: If the work folder is one the tests cannot run in.
        RuntimeError: If pytest ends without running the tests.

    """
    suite.validate(work)
    store = work / 'store'
    store.mkdir(parents=True)
    # tell the plugin what to record and where
    plan = work / 'record.json'
    document = {
        'store': f'{store}',
        'wiki': _WIKI,
        'format': _FORMAT,
        'formats': list(_FORMATS),
        'helper': source.helper,
    }
    plan.write_text(json.dumps(document), encoding='utf-8')
    tests = suite.run(
        source,
        dispatcher,
        work=work / 'tests',
        only=only or TESTS,
        jobs=jobs,
        options=['-p', _PLUGIN, '--parity-record', f'{plan}'],
    )
    # read back what each process of the run left
    calls: list[Call] = []
    kinds: dict[str, Mapping[str, Mapping[str, int]]] = {}
    formats: dict[str, dict[str, Any]] = {}
    for folder in sorted(path for path in store.iterdir() if path.is_dir()):
        for row in _rows(folder / _CALLS):
            calls.append(_call(row, folder))
        for row in _rows(folder / _TESTS):
            kinds[row['test']] = row['kinds']
        # one process's distinct format calls, joined with the others'
        for row in _rows(folder / _FORMAT_CALLS):
            call = {key: row[key] for key in row if key not in ('count', 'tests')}
            key = json.dumps(call, sort_keys=True)
            entry = formats.setdefault(key, {**call, 'count': 0, 'tests': []})
            entry['count'] += row['count']
            entry['tests'] = sorted({*entry['tests'], *row['tests']})
    return Recording(
        tests=tests,
        calls=tuple(calls),
        kinds=kinds,
        formats=tuple(formats[key] for key in sorted(formats)),
        store=store,
    )


# ------ helper functions


def _rows(path: pathlib.Path) -> list[dict[str, Any]]:
    """Return the JSON lines of the file at ``path``; none for a missing file."""
    if not path.is_file():
        return []
    text = path.read_text(encoding='utf-8')
    return [json.loads(line) for line in text.splitlines()]


def _call(row: Mapping[str, Any], folder: pathlib.Path) -> Call:
    """Return one line of a process's call file as a call."""
    call = Call(
        test=row['test'],
        index=row['index'],
        worker=folder.name,
        source=row['source'],
        command=row['command'],
        unformed=row.get('unformed', ''),
        root=row.get('root'),
        outcome=row.get('outcome', {}),
        notices=tuple(row.get('notices') or ()),
    )
    if call.unformed:
        return call
    copies = {}
    for moment in ('before', 'after'):
        copies[moment] = Copy(
            folder=folder / f'{row["index"]}' / moment,
            names=tuple(row[moment]['names']),
            sealed=tuple((relpath, mode) for relpath, mode in row[moment]['sealed']),
        )
    return dataclasses.replace(
        call,
        argv=tuple(row['argv']),
        cwd=row['cwd'],
        env=row['env'],
        base=pathlib.Path(row['trees']['base']),
        trees=tuple(row['trees']['names']),
        before=copies['before'],
        after=copies['after'],
    )
