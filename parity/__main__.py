"""Command-line interface for ``parity``.

usage: python -m parity matrix --a NAME --b NAME [--registered] [--sides FILE] --corpora FILE --work DIR --out DIR
       python -m parity selfcheck --side NAME [--sides FILE] --corpora FILE --work DIR --out DIR
       python -m parity registry --side NAME [--sides FILE] --corpora FILE --work DIR --out DIR
       python -m parity suite [--engine NAME] [--sides FILE] --work DIR --out DIR
       python -m parity replay [--engine NAME] [--sides FILE] --work DIR --out DIR
       python -m parity oracle ensure [--side NAME]
       python -m parity oracle check [--skip NAME]

``matrix`` compares two sides over the matrix; ``selfcheck`` runs one side
against itself, clean and doctored; ``registry`` runs one side against a
stand-in on the fixtures of the registry's classifiers. Each exits 0 when
every result is the expected one, and 1 otherwise. A run that would compare
nothing -- no corpus, an unknown corpus, stage or side, one side named
twice, a sides or corpora file of the wrong shape, a ``--now`` no engine
takes, a checkout or a work folder the harness refuses, a side or a
``describe`` command that does not start, a side that does not prove its
identity, a v1 side the oracle rules refuse, a registry file the harness
cannot hold to its classifiers -- exits 2 before it snapshots anything. A
side is one the sides file declares, or a v1 side of ``oracle.toml``.
``registry --release`` also exits 1 while an entry of the registry is not
ready for a release.

``suite`` runs v1's CLI tests and the terminal table through the dispatcher,
which sends the calls of the ported commands to the side ``--engine`` names
and every other call to v1. It exits 0 when every test passes on the sides
its calls went to and the terminal table shows no difference, and 1
otherwise. It exits 2, before it runs a test, on an unknown side, on an
engine that no ported command would reach, and on a work folder the harness
refuses.

``replay`` runs v1's core and CLI tests under a recorder, then runs each
call they made again as a command line, on the trees the call found. With
no ``--engine`` it replays every call on v1 under two hash seeds, makes the
recorded format calls again and runs its controls; with one it replays the
calls of the ported commands on v1 and on that side. It exits 0 when the
sides differ in nothing and v1's command lines do what the calls did in
their tests, and 1 otherwise. It exits 2 as ``suite`` does.

The registry lists what a second engine may do differently from v1, and two
builds of v1 have nothing registered between them. ``matrix`` therefore
counts a command the registry excuses as a result that is not the expected
one, unless ``--registered`` says side ``b`` is a second engine.

``oracle ensure`` builds the oracle checkout and the venvs of its sides, and
exits 2 when it refuses a checkout or a venv. ``oracle check`` holds
``oracle.toml`` level with v1 and exits 1 when a check fails.

An error of the harness itself prints its traceback and exits 2 as well, so
exit 1 always means that a result is not the expected one.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import logging
import pathlib
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import traceback
from collections.abc import Mapping, Sequence
from typing import Any, Optional, Union

from . import (
    config,
    dispatch,
    oracle,
    ported,
    record,
    registry,
    replay,
    routing,
    runner,
    selfcheck,
    side,
    snapshot,
    suite,
    terminal,
)
from .classify import Excused
from .compare import Difference
from .matrix import STAGES
from .oracle import CHECKS
from .runner import Corpus
from .side import Side

__all__ = []

# the instant every side's clock is fixed at, unless the caller names one
_NOW = '2026-09-18T12:34:56Z'
# the one form the engines take WIKI_FREEZE_TIME in: an RFC 3339 date-time
# with a capital T, no leap second and at most six fraction digits
_INSTANT = re.compile(
    r'[0-9]{4}-[0-9]{2}-[0-9]{2}'
    r'T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](?:\.[0-9]{1,6})?'
    r'(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])'
)
# the UTC years such an instant may fall in
_YEARS = (1001, 9998)
# what --help prints above the commands, with the exit codes a gate reads
_DESCRIPTION = """\
Run two wiki engines on the same trees and report what they do differently.

matrix, selfcheck, registry, suite and replay exit 0 when every result is the
expected one, and 1 otherwise. A run that would compare nothing exits 2 with a message,
before it snapshots anything or runs a test. oracle ensure exits 2 when it
refuses a checkout or a venv, and oracle check exits 1 when a check fails. An
error of the harness itself prints its traceback and exits 2.
"""
# the folder of the CLI suite below the work folder, and its result file
_SUITE = 'suite'
# the same for the replay
_REPLAY = 'replay'


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the harness and return its exit code."""
    # construct parser
    parser = argparse.ArgumentParser(
        prog='python -m parity',
        description=_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = parser.add_subparsers(dest='command', required=True)
    summaries = {
        'matrix': 'Compare two sides over the command matrix.',
        'selfcheck': 'Check the harness with one side, clean and doctored.',
        'registry': 'Check the classifiers of the registry with one side.',
    }
    for name, summary in summaries.items():
        command = commands.add_parser(name, help=summary, description=summary)
        # sides file option
        sides_help = 'TOML file with one table per side, beside the v1 sides.'
        command.add_argument('--sides', type=pathlib.Path, help=sides_help)
        # corpora file option
        corpora_help = 'TOML file with one table per corpus.'
        command.add_argument(
            '--corpora',
            type=pathlib.Path,
            required=True,
            help=corpora_help,
        )
        # corpus label option
        corpus_help = 'Label of a corpus to run on (repeatable; default: every corpus).'
        command.add_argument('--corpus', action='append', help=corpus_help)
        # work folder option
        work_help = 'Folder the harness makes one new folder per corpus in.'
        command.add_argument('--work', type=pathlib.Path, required=True, help=work_help)
        # output folder option
        out_help = 'Folder for the result files and the saved streams.'
        command.add_argument('--out', type=pathlib.Path, required=True, help=out_help)
        # fixed instant option
        now_help = f'RFC 3339 instant every side runs at (default: {_NOW}).'
        command.add_argument('--now', type=_instant, default=_NOW, help=now_help)
        if name == 'registry':
            # release flag
            release_help = 'Also fail while an entry is not ready for a release.'
            command.add_argument('--release', action='store_true', help=release_help)
        else:
            # sample seed option
            seed_help = 'Seed of the matrix sample (default: 0).'
            command.add_argument('--seed', type=int, default=0, help=seed_help)
        if name == 'matrix':
            # reference side option
            a_help = 'Name of the side the other is compared against.'
            command.add_argument('--a', required=True, help=a_help)
            # compared side option
            b_help = 'Name of the side compared against the first.'
            command.add_argument('--b', required=True, help=b_help)
            # second engine flag
            registered_help = 'Pass what the registry excuses: b is a second engine.'
            command.add_argument(
                '--registered',
                action='store_true',
                help=registered_help,
            )
            # stage name option
            stage_help = 'Name of a stage to run (repeatable; default: every stage).'
            command.add_argument(
                '--stage',
                action='append',
                choices=STAGES,
                help=stage_help,
            )
            # keep flag
            keep_help = 'Leave the snapshot and every tree in the work folder.'
            command.add_argument('--keep', action='store_true', help=keep_help)
        else:
            # side name option
            side_help = 'Name of the side to run against itself or a stand-in.'
            command.add_argument('--side', required=True, help=side_help)
    summaries = {
        _SUITE: "Run v1's CLI tests and the terminal table through the dispatcher.",
        _REPLAY: "Record the calls of v1's tests and replay them as command lines.",
    }
    for name, summary in summaries.items():
        command = commands.add_parser(name, help=summary, description=summary)
        # engine name option
        engine_help = 'Name of the side that takes the calls of the ported commands.'
        command.add_argument('--engine', help=engine_help)
        # sides file option
        sides_help = 'TOML file with one table per side, beside the v1 sides.'
        command.add_argument('--sides', type=pathlib.Path, help=sides_help)
        # work folder option
        work_help = 'Folder the harness makes one new folder in.'
        command.add_argument('--work', type=pathlib.Path, required=True, help=work_help)
        # output folder option
        out_help = "Folder for the result file, pytest's output and the saved streams."
        command.add_argument('--out', type=pathlib.Path, required=True, help=out_help)
        # test option
        test_help = (
            'A test file or test to run, as pytest names it from the oracle'
            ' checkout (repeatable; default: every test of the command).'
        )
        command.add_argument('--test', action='append', help=test_help)
        # jobs option
        jobs_help = "How many processes run the tests (default: v1's pytest settings)."
        command.add_argument('--jobs', type=int, help=jobs_help)
    summary = 'Build the oracle checkout, or hold oracle.toml level with v1.'
    command = commands.add_parser('oracle', help=summary, description=summary)
    actions = command.add_subparsers(dest='action', required=True)
    summary = 'Build the oracle checkout and the venvs of its sides.'
    action = actions.add_parser('ensure', help=summary, description=summary)
    # side name option
    side_help = 'Name of a v1 side to build (repeatable; default: every side).'
    action.add_argument('--side', action='append', help=side_help)
    summary = 'Check that oracle.toml is level with v1.'
    action = actions.add_parser('check', help=summary, description=summary)
    # skipped check option
    skip_help = 'Name of a check to leave out (repeatable).'
    action.add_argument(
        '--skip',
        action='append',
        choices=CHECKS,
        default=[],
        help=skip_help,
    )
    # parse arguments
    options = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    # NOTE: an uncaught exception exits 1, the code that says a result is not
    #   the expected one, so an error of the harness itself is caught here
    try:
        return _run(parser, options)
    except Exception:
        traceback.print_exc()
        return 2


# ------ helper functions


def _instant(value: str) -> str:
    """Return ``value``, a ``--now`` argument, if it is an instant the engines take.

    An engine refuses any other value on every command, so both sides of
    such a run would fail alike without running a wiki command; an empty
    one leaves the clock unfixed. Surrounding whitespace, which an engine
    strips, is refused too: the result file records the instant as given.

    Raises:
        argparse.ArgumentTypeError: If ``value`` is not an RFC 3339
            date-time in the form ``WIKI_FREEZE_TIME`` takes, or names no
            instant in the allowed UTC years.

    """
    first, last = _YEARS
    message = (
        f'The instant must be an RFC 3339 date-time in the UTC years {first}'
        f' to {last}, like {_NOW}, got {value!r}.'
    )
    # NOTE: fromisoformat reads more forms than this one, and which ones turns
    #   on the Python version: the pattern fixes the form, and fromisoformat
    #   only checks the calendar date
    if _INSTANT.fullmatch(value) is None:
        raise argparse.ArgumentTypeError(message)
    try:
        instant = dt.datetime.fromisoformat(value).astimezone(dt.UTC)
    except (ValueError, OverflowError):
        raise argparse.ArgumentTypeError(message) from None
    if not (first <= instant.year <= last):
        raise argparse.ArgumentTypeError(message)
    return value


def _run(parser: argparse.ArgumentParser, options: argparse.Namespace) -> int:
    """Run the parsed command and return the exit code."""
    if options.command == 'oracle':
        return _oracle(parser, options)
    if options.command == _SUITE:
        return _suite(parser, options)
    if options.command == _REPLAY:
        return _replay(parser, options)
    # read the registry
    try:
        entries = registry.load()
    except ValueError as e:
        parser.error(f'{e}')
    # read the sides and the corpora
    try:
        declared = {}
        if options.sides is not None:
            declared = config.load_sides(options.sides)
        corpora = config.load_corpora(options.corpora)
    except ValueError as e:
        parser.error(f'{e}')
    # resolve the corpora to run on
    labels = options.corpus or list(corpora)
    unknown = [label for label in labels if label not in corpora]
    if unknown:
        parser.error(f'{options.corpora} declares no corpus named {unknown!r}.')
    if not labels:
        parser.error(f'{options.corpora} declares no corpus.')
    # resolve the sides to run
    if options.command == 'matrix':
        if options.a == options.b:
            parser.error(f'--a and --b both name {options.a!r}.')
        names = [options.a, options.b]
    else:
        names = [options.side]
    try:
        chosen = _sides(names, declared, path=options.sides)
    except ValueError as e:
        parser.error(f'{e}')
    # refuse every corpus the harness cannot snapshot before the first one runs
    try:
        for label in labels:
            runner.validate(corpora[label], work=options.work / label)
    except ValueError as e:
        parser.error(f'{e}')
    # probe every side before anything is snapshotted
    try:
        header = _header(chosen, now=options.now)
    except ValueError as e:
        parser.error(f'{e}')
    header['registry'] = {id: entry.status for id, entry in entries.items()}
    # run each corpus and write its result file
    options.out.mkdir(parents=True, exist_ok=True)
    failed = False
    # list the entries that are not ready for a release
    if options.command == 'registry':
        waits = registry.unready(entries)
        for line in waits:
            print(f'registry: {line}')
        failed = options.release and bool(waits)
    for label in labels:
        corpus = corpora[label]
        work = options.work / label
        if options.command == 'matrix':
            document, passed = _matrix(corpus, chosen, work=work, options=options)
        else:
            base, *_ = chosen
            document, passed = _selfcheck(corpus, base, work=work, options=options)
        document = {**header, **document}
        text = json.dumps(document, indent=2)
        (options.out / f'{label}.json').write_text(text + '\n', encoding='utf-8')
        failed = failed or not passed
    if failed:
        return 1
    return 0


def _oracle(parser: argparse.ArgumentParser, options: argparse.Namespace) -> int:
    """Build the oracle, or check its file, and return the exit code."""
    named = oracle.load()
    if options.action == 'check':
        verdicts = oracle.check(named, skip=options.skip)
        for verdict in verdicts:
            result = 'passed' if verdict.passed else 'FAILED'
            print(f'{verdict.name}: {result}: {verdict.detail}')
        if all(verdict.passed for verdict in verdicts):
            return 0
        return 1
    unknown = [name for name in options.side or () if name not in named.builds]
    if unknown:
        parser.error(f'{named.path.name} names no side {unknown!r}.')
    try:
        oracle.ensure(named, names=options.side)
    except ValueError as e:
        parser.error(f'{e}')
    return 0


def _suite(parser: argparse.ArgumentParser, options: argparse.Namespace) -> int:
    """Run the CLI suite and return the exit code."""
    # read the ported commands and the sides
    try:
        commands = ported.load()
        declared = {}
        if options.sides is not None:
            declared = config.load_sides(options.sides)
    except ValueError as e:
        parser.error(f'{e}')
    # resolve the sides: v1, and the engine if the run names one
    names = [suite.REFERENCE]
    if options.engine is not None:
        if options.engine == suite.REFERENCE:
            parser.error(f'--engine names {options.engine!r}, the side v1 runs as.')
        if not commands:
            parser.error(
                f'{ported.PATH.name} lists no command, so no call would reach'
                f' {options.engine!r}.'
            )
        names.append(options.engine)
    try:
        chosen = _sides(names, declared, path=options.sides)
    except ValueError as e:
        parser.error(f'{e}')
    # refuse the work folder and probe every side before a test runs. pytest
    # runs in the oracle checkout, so it is handed the folder's full path
    work = options.work.resolve() / _SUITE
    try:
        suite.validate(work)
        header = _header(chosen, now=_NOW)
    except ValueError as e:
        parser.error(f'{e}')
    # the tests bring their own clock, and the terminal table reads none
    del header['now']
    reference, *engines = chosen
    work.mkdir(parents=True)
    dispatcher = dispatch.build(
        work / 'bin',
        reference=reference,
        engine=engines[0] if engines else None,
        commands=commands,
    )
    result = suite.run(
        suite.source(oracle.load()),
        dispatcher,
        work=work / 'tests',
        only=options.test or (),
        jobs=options.jobs,
    )
    # write the result file and pytest's output before the terminal table
    # runs, so an error of the table leaves the tests' result behind
    document = {**header, 'ported': list(dispatcher.commands), **result.report}
    options.out.mkdir(parents=True, exist_ok=True)
    path = options.out / f'{_SUITE}.json'
    path.write_text(json.dumps(document, indent=2) + '\n', encoding='utf-8')
    _save({'pytest.txt': result.log}, options.out / _SUITE)
    table = terminal.run(reference, dispatcher, work=work / 'terminal')
    # add the table to the result file, and write the streams that differ
    document['terminal'] = {
        'cases': table.report,
        'differences': [dataclasses.asdict(item) for item in table.differences],
    }
    path.write_text(json.dumps(document, indent=2) + '\n', encoding='utf-8')
    _save(table.streams, options.out / _SUITE)
    # print how much ran on each side, then what is not the expected result
    counts = result.report['counts']
    outcomes = ', '.join(
        f'{count} {outcome}' for outcome, count in counts['outcomes'].items()
    )
    left = len(result.report['deselected'])
    print(f'{_SUITE}: {counts["tests"]} tests: {outcomes}; {left} left out')
    for name, count in {**counts['sides'], **counts['commands']}.items():
        print(f'{_SUITE}: {name}: {count["calls"]} calls of {count["tests"]} tests')
    print(f'{_SUITE}: {counts["uncalled"]} tests called no side')
    for finding in result.findings:
        print(f'  {finding.kind}: {finding.subject}')
    count = len(table.differences)
    s = 's' if count != 1 else ''
    print(f'{terminal.STAGE}: {count} difference{s}')
    for difference in table.differences:
        line = _line(difference)
        print(f'  {line}')
    if result.findings or table.differences:
        return 1
    shutil.rmtree(work)
    return 0


def _replay(parser: argparse.ArgumentParser, options: argparse.Namespace) -> int:
    """Record the calls of v1's tests, replay them, and return the exit code."""
    # read the registry, the routing table, the ported commands and the sides
    try:
        entries = registry.load()
        routes = routing.load(entries=entries)
        commands = ported.load()
        declared = {}
        if options.sides is not None:
            declared = config.load_sides(options.sides)
    except ValueError as e:
        parser.error(f'{e}')
    # resolve the sides: v1, and the engine if the run names one
    names = [suite.REFERENCE]
    if options.engine is not None:
        if options.engine == suite.REFERENCE:
            parser.error(f'--engine names {options.engine!r}, the side v1 runs as.')
        if not commands:
            parser.error(
                f'{ported.PATH.name} lists no command, so no call would reach'
                f' {options.engine!r}.'
            )
        names.append(options.engine)
    try:
        chosen = _sides(names, declared, path=options.sides)
    except ValueError as e:
        parser.error(f'{e}')
    # refuse the work folder and probe every side before a test runs
    work = options.work.resolve() / _REPLAY
    try:
        suite.validate(work)
        header = _header(chosen, now=_NOW)
    except ValueError as e:
        parser.error(f'{e}')
    # each call brings its own clock
    del header['now']
    reference, *engines = chosen
    work.mkdir(parents=True)
    # record the calls with v1 alone: a record is what v1 did in the test
    named = suite.source(oracle.load())
    dispatcher = dispatch.build(work / 'bin', reference=reference)
    recording = record.run(
        named,
        dispatcher,
        work=work / 'record',
        only=options.test or (),
        jobs=options.jobs,
    )
    options.out.mkdir(parents=True, exist_ok=True)
    _save({'pytest.txt': recording.tests.log}, options.out / _REPLAY)
    # replay every call on v1 under two hash seeds, or the calls of the ported
    # commands on v1 and on the engine, which also runs each on one thread
    explained = [test for test, route in routes.items() if route.cite in entries]
    if engines:
        sides = [reference, engines[0], replay.threaded(engines[0])]
        result = replay.run(
            recording,
            sides,
            commands=commands,
            routed=routes,
            explained=explained,
            second=True,
            jobs=options.jobs,
        )
    else:
        sides = selfcheck._seeded(reference)
        result = replay.run(
            recording,
            sides,
            routed=routes,
            explained=explained,
            jobs=options.jobs,
        )
    findings = list(result.findings)
    document = {
        **header,
        'runs': [item.name for item in sides],
        **result.report,
        'tests': recording.tests.report['tests'],
        'deselected': recording.tests.report['deselected'],
    }
    # make the format calls again, and run the controls on a whole recording
    if not engines:
        found, document['formats'] = replay.formats(
            recording,
            named,
            reference,
            work=work / 'formats',
            routed=routes,
        )
        findings.extend(found)
        lines = [json.dumps(entry) + '\n' for entry in recording.formats]
        _save({'formats.jsonl': ''.join(lines).encode('utf-8')}, options.out / _REPLAY)
        differing = document['formats']['differing']
        findings.extend(replay.unneeded(routes, result.report, differing))
        document['controls'] = {}
        for name in replay.CONTROLS if not options.test else ():
            found, document['controls'][name] = replay.control(
                recording,
                reference,
                name,
                jobs=options.jobs,
            )
            findings.extend(found)
    document['findings'] = [dataclasses.asdict(finding) for finding in findings]
    path = options.out / f'{_REPLAY}.json'
    path.write_text(json.dumps(document, indent=2) + '\n', encoding='utf-8')
    _save(result.streams, options.out / _REPLAY)
    # print how much was replayed, then what is not the expected result
    counts = result.report['counts']
    print(
        f'{_REPLAY}: {counts["calls"]} calls of {counts["tests"]} tests:'
        f' {counts["replayed"]} replayed, {counts["unformed"]} with no command'
        f' line, {counts["unfaithful"]} unfaithful; {counts["tests_routed"]}'
        f' tests routed'
    )
    for name, count in counts['commands'].items():
        print(f'{_REPLAY}: {name}: {count["replayed"]} of {count["calls"]} calls')
    if 'formats' in document:
        made = document['formats']
        print(f'{_REPLAY}: {made["calls"]} format calls, {made["distinct"]} distinct')
    for name, count in document.get('controls', {}).items():
        print(
            f'{_REPLAY}: control {name}: {count["reported"]} of'
            f' {count["doctored"]} doctored calls reported'
        )
    for finding in findings:
        line = _line(finding)
        print(f'  {line}')
    count = len(result.differences)
    s = 's' if count != 1 else ''
    print(f'{_REPLAY}: {count} difference{s}')
    for difference in result.differences:
        line = _line(difference)
        print(f'  {line}')
    for entry in result.excused:
        line = _line(entry)
        print(f'  registered: {line}')
    if findings or result.differences:
        return 1
    shutil.rmtree(work)
    return 0


def _sides(
    names: Sequence[str],
    declared: Mapping[str, Side],
    *,
    path: Optional[pathlib.Path],
) -> list[Side]:
    """Return the sides ``names``: declared in the sides file, or v1 sides.

    Args:
        names: The names a run gives its sides.
        declared: The sides the file at ``path`` declares, by name.
        path: The sides file, if the run names one.

    Raises:
        ValueError: If the file declares a side under the name of a v1 side
            of ``oracle.toml``, if no side has one of the names, or if a v1
            side is not what ``oracle.toml`` names.

    """
    named = oracle.load()
    taken = sorted(set(declared) & set(named.builds))
    if taken:
        raise ValueError(
            f'{path} declares {taken!r}, which {named.path.name} names as v1 sides.'
        )
    result = []
    for name in names:
        if name in declared:
            result.append(declared[name])
        elif name in named.builds:
            result.append(oracle.side(named, name))
        else:
            raise ValueError(f'No side is named {name!r}.')
    return result


def _matrix(
    corpus: Corpus,
    sides: Sequence[Side],
    *,
    work: pathlib.Path,
    options: argparse.Namespace,
) -> tuple[dict[str, Any], bool]:
    """Run the matrix on one corpus and save the streams that differ.

    The run passes with no difference, and with no excused command unless
    ``--registered`` lets the registry's differences pass.
    """
    plan = runner.prepare(corpus, work=work, seed=options.seed)
    result = runner.run(
        plan=plan,
        sides=sides,
        now=options.now,
        only=options.stage,
        keep=options.keep,
    )
    if not options.keep:
        runner.discard(plan)
    _save(result.streams, options.out / corpus.label)
    # print the differences and the searches that found nothing
    count = len(result.differences)
    s = 's' if count != 1 else ''
    print(f'{corpus.label}: {count} difference{s}')
    for difference in result.differences:
        line = _line(difference)
        print(f'  {line}')
    for excused in result.excused:
        line = _line(excused)
        print(f'  registered: {line}')
    for name in _no_hits(result.report):
        print(f'  found nothing: {name}')
    passed = (not result.differences) and (options.registered or not result.excused)
    return dict(result.report), passed


def _selfcheck(
    corpus: Corpus,
    base: Side,
    *,
    work: pathlib.Path,
    options: argparse.Namespace,
) -> tuple[dict[str, Any], bool]:
    """Run the self-check, or the registry check, on one corpus.

    The streams of a failed check are saved.
    """
    if options.command == 'registry':
        checks = selfcheck.registry(corpus, base, work=work, now=options.now)
    else:
        checks = selfcheck.run(
            corpus=corpus,
            base=base,
            work=work,
            now=options.now,
            seed=options.seed,
        )
    rows = []
    for check in checks:
        # print the verdict and what the check found
        verdict = 'passed' if check.passed else 'FAILED'
        print(f'{corpus.label}: {check.name}: {verdict}')
        for difference in check.found:
            line = _line(difference)
            print(f'  {line}')
        for excused in check.excused:
            line = _line(excused)
            print(f'  registered: {line}')
        for name in _no_hits(check.report):
            print(f'  found nothing: {name}')
        if not check.passed:
            _save(check.streams, options.out / corpus.label / check.name)
        # record the check in the result file
        row = {
            'name': check.name,
            'passed': check.passed,
            'expected': [dataclasses.asdict(item) for item in check.expected],
            'found': [dataclasses.asdict(item) for item in check.found],
            'registered': [dataclasses.asdict(item) for item in check.registered],
            'excused': [dataclasses.asdict(item) for item in check.excused],
            'run': check.report,
        }
        rows.append(row)
    document = {'corpus': corpus.label, 'checks': rows}
    return document, all(check.passed for check in checks)


def _save(streams: Mapping[str, bytes], folder: pathlib.Path) -> None:
    """Write each stream to its file below ``folder``."""
    for name, stream in streams.items():
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(stream)


def _line(difference: Union[Difference, Excused, replay.Finding]) -> str:
    """Return one difference, excused command or finding as a line of the summary."""
    fields = dataclasses.astuple(difference)
    return ': '.join(field for field in fields if field)


def _no_hits(report: Mapping[str, Any]) -> list[str]:
    """Return the cases built to find something that found nothing, by stage."""
    result = []
    for stage in report['stages']:
        label = stage['name']
        result.extend(f'{label}: {name}' for name in stage['no_hit'])
    return result


def _header(sides: Sequence[Side], *, now: str) -> dict[str, Any]:
    """Return what a result file records about the host and the sides."""
    # read the host's git under the caller's environment; a host with no
    # system config file records none
    git = snapshot._git(['--version'], check=False) or ''
    system = snapshot._git(['config', '--system', '--list'], check=False) or ''
    host = {
        'platform': platform.platform(),
        'python': sys.version,
        'sqlite': sqlite3.sqlite_version,
        'git': git,
        'git_system_config': system.splitlines(),
    }
    return {
        'host': host,
        'now': now,
        'sides': [_side_record(item, now=now) for item in sides],
    }


def _side_record(item: Side, *, now: str) -> dict[str, Any]:
    """Return one side's argv, variables, identity, version and description.

    Raises:
        ValueError: If the side's ``--version`` does not start, fails or
            prints nothing (such a side runs no wiki command, and two of
            them would fail every case alike), if the side does not prove
            it is the engine it names, or if its ``describe`` command does
            not start or fails.

    """
    described = None
    # run the side's --version and its describe command under a scratch home
    with tempfile.TemporaryDirectory() as tmp:
        home = pathlib.Path(tmp)
        env = side.environment(item, home, now=now)
        try:
            probe = side.run(item, ['--version'], cwd=home, env=env)
        except OSError as e:
            raise ValueError(f'Side {item.name!r} does not start: {e}') from e
        if item.describe:
            try:
                described = subprocess.run(
                    item.describe,
                    cwd=home,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                )
            except OSError as e:
                raise ValueError(
                    f'Side {item.name!r}: describe does not start: {e}'
                ) from e
    # refuse a side that runs no wiki command
    version = probe.stdout.decode('utf-8', 'replace').strip()
    if (probe.code != 0) or not version:
        error = probe.stderr.decode('utf-8', 'replace').strip()
        raise ValueError(
            f'Side {item.name!r} does not start: --version exited'
            f' {probe.code} and printed {version!r} ({error!r} on stderr).'
        )
    # refuse a side that is not the engine it names
    if not probe.identity:
        expected = dict(item.identity)
        raise ValueError(
            f'Side {item.name!r} is not the engine it names: --version left'
            f' no identity record of {expected!r}.'
        )
    # refuse a describe command that fails
    if (described is not None) and (described.returncode != 0):
        error = described.stderr.strip()
        raise ValueError(
            f'Side {item.name!r}: describe exited {described.returncode}'
            f' ({error!r} on stderr).'
        )
    return {
        'name': item.name,
        'argv': list(item.argv),
        'env': dict(item.env),
        'notice': item.notice,
        'identity': dict(item.identity),
        'version': version,
        'describe': described.stdout.strip() if described is not None else None,
    }


if __name__ == '__main__':
    sys.exit(main())
