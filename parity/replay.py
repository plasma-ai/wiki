"""Functions for replaying recorded calls through the sides.

A recorded call is a command line and the trees it ran on. The replay puts
the trees back at their own paths, once for each side, runs the command
line there, and compares the sides by the rules of ``compare``. It also
holds the first side, which is v1, to what the call did in its test: a
replay that does not reproduce the test's own call compares some other
scenario, and says nothing about the test.
"""

from __future__ import annotations

import collections
import concurrent.futures
import dataclasses
import hashlib
import json
import os
import pathlib
import posixpath
import shutil
import subprocess
import sys
import threading
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Optional

from . import cache, classify, compare, doctor, manifest, side, snapshot, suite
from .cache import CACHE, View
from .classify import Excused, Judge
from .compare import Difference, Record, Run
from .manifest import Entry
from .matrix import Case, Stage
from .record import Call, Copy, Recording
from .runner import WIKI_SETTINGS
from .side import NOTICE, Outcome, Side
from .suite import Source

__all__ = []

#: the commands that write nothing outside the caches
READ_ONLY = ('lint', 'read', 'match', 'map', 'search')
#: the control that doctors the argument mapping, and the one that doctors a side
CONTROLS = ('argv', 'side')

# the option that makes `update` a read-only command, and the mark that ends
# the options of a command line
_CHECK = '--check'
_END = '--'
# the lines v1's command line prints for what an engine call returns
_WOULD = 'Would update: '
_UPDATED = 'Updated {count} file{s}.\n'
_NOTHING = 'Nothing to update.\n'
_CREATED = 'Created {name}.\n'
_EMPTY = 'Wiki is empty.\n'
_ERROR = 'Error: {text}\n'
# the commands whose command line prints every notice of the call on stderr
_NARRATED = ('update', 'map')
# the folder of a repository's own state, which no side is compared on
_GIT = '.git'
# the script that makes the recorded format calls again, and the module of
# the format functions
_VALUES = suite._PLUGINS / 'parity_values.py'
_FORMAT = 'wiki.core.format'
# the hash seeds the format calls are made again under
_SEEDS = ('1', '2')
# the outcomes of a test that the recording counts as its own failure
_BROKEN = ('failed', 'error', 'identity', 'untraced')
# seconds the format calls may take -- a run this long has hung
_TIMEOUT = 600
# the thread count the second engine also runs every call at
_THREADS = 'WIKI_THREADS'
# what the doctored side of a control changes behind one command, each with
# the aspects it must show: on a stream, or on the path it acts on
_DOCTORED = {
    'stdout': ('stdout',),
    'extra': ('tree', 'touched'),
    'file': ('tree',),
    'mode': ('tree',),
    'rewrite': ('touched',),
}
# the file the `extra` kind writes, beside the file the other kinds act on
_EXTRA = 'parity_doctored.md'


@dataclasses.dataclass(frozen=True, order=True)
class Finding:
    """One way a replay is not the expected one.

    Attributes:
        kind: ``failed``, ``error``, ``identity`` or ``untraced`` (the
            tests did not run as they do without the recorder);
            ``unformed`` (no command line states a call); ``unfaithful``
            (v1's command line does not do what the call did in its
            test); ``registered`` (a registry entry excused a call
            between two builds of v1); ``unneeded`` (the routing table
            names a test the replay holds); ``unrecorded`` (the tests
            ran commands, and the recorder recorded none); ``format`` (a
            format call that has no JSON form, or that gives another
            outcome when it is made again); or ``control`` (a doctored
            replay did not show what it must).
        subject: The test, the format function, the control, or the
            number of commands.
        detail: What is wrong.

    """

    kind: str
    subject: str
    detail: str = ''


@dataclasses.dataclass(frozen=True)
class Result:
    """What one replay found.

    Attributes:
        findings: Every way the replay is not the expected one.
        differences: Every difference between the sides that no entry of
            the registry explains.
        excused: The calls whose differences a registry entry explains.
            Both leave out the calls of the tests whose routing row rests
            on a registry entry.
        report: The replay as JSON data.
        streams: Both streams of every run of each call that differs or
            is unfaithful, by a file name that carries the call's process
            and number and the run.

    """

    findings: tuple[Finding, ...]
    differences: tuple[Difference, ...]
    excused: tuple[Excused, ...]
    report: Mapping[str, Any]
    streams: Mapping[str, bytes]


@dataclasses.dataclass(frozen=True)
class _Pass:
    """What one side did on one call.

    Attributes:
        name: Name of the side.
        outcome: The streams and the exit code.
        before: The manifest of the trees before the command.
        after: The manifest after it.
        final: Manifest content after it, from a scan that hashed every
            file again.
        views: For each wiki root, the caches before and after.
        settings: For each wiki root, the bytes of its settings file as
            the command found it.

    """

    name: str
    outcome: Outcome
    before: Mapping[str, Entry]
    after: Mapping[str, Entry]
    final: Mapping[str, tuple]
    views: Mapping[str, tuple[View, View]]
    settings: Mapping[str, Optional[bytes]]


@dataclasses.dataclass(frozen=True)
class _Played:
    """One call as the sides replayed it.

    Attributes:
        call: The recorded call.
        unformed: Why the call could not be replayed; empty for one that
            was.
        problems: The ways v1's command line departs from the call.
        differences: What the sides did differently.
        excused: The registry entries that explain a difference.
        passes: What each side did.

    """

    call: Call
    unformed: str = ''
    problems: tuple[str, ...] = ()
    differences: tuple[Difference, ...] = ()
    excused: tuple[Excused, ...] = ()
    passes: tuple[_Pass, ...] = ()


def run(
    recording: Recording,
    sides: Sequence[Side],
    *,
    commands: Optional[Sequence[str]] = None,
    routed: Iterable[str] = (),
    explained: Iterable[str] = (),
    second: bool = False,
    jobs: Optional[int] = None,
) -> Result:
    """Replay the recorded calls on every side and compare the sides.

    Args:
        recording: The calls, from :func:`parity.record.run`.
        sides: The sides; the first is v1, the reference the others are
            compared against and the one held to the tests' own calls.
        commands: The commands whose calls are replayed; ``None`` replays
            every call.
        routed: The ids of the tests that another check holds. A call of
            such a test is replayed and compared like any other, and is
            no finding when it has no command line or is unfaithful.
        explained: The ids among them whose row rests on a registry
            entry. What the sides differ in on a call of such a test is
            the entry's difference, and does not fail the replay.
        second: Whether the later sides are a second engine, which the
            registry may excuse. Between two builds of v1 an excused call
            is a finding.
        jobs: How many calls are replayed at once; ``None`` takes one per
            process of the recording.

    Returns:
        The findings, the differences and the report.

    """
    routed = frozenset(routed)
    explained = frozenset(explained)
    chosen = [
        call
        for call in recording.calls
        if (commands is None) or (call.command in commands)
    ]
    played = _play(chosen, sides, work=recording.store.parent, jobs=jobs)
    # gather what is not the expected result
    findings = [
        Finding(finding.kind, finding.subject)
        for finding in recording.tests.findings
        if finding.kind in _BROKEN
    ]
    differences: list[Difference] = []
    excused: list[Excused] = []
    streams: dict[str, bytes] = {}
    rows = []
    for item in played:
        call = item.call
        exempt = _is_routed(call.test, routed)
        known = _is_routed(call.test, explained)
        if item.unformed and not exempt:
            findings.append(Finding('unformed', call.test, item.unformed))
        if item.problems and not exempt:
            findings.append(Finding('unfaithful', call.test, '; '.join(item.problems)))
        if not known:
            differences.extend(item.differences)
            excused.extend(item.excused)
        if not (second or known):
            findings.extend(
                Finding('registered', call.test, f'{entry.case}: {entry.entry}')
                for entry in item.excused
            )
        if item.problems or item.differences or item.excused:
            for done in item.passes:
                stem = f'{call.worker}-{call.index} {done.name}'
                streams[f'{stem}.out'] = done.outcome.stdout
                streams[f'{stem}.err'] = done.outcome.stderr
        rows.append(_row(item, exempt))
    report = {
        'commands': None if commands is None else list(commands),
        'counts': _counts(played, routed),
        'kinds': recording.kinds,
        'calls': rows,
        'unrecorded': _unrecorded(recording),
    }
    return Result(
        findings=tuple(sorted(findings)),
        differences=tuple(differences),
        excused=tuple(excused),
        report=report,
        streams=streams,
    )


def formats(
    recording: Recording,
    source: Source,
    reference: Side,
    *,
    work: pathlib.Path,
    routed: Iterable[str] = (),
) -> tuple[list[Finding], dict[str, Any]]:
    """Make the recorded format calls again in v1 and compare the outcomes.

    Each call is made again in a fresh interpreter of the reference side,
    once under each of two hash seeds. A call that gives another outcome
    there than in its test turns on something its arguments do not hold.

    Args:
        recording: The calls, from :func:`parity.record.run`.
        source: v1's tests, whose interpreter makes the calls.
        reference: The v1 side, for its environment.
        work: New folder for the file of the calls and a scratch home.
        routed: The ids of the tests that another check holds.

    Returns:
        The findings, and the calls as JSON data: how many were made and
        how many are distinct, by function, and the tests that made a
        call which gave another outcome (``differing``).

    """
    routed = frozenset(routed)
    work.mkdir(parents=True)
    # leave out the calls with no JSON form: of an argument, or of the outcome
    formed = []
    findings = []
    for entry in recording.formats:
        unformed = entry.get('unformed') or entry['outcome'].get('unformed')
        if not unformed:
            formed.append(entry)
        elif not _all_routed(entry['tests'], routed):
            findings.append(Finding('format', entry['function'], unformed))
    # write the calls, one per line, and make them again under each seed
    path = work / 'calls.jsonl'
    with open(path, 'w', encoding='utf-8') as handle:
        for entry in formed:
            call = {key: entry[key] for key in ('function', 'args', 'kwargs')}
            handle.write(json.dumps(call) + '\n')
    home = work / 'home'
    home.mkdir()
    env = side.environment(reference, home, now='')
    del env['WIKI_FREEZE_TIME']
    differing: set[str] = set()
    for seed in _SEEDS:
        done = subprocess.run(
            [f'{source.python}', '-P', f'{_VALUES}', _FORMAT, f'{path}'],
            cwd=home,
            env={**env, 'PYTHONHASHSEED': seed},
            stdin=subprocess.DEVNULL,
            capture_output=True,
            encoding='utf-8',
            timeout=_TIMEOUT,
        )
        lines = done.stdout.splitlines()
        if (done.returncode != 0) or (len(lines) != len(formed)):
            raise RuntimeError(
                f'The format calls did not run (exit {done.returncode}):'
                f' {done.stderr[-2000:]!r}'
            )
        for entry, line in zip(formed, lines):
            same = json.loads(line) == entry['outcome']
            if not same:
                differing.update(entry['tests'])
            if (not same) and not _all_routed(entry['tests'], routed):
                detail = f'another outcome when it is made again: {entry["tests"][0]}'
                findings.append(Finding('format', entry['function'], detail))
    # count the calls by function
    functions: dict[str, dict[str, int]] = {}
    for entry in recording.formats:
        count = functions.setdefault(entry['function'], {'calls': 0, 'distinct': 0})
        count['calls'] += entry['count']
        count['distinct'] += 1
    report = {
        'calls': sum(count['calls'] for count in functions.values()),
        'distinct': len(recording.formats),
        'functions': dict(sorted(functions.items())),
        'differing': sorted(differing),
    }
    return sorted(set(findings)), report


def control(
    recording: Recording,
    reference: Side,
    name: str,
    *,
    jobs: Optional[int] = None,
) -> tuple[list[Finding], dict[str, Any]]:
    """Replay doctored calls, which the replay must report.

    ``argv`` doctors the argument mapping: every ``update`` call that
    found pending changes under ``--check`` is replayed without the
    option, so the command writes what the call only listed. Each must
    come out unfaithful. ``side`` doctors a side: the first faithful
    call that may write is replayed against a wrapper that changes one
    thing behind the command, once for each of five things: one byte of
    stdout, one more file, and one byte, the mode and the inode of a
    file the command leaves alone. Each must show as exactly the
    differences of that change.

    Args:
        recording: The calls, from :func:`parity.record.run`.
        reference: The v1 side.
        name: One of :data:`CONTROLS`.
        jobs: How many calls are replayed at once.

    Returns:
        The findings, and the control as JSON data: how many replays it
        doctored and how many the replay reported.

    """
    work = recording.store.parent
    formed = [call for call in recording.calls if not call.unformed]
    doctored = reported = 0
    if name == 'argv':
        chosen = [
            dataclasses.replace(call, argv=_without(call.argv, _CHECK))
            for call in formed
            if (call.source == 'core')
            and (call.command == 'update')
            and (_CHECK in _options(call.argv))
            and call.outcome.get('returned')
        ]
        played = _play(chosen, [reference], work=work, jobs=jobs)
        doctored = len(chosen)
        reported = sum(1 for item in played if item.problems)
    else:
        # take the first call that may write and that v1's command line
        # replays faithfully, on trees that hold a file it leaves alone
        for call in formed:
            if not _writes(call):
                continue
            (clean,) = _play([call], [reference], work=work, jobs=jobs)
            if clean.unformed or clean.problems:
                continue
            spare = _spare(clean.passes[0])
            if not spare:
                continue
            extra = posixpath.join(posixpath.dirname(spare), _EXTRA)
            for kind, aspects in _DOCTORED.items():
                # a stream has no path, and the new file goes beside the spare one
                relpath = {'stdout': '', 'extra': extra}.get(kind, spare)
                changed = _doctored(reference, kind, call, call.base / relpath)
                sides = [reference, changed]
                (played,) = _play([call], sides, work=work, jobs=jobs)
                found = [(item.aspect, item.detail) for item in played.differences]
                doctored += 1
                if sorted(found) == sorted((aspect, relpath) for aspect in aspects):
                    reported += 1
            break
    findings = []
    if not doctored:
        findings.append(Finding('control', name, 'the recording has no call to doctor'))
    elif reported != doctored:
        missed = doctored - reported
        findings.append(Finding('control', name, f'{missed} doctored calls passed'))
    return findings, {'doctored': doctored, 'reported': reported}


def unneeded(
    routed: Iterable[str],
    report: Mapping[str, Any],
    differing: Iterable[str] = (),
) -> list[Finding]:
    """Return a finding for each routed test that a whole replay holds.

    A test whose calls all replay as their commands, with no difference,
    has its place in the replay, and a row of the routing table would
    give it a second one. A row that rests on a registry entry is not
    judged this way: its tests differ against a second engine only.

    Args:
        routed: The ids the routing table names in rows that rest on no
            registry entry.
        report: The report of a replay of every call, from :func:`run`.
        differing: The tests that made a format call which gave another
            outcome when it was made again, from :func:`formats`.

    """
    ran = set()
    shown = set(differing)
    for row in report['calls']:
        ran.add(row['test'])
        if row['unformed'] or row['problems'] or row['differences']:
            shown.add(row['test'])
    ran.update([_function(test) for test in ran])
    shown.update([_function(test) for test in shown])
    return [
        Finding('unneeded', test, 'every call of the test replays as its command')
        for test in sorted(routed)
        if (test in ran) and (test not in shown)
    ]


def unrecorded(recording: Recording) -> list[Finding]:
    """Return a finding if the tests ran commands and the recorder recorded none.

    Some commands leave no record: one that git starts, and one a test
    starts without the helper. A whole recording that holds no command
    of the helper at all is another matter: the recorder did not watch
    the helper, and the replay would pass with the CLI tests left out.

    Args:
        recording: A recording of every test, from
            :func:`parity.record.run`.

    """
    count = sum(_unrecorded(recording).values())
    if (not count) or any(call.source == 'cli' for call in recording.calls):
        return []
    s = 's' if count != 1 else ''
    return [Finding('unrecorded', f'{count} command{s}')]


def threaded(engine: Side) -> Side:
    """Return ``engine`` as a side that runs every command on one thread."""
    return dataclasses.replace(
        engine,
        name=f'{engine.name} on one thread',
        env={**engine.env, _THREADS: '1'},
    )


# ------ helper functions


def _unrecorded(recording: Recording) -> dict[str, int]:
    """Return how many commands of each test reached a side and left no record.

    The dispatcher counts every command a test sends to a side, and the
    recorder every command the helper starts; the tests that show a gap
    are listed with it.
    """
    recorded = collections.Counter(
        call.test for call in recording.calls if call.source == 'cli'
    )
    result = {}
    for row in recording.tests.report['tests']:
        count = sum(row['calls'].values()) - recorded[row['id']]
        if count:
            result[row['id']] = count
    return result


def _play(
    calls: Sequence[Call],
    sides: Sequence[Side],
    *,
    work: pathlib.Path,
    jobs: Optional[int],
) -> list[_Played]:
    """Replay ``calls`` on every side, the calls of one process in order.

    Two calls of one process may name the same paths, so they run one
    after the other; the calls of two processes share no path.
    ``work`` is the folder of the recording: every call's trees are
    placed below it, and it holds the scratch home the reference side's
    judge runs under.
    """
    judge = None
    if (len(sides) > 1) and sides[0].judge:
        home = work / 'home'
        home.mkdir(parents=True, exist_ok=True)
        judge = Judge(sides[0].judge, env=side.environment(sides[0], home, now=''))
    lock = threading.Lock()
    groups: dict[pathlib.Path, list[Call]] = collections.defaultdict(list)
    for call in calls:
        groups[call.base].append(call)

    def play(group: Sequence[Call]) -> list[_Played]:
        """Replay the calls of one process."""
        return [
            _play_one(call, sides, work=work, judge=judge, lock=lock) for call in group
        ]

    played: list[_Played] = []
    workers = jobs or max(len(groups), 1)
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for group in pool.map(play, groups.values()):
            played.extend(group)
    return sorted(played, key=lambda item: (item.call.worker, item.call.index))


def _play_one(
    call: Call,
    sides: Sequence[Side],
    *,
    work: pathlib.Path,
    judge: Optional[Judge],
    lock: threading.Lock,
) -> _Played:
    """Replay one call on every side, and judge the replay."""
    if call.unformed:
        return _Played(call=call, unformed=call.unformed)
    # NOTE: the trees are removed and placed again at the path the record
    #   names, so a record names no path outside the recording's own folder
    if work not in call.base.parents:
        return _Played(call=call, unformed="the call's trees lie outside the recording")
    # read what the call left in its test, placed where the call ran
    _place(call.after, call.base)
    left = _compared(manifest.scan(call.base))
    passes = []
    notice = None
    try:
        for runner in sides:
            done = _pass(call, runner, notice=notice)
            # the reference's command says whether the clock was read
            if notice is None:
                notice = done.outcome.notice
                done = dataclasses.replace(
                    done,
                    outcome=dataclasses.replace(done.outcome, notice=True),
                )
            passes.append(done)
    except OSError as e:
        return _Played(call=call, unformed=f'the command does not start: {e}')
    finally:
        _clear(call.base)
    problems = _faithful(call, passes[0], left, noticed=bool(notice))
    # compare the sides, once for each wiki whose caches a side holds
    roots = sorted({root for done in passes for root in done.views})
    folders = [f'{root}/{CACHE}' for root in roots]
    codes = frozenset(done.outcome.code for done in passes)
    case = Case(
        name=f'{call.worker}-{call.index} {call.command}',
        args=call.argv,
        codes=codes,
        writes=_writes(call),
        notice=bool(notice),
    )
    stage = Stage(name=call.test, cases=(case,))
    differences: dict[Difference, None] = {}
    excused: dict[Excused, None] = {}
    for root in roots or [None]:
        folder = '' if root is None else f'{root}/{CACHE}'
        others = [other for other in folders if other != folder]
        runs = [_as_run(done, root, others) for done in passes]
        found = compare.stage(stage, runs, folder=folder)
        with lock:
            found, explained = classify.apply(stage, runs, found, judge=judge)
        differences.update(dict.fromkeys(found))
        excused.update(dict.fromkeys(explained))
    return _Played(
        call=call,
        problems=tuple(problems),
        differences=tuple(differences),
        excused=tuple(excused),
        passes=tuple(passes),
    )


def _pass(call: Call, runner: Side, *, notice: Optional[bool]) -> _Pass:
    """Run the command line of ``call`` on ``runner``, on the call's own trees.

    Args:
        call: The call.
        runner: The side.
        notice: Whether the command reads the clock; ``None`` for the
            reference, whose stderr says so.

    """
    _place(call.before, call.base)
    before = manifest.scan(call.base)
    # find the wikis whose caches the command may read
    known = set(_roots(before))
    if (call.root is not None) and pathlib.Path(call.root).is_relative_to(call.base):
        known.add(pathlib.Path(call.root).relative_to(call.base).as_posix())
    first = {name: cache.view(call.base / name, _below(before, name)) for name in known}
    settings = {name: _settings(call.base / name) for name in known}
    # run the command under the call's own environment
    env = {**call.env, **runner.env}
    env.pop(side.TRACE, None)
    outcome = side.run(
        runner,
        call.argv,
        cwd=pathlib.Path(call.cwd),
        env=env,
        notice=True if notice is None else notice,
    )
    after = manifest.scan(call.base, previous=before)
    views = {}
    for name in sorted({*known, *_roots(after)}):
        start = first.get(name) or cache.view(call.base / name, {})
        end = cache.view(call.base / name, _below(after, name), previous=start)
        views[name] = (start, end)
    # hash every file again: the scan above trusts a file's stat
    final = {
        relpath: entry.content for relpath, entry in manifest.scan(call.base).items()
    }
    return _Pass(
        name=runner.name,
        outcome=outcome,
        before=before,
        after=after,
        final=final,
        views=views,
        settings=settings,
    )


def _as_run(done: _Pass, root: Optional[str], others: Sequence[str]) -> Run:
    """Return one side's pass as a run of one command, for the wiki at ``root``.

    The caches of the other wikis are left out: each is compared in the
    run of its own wiki.
    """

    def kept(relpath: str) -> bool:
        """Return whether the run is compared on ``relpath``."""
        within = any(compare._within(relpath, other) for other in others)
        return _is_compared(relpath) and not within

    blank = cache.view(pathlib.Path(), {})
    start, end = done.views.get(root or '', (blank, blank))
    record = Record(
        outcome=done.outcome,
        touched=tuple(filter(kept, manifest.touched(done.before, done.after))),
        changes={
            relpath: content
            for relpath, content in manifest.changes(done.before, done.after).items()
            if kept(relpath)
        },
        view=end,
        settings=done.settings.get(root or ''),
    )
    return Run(
        name=done.name,
        baseline={
            relpath: entry.content
            for relpath, entry in done.before.items()
            if kept(relpath)
        },
        view=start,
        records=(record,),
        final={
            relpath: content for relpath, content in done.final.items() if kept(relpath)
        },
    )


def _doctored(reference: Side, kind: str, call: Call, path: pathlib.Path) -> Side:
    """Return ``reference`` as a side that changes one thing behind one command.

    Args:
        reference: The side that runs every command.
        kind: What the side changes, one of :data:`parity.doctor.KINDS`.
        call: The call whose command is doctored.
        path: The file the kind acts on.

    """
    # -P keeps the harness's own modules off the wrapper's import path
    argv = (
        sys.executable,
        '-P',
        f'{doctor.__file__}',
        kind,
        json.dumps(list(call.argv)),
        f'{path}',
        f'{len(reference.argv)}',
        *reference.argv,
    )
    return dataclasses.replace(reference, name=f'doctored {kind}', argv=argv)


def _spare(done: _Pass) -> str:
    """Return a file the command of ``done`` left alone, for a doctored side to act on.

    The file is one the sides are compared on by the tree rules: outside
    every cache and every repository's own folder.

    Returns:
        The path relative to the temporary folder; empty for trees that
        hold no such file.

    """
    touched = set(manifest.touched(done.before, done.after))
    for relpath, entry in sorted(done.after.items()):
        if (entry.kind != 'file') or (relpath in touched):
            continue
        if _is_compared(relpath) and not _is_cache(relpath):
            return relpath
    return ''


def _faithful(
    call: Call,
    done: _Pass,
    left: Mapping[str, Entry],
    *,
    noticed: bool,
) -> list[str]:
    """Return the ways v1's command line departs from what ``call`` did.

    Args:
        call: The call.
        done: What the reference did on the call's command line.
        left: The manifest of the trees as the call left them in its test.
        noticed: Whether the reference's stderr opened with the clock
            notice.

    """
    problems = []
    outcome = done.outcome
    if call.source == 'cli':
        problems.extend(_same_command(call, outcome, noticed=noticed))
    elif 'raised' in call.outcome:
        raised = call.outcome['raised']
        error = _ERROR.format(text=call.outcome['text'])
        if not call.outcome['error']:
            problems.append(f'the call ended with {raised}, which no command reports')
        elif outcome.code != 2:
            problems.append(
                f'the call raised {raised}, the command exited {outcome.code}'
            )
        elif not _text(outcome.stderr).endswith(error):
            problems.append(f'the command reports another error than {raised}')
    else:
        problems.extend(_same_result(call, outcome))
    # the trees: what the command left, and which paths it wrote
    after = _compared(done.after)
    for relpath in sorted(set(left) | set(after)):
        if _is_cache(relpath):
            continue
        old, new = left.get(relpath), after.get(relpath)
        if (old is None) or (new is None) or (old.content != new.content):
            problems.append(f'tree: {relpath}')
    before = _compared(done.before)
    written = set(_written(before, left)) ^ set(_written(before, after))
    problems.extend(
        f'written: {relpath}' for relpath in sorted(written) if not _is_cache(relpath)
    )
    return problems


def _same_command(call: Call, outcome: Outcome, *, noticed: bool) -> list[str]:
    """Return how a command differs from the same command in its test."""
    problems = []
    recorded = call.outcome
    if outcome.code != recorded['code']:
        problems.append(f'exit code {outcome.code}, in the test {recorded["code"]}')
    stderr = (NOTICE if noticed else b'') + outcome.stderr
    streams = {'stdout': outcome.stdout, 'stderr': stderr}
    for name, stream in streams.items():
        # read the stream as the helper did: as text, with one kind of line end
        if recorded['encoding'] is None:
            text = stream.decode('latin-1')
        else:
            text = stream.decode(recorded['encoding'], 'replace')
            text = text.replace('\r\n', '\n').replace('\r', '\n')
        if text != recorded[name]:
            problems.append(name)
    return problems


def _same_result(call: Call, outcome: Outcome) -> list[str]:
    """Return how a command's output differs from what its engine call returned."""
    problems = []
    result = call.outcome['returned']
    stdout = _text(outcome.stdout)
    code = 0
    shown = True
    if call.command == 'update':
        if _CHECK in _options(call.argv):
            code = 1 if result else 0
            lines = stdout.splitlines()
            listed = [line[len(_WOULD) :] for line in lines if line.startswith(_WOULD)]
            shown = listed == result
        else:
            count = len(result)
            s = 's' if count != 1 else ''
            shown = stdout == (_UPDATED.format(count=count, s=s) if count else _NOTHING)
    elif call.command == 'lint':
        code = 1 if result else 0
        shown = _json(stdout, 'issues') == result
    elif call.command == 'read':
        shown = stdout == result
    elif call.command == 'map':
        shown = stdout == f'{result}\n' if result else stdout in ('', _EMPTY)
    elif call.command == 'new':
        shown = stdout == _CREATED.format(name=result)
    elif call.command == 'search':
        code = 0 if result else 1
        found = _json(stdout, None)
        shown = (not result) or ((found is not None) and (len(found) == result))
    elif call.command == 'match':
        code = 0 if result else 1
        shown = (not result) or (stdout.count('\n') == result)
    if outcome.code != code:
        problems.append(f'exit code {outcome.code}, the call asks for {code}')
    elif not shown:
        problems.append('the command prints another result than the call returned')
    # every notice of the call is in the command's report, in the call's order
    if call.command in _NARRATED:
        report = _text(outcome.stderr)
        place = 0
        for notice in call.notices:
            place = report.find(f'{notice}\n', place)
            if place < 0:
                problems.append(
                    'the command prints other notices than the call emitted'
                )
                break
            place += len(notice) + 1
    elif call.command == 'lint':
        notes = iter(_json(stdout, 'notes') or [])
        if not all(notice in notes for notice in call.notices):
            problems.append('the command prints other notices than the call emitted')
    return problems


def _json(text: str, member: Optional[str]) -> Optional[list[Any]]:
    """Return the list a command printed as JSON: the texts of ``member``, or all.

    Returns:
        The list; ``None`` for output that is no such JSON.

    """
    try:
        data = json.loads(text)
        if member is None:
            return list(data)
        return [row['text'] for row in data[member]]
    except (ValueError, TypeError, KeyError):
        return None


def _written(before: Mapping[str, Entry], after: Mapping[str, Entry]) -> list[str]:
    """Return the paths a call wrote, as two copies of its trees show them.

    A copy keeps a path's modification time and not its inode, so a path
    is written when it was made or removed, or when it is no folder and
    its modification time or its content changed. The trees of a test and
    the trees of a replayed command are both read this way, so the two
    are held to one rule.
    """
    result = []
    for relpath in sorted(set(before) | set(after)):
        old, new = before.get(relpath), after.get(relpath)
        if (old is None) or (new is None):
            result.append(relpath)
        elif (old.kind == 'dir') and (new.kind == 'dir'):
            continue
        elif (old.mtime_ns != new.mtime_ns) or (old.content != new.content):
            result.append(relpath)
    return result


def _place(copy: Copy, base: pathlib.Path) -> None:
    """Put the trees of ``copy`` at their own paths below ``base``, and nothing else."""
    _clear(base)
    base.mkdir(parents=True)
    for name in copy.names:
        snapshot.clone(copy.folder / name, base / name)
    # seal the deepest paths first: a sealed folder hides what is below it
    for relpath, mode in sorted(copy.sealed, key=lambda item: -item[0].count('/')):
        os.chmod(base / relpath, mode)


def _clear(folder: pathlib.Path) -> None:
    """Remove ``folder`` and all below it, sealed folders included."""
    if not os.path.lexists(folder):
        return
    pending = [f'{folder}']
    while pending:
        path = pending.pop()
        os.chmod(path, 0o700)
        with os.scandir(path) as listing:
            pending.extend(
                item.path for item in listing if item.is_dir(follow_symlinks=False)
            )
    shutil.rmtree(folder)


def _settings(root: pathlib.Path) -> Optional[bytes]:
    """Return the bytes of the settings file of the wiki at ``root``, if it can be read."""
    try:
        return (root / WIKI_SETTINGS).read_bytes()
    except OSError:
        return None


def _roots(entries: Mapping[str, Entry]) -> list[str]:
    """Return the wiki roots whose cache folder ``entries`` list."""
    tail = f'/{CACHE}'
    return [
        relpath[: -len(tail)]
        for relpath, entry in entries.items()
        if relpath.endswith(tail) and (entry.kind == 'dir')
    ]


def _below(entries: Mapping[str, Entry], root: str) -> dict[str, Entry]:
    """Return the entries of the wiki at ``root``, keyed by path relative to it."""
    prefix = f'{root}/'
    return {
        relpath[len(prefix) :]: entry
        for relpath, entry in entries.items()
        if relpath.startswith(prefix)
    }


def _compared(entries: Mapping[str, Entry]) -> dict[str, Entry]:
    """Return ``entries`` without the paths no side is compared on."""
    return {
        relpath: entry for relpath, entry in entries.items() if _is_compared(relpath)
    }


def _is_compared(relpath: str) -> bool:
    """Return whether ``relpath`` lies outside every repository's own state."""
    return _GIT not in relpath.split('/')


def _is_cache(relpath: str) -> bool:
    """Return whether ``relpath`` is a wiki's cache folder or lies below one."""
    return f'/{relpath}/'.find(f'/{CACHE}/') >= 0


def _writes(call: Call) -> bool:
    """Return whether the command of ``call`` may write outside the caches."""
    if call.command == 'update':
        return _CHECK not in _options(call.argv)
    return call.command not in READ_ONLY


def _options(argv: Sequence[str]) -> list[str]:
    """Return the arguments of ``argv`` before the mark that ends its options."""
    items = list(argv)
    if _END in items:
        return items[: items.index(_END)]
    return items


def _without(argv: Sequence[str], option: str) -> tuple[str, ...]:
    """Return ``argv`` without the option ``option``."""
    count = len(_options(argv))
    head = [item for item in argv[:count] if item != option]
    return (*head, *argv[count:])


def _text(stream: bytes) -> str:
    """Return a stream as the text v1 wrote."""
    return stream.decode('utf-8', 'replace')


def _function(test: str) -> str:
    """Return the id of a test without its parameters: the test function's."""
    return test.partition('[')[0]


def _is_routed(test: str, routed: frozenset[str]) -> bool:
    """Return whether the routing table names ``test`` or its test function."""
    return (test in routed) or (_function(test) in routed)


def _all_routed(tests: Sequence[str], routed: frozenset[str]) -> bool:
    """Return whether every one of ``tests`` is routed."""
    return all(_is_routed(test, routed) for test in tests)


def _row(item: _Played, exempt: bool) -> dict[str, Any]:
    """Return one replayed call as JSON data."""
    call = item.call
    runs = {}
    for done in item.passes:
        runs[done.name] = {
            'exit': done.outcome.code,
            'stdout': _stream_report(done.outcome.stdout),
            'stderr': _stream_report(done.outcome.stderr),
            'touched': len(manifest.touched(done.before, done.after)),
        }
    return {
        'test': call.test,
        'call': f'{call.worker}-{call.index}',
        'source': call.source,
        'command': call.command,
        'argv': list(call.argv),
        'unformed': item.unformed,
        'problems': list(item.problems),
        'routed': exempt,
        'runs': runs,
        'differences': [dataclasses.asdict(found) for found in item.differences],
        'excused': [dataclasses.asdict(entry) for entry in item.excused],
    }


def _counts(played: Sequence[_Played], routed: frozenset[str]) -> dict[str, Any]:
    """Return how much was replayed, as JSON data."""
    commands: dict[str, dict[str, int]] = {}
    tests: dict[str, int] = collections.Counter()
    for item in played:
        key = f'{item.call.source} {item.call.command}'
        count = commands.setdefault(
            key,
            {'calls': 0, 'replayed': 0, 'unformed': 0, 'unfaithful': 0},
        )
        count['calls'] += 1
        if item.unformed:
            count['unformed'] += 1
        elif item.problems:
            count['unfaithful'] += 1
        else:
            count['replayed'] += 1
            tests[item.call.test] += 1
    called = {item.call.test for item in played}
    held = {test for test in called if _is_routed(test, routed)}
    total = {
        name: sum(count[name] for count in commands.values())
        for name in ('calls', 'replayed', 'unformed', 'unfaithful')
    }
    return {
        **total,
        'tests': len(called),
        'tests_replayed': len(tests),
        'tests_routed': len(held),
        'differing': sum(1 for item in played if item.differences),
        'commands': dict(sorted(commands.items())),
    }


def _stream_report(stream: bytes) -> dict[str, Any]:
    """Return the size and digest of one stream."""
    return {'bytes': len(stream), 'sha256': hashlib.sha256(stream).hexdigest()}
