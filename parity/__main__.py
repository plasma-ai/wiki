"""Command-line interface for ``parity``.

usage: python -m parity matrix --a NAME --b NAME [--registered] [--sides FILE] --corpora FILE --work DIR --out DIR
       python -m parity selfcheck --side NAME [--sides FILE] --corpora FILE --work DIR --out DIR
       python -m parity registry --side NAME [--sides FILE] --corpora FILE --work DIR --out DIR
       python -m parity suite [--engine NAME] [--sides FILE] --work DIR --out DIR
       python -m parity replay [--engine NAME] [--sides FILE] --work DIR --out DIR
       python -m parity fixtures [--engine NAME] [--sides FILE] [--every] --work DIR --out DIR
       python -m parity perturb corpus [--engine NAME] [--sides FILE] --corpora FILE [--seed N] --work DIR --out DIR
       python -m parity perturb loop [--engine NAME] [--sides FILE] [--corpora FILE] [--seed N | --fresh N] --work DIR --out DIR
       python -m parity interpreters --corpora FILE --work DIR --out DIR
       python -m parity oracle ensure [--side NAME]
       python -m parity oracle check [--skip NAME]
       python -m parity container [--commit COMMIT] [--out DIR] COMMAND [ARG ...]

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
calls of the ported commands on v1 and on that side. With no ``--engine``
and no ``--test`` it ends with an account of v1 itself: every test v1
collects has a home, and the kind list gives every notice and issue kind
one. It exits 0 when the sides differ in nothing, v1's command lines do
what the calls did in their tests, no call that is not routed for it runs
on input outside the supported set, and the account holds; it exits 1
otherwise. It exits 2 as ``suite`` does, and on a routing table or a kind
list that breaks its rules.

``fixtures`` writes the fixture families and runs the matrix, and each
family's own stages, on each. With no ``--engine`` both sides are v1, under
two hash seeds; with one it compares v1 with that side, and with that side
on one thread. A family the file system of the work folder cannot hold is
left out and named. It exits 0 when no family shows a difference, and 1
otherwise. It exits 2, before it writes a family, on an unknown side or
family, on a family named twice, on a tree a second engine refuses, on a
family the file system cannot hold when ``--family`` names it or ``--every``
is given, and on a work folder the harness refuses.

``perturb`` changes trees by a seed, the way hands, tools and merges change
a wiki, and compares the sides on what the changes leave. ``perturb corpus``
perturbs each corpus once, under a fresh seed unless ``--seed`` names one,
and runs the matrix and the perturbation's own stages on it. ``perturb loop``
perturbs small trees once for every seed and runs a short stage in place of
the matrix: the example wiki and the fixture families, or with ``--corpora``
the wiki of each corpus, copied into a repository of its own. Its seeds are
the committed list, the ones ``--seed`` names, or ``--fresh N`` new ones.
Both hold every perturbation to the kinds its operators name, and a run of
every tree holds the kind list to the kinds it shows. They exit 0 when no
tree shows a difference or a finding, and 1 otherwise. They exit 2, before a
tree is written, as ``fixtures`` does, on a seed list or a kind list that
breaks its rules, and on a kind list that disagrees with the operators. A
tree that ends in an error under a seed is printed and recorded, the others
run on, and the run then exits 2.

``interpreters`` compares the alt side, v1 under a second interpreter and
its own packages, with the oracle over the matrix on each corpus. Whatever
differs runs once more with the crossed side, the alt side's interpreter
under the oracle's packages, which says whether the interpreter, a package
or both cause it. It also asks both interpreters what they make of every
code point, and reads every name of each wiki for a code point they do not
agree on. It exits 0 when no corpus shows a difference, an excused command
or such a name, and 1 otherwise. It exits 2, before it snapshots anything,
as ``matrix`` does, and on an oracle file whose crossed side does not cross
the other two.

The registry lists what a second engine may do differently from v1, and two
builds of v1 have nothing registered between them. ``matrix`` therefore
counts a command the registry excuses as a result that is not the expected
one, unless ``--registered`` says side ``b`` is a second engine.

``oracle ensure`` builds the oracle checkout and the venvs of its sides, and
exits 2 when it refuses a checkout or a venv. ``oracle check`` holds
``oracle.toml`` level with v1 and exits 1 when a check fails.

``container`` runs a command in the Linux container: a clone of the
repository at ``HEAD``, or at the commit ``--commit`` names, on a file system
that keeps case and bytes. It exits with the exit code of the command, and
with 2 when Docker does not answer or the commit is none.

An error of the harness itself prints its traceback and exits 2 as well, so
exit 1 always means that a result is not the expected one.
"""

from __future__ import annotations

import argparse
import collections
import concurrent.futures
import dataclasses
import datetime as dt
import functools
import json
import logging
import os
import pathlib
import platform
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback
from collections.abc import Mapping, Sequence
from typing import Any, Optional, Union

from . import (
    config,
    container,
    dispatch,
    fixtures,
    homes,
    interpreters,
    kinds,
    oracle,
    perturb,
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

matrix, selfcheck, registry, suite, replay, fixtures, perturb and interpreters exit 0 when every
result is the expected one, and 1 otherwise. A run that would compare nothing exits 2 with a message,
before it snapshots anything or runs a test. oracle ensure exits 2 when it
refuses a checkout or a venv, and oracle check exits 1 when a check fails. An
error of the harness itself prints its traceback and exits 2.
"""
# the folder of the CLI suite below the work folder, and its result file
_SUITE = 'suite'
# the same for the replay
_REPLAY = 'replay'
# the same for the fixture families, whose result files carry their names
_FIXTURES = 'fixtures'
# the folders of the checkouts and of the runs, below the fixtures' folder
_TREES = 'trees'
_RUNS = 'runs'
# the same for the perturbation, and its two modes
_PERTURB = 'perturb'
_CORPUS = 'corpus'
_LOOP = 'loop'
# the seeds a fresh one is drawn from: every whole number of 32 bits
_SEEDS = 2**32
# the command that compares the alt side with the oracle
_INTERPRETERS = 'interpreters'
# the command that runs another in the Linux container
_CONTAINER = 'container'


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
    summary = 'Write the fixture families and run the matrix on each.'
    command = commands.add_parser(_FIXTURES, help=summary, description=summary)
    # engine name option
    engine_help = 'Name of the side compared against v1 (default: v1 against itself).'
    command.add_argument('--engine', help=engine_help)
    # sides file option
    sides_help = 'TOML file with one table per side, beside the v1 sides.'
    command.add_argument('--sides', type=pathlib.Path, help=sides_help)
    # family name option
    family_help = (
        'Name of a family to run (repeatable; default: every family the file'
        ' system of the work folder can hold).'
    )
    command.add_argument('--family', action='append', help=family_help)
    # every family option
    every_help = 'Refuse a work folder whose file system cannot hold every family.'
    command.add_argument('--every', action='store_true', help=every_help)
    # work folder option
    work_help = 'Folder the harness makes one new folder in.'
    command.add_argument('--work', type=pathlib.Path, required=True, help=work_help)
    # output folder option
    out_help = 'Folder for the result files and the saved streams.'
    command.add_argument('--out', type=pathlib.Path, required=True, help=out_help)
    # fixed instant option
    now_help = f'RFC 3339 instant every side runs at (default: {_NOW}).'
    command.add_argument('--now', type=_instant, default=_NOW, help=now_help)
    # sample seed option
    seed_help = 'Seed of the matrix sample (default: 0).'
    command.add_argument('--seed', type=int, default=0, help=seed_help)
    summary = 'Perturb trees by a seed and compare the sides on each.'
    command = commands.add_parser(_PERTURB, help=summary, description=summary)
    modes = command.add_subparsers(dest='mode', required=True)
    summaries = {
        _CORPUS: 'Perturb each corpus once and run the matrix on it.',
        _LOOP: 'Perturb small trees once for every seed and run a short stage on each.',
    }
    for name, summary in summaries.items():
        mode = modes.add_parser(name, help=summary, description=summary)
        # engine name option
        engine_help = (
            'Name of the side compared against v1 (default: v1 against itself).'
        )
        mode.add_argument('--engine', help=engine_help)
        # sides file option
        sides_help = 'TOML file with one table per side, beside the v1 sides.'
        mode.add_argument('--sides', type=pathlib.Path, help=sides_help)
        # corpora file option
        corpora_help = 'TOML file with one table per corpus.'
        if name == _LOOP:
            corpora_help += ' (default: the example wiki and the fixture families)'
        mode.add_argument(
            '--corpora',
            type=pathlib.Path,
            required=name == _CORPUS,
            help=corpora_help,
        )
        # corpus label option
        corpus_help = 'Label of a corpus to run on (repeatable; default: every corpus).'
        mode.add_argument('--corpus', action='append', help=corpus_help)
        # work folder option
        work_help = 'Folder the harness makes one new folder in.'
        mode.add_argument('--work', type=pathlib.Path, required=True, help=work_help)
        # output folder option
        out_help = 'Folder for the result files and the saved streams.'
        mode.add_argument('--out', type=pathlib.Path, required=True, help=out_help)
        # fixed instant option
        now_help = f'RFC 3339 instant every side runs at (default: {_NOW}).'
        mode.add_argument('--now', type=_instant, default=_NOW, help=now_help)
        if name == _CORPUS:
            # seed option
            seed_help = 'Seed of the perturbation (default: a fresh one).'
            mode.add_argument('--seed', type=_seed, help=seed_help)
        else:
            # tree name option
            family_help = (
                'Name of a tree to run on: hello or a fixture family (repeatable;'
                ' default: every tree the file system of the work folder can hold).'
            )
            mode.add_argument('--family', action='append', help=family_help)
            # every tree option
            every_help = (
                'Refuse a work folder whose file system cannot hold every tree.'
            )
            mode.add_argument('--every', action='store_true', help=every_help)
            # seed option
            seed_help = 'A seed to run (repeatable; default: the committed list).'
            mode.add_argument('--seed', type=_seed, action='append', help=seed_help)
            # fresh seeds option
            fresh_help = 'How many fresh seeds to run in place of the committed list.'
            mode.add_argument('--fresh', type=int, help=fresh_help)
            # jobs option
            jobs_help = 'How many trees are run at once (default: one per processor).'
            mode.add_argument('--jobs', type=int, help=jobs_help)
    summary = 'Compare the alt side with the oracle, and read the names.'
    command = commands.add_parser(_INTERPRETERS, help=summary, description=summary)
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
    # sample seed option
    seed_help = 'Seed of the matrix sample (default: 0).'
    command.add_argument('--seed', type=int, default=0, help=seed_help)
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
    summary = 'Run a command in the Linux container, in a clone of the repository.'
    command = commands.add_parser(_CONTAINER, help=summary, description=summary)
    # commit option
    commit_help = 'Commit the clone is checked out at (default: HEAD).'
    command.add_argument('--commit', help=commit_help)
    # output folder option
    out_help = 'Folder of the host that the container writes as /out.'
    command.add_argument('--out', type=pathlib.Path, help=out_help)
    # command argument
    argv_help = "The command and its arguments, run from the clone's root."
    command.add_argument('argv', nargs=argparse.REMAINDER, help=argv_help)
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


def _seed(value: str) -> int:
    """Return ``value``, a ``--seed`` argument, as a seed.

    Raises:
        argparse.ArgumentTypeError: If ``value`` is not a whole number
            from zero up.

    """
    if not (value.isascii() and value.isdigit()):
        raise argparse.ArgumentTypeError(
            f'A seed must be a whole number from zero up, got {value!r}.'
        )
    return int(value)


def _run(parser: argparse.ArgumentParser, options: argparse.Namespace) -> int:
    """Run the parsed command and return the exit code."""
    if options.command == 'oracle':
        return _oracle(parser, options)
    if options.command == _SUITE:
        return _suite(parser, options)
    if options.command == _REPLAY:
        return _replay(parser, options)
    if options.command == _FIXTURES:
        return _fixtures(parser, options)
    if options.command == _PERTURB:
        return _perturb(parser, options)
    if options.command == _INTERPRETERS:
        return _interpreters(parser, options)
    if options.command == _CONTAINER:
        return _container(parser, options)
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
    # read the registry, the routing table, the kind list, the ported commands
    # and the sides
    try:
        entries = registry.load()
        routes = routing.load(entries=entries)
        listed = kinds.load()
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
    explained = {
        test: route.cite for test, route in routes.items() if route.cite in entries
    }
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
        formatted = [test for entry in recording.formats for test in entry['tests']]
        findings.extend(replay.unneeded(routes, result.report, differing, formatted))
        document['controls'] = {}
        for name in replay.CONTROLS if not options.test else ():
            found, document['controls'][name] = replay.control(
                recording,
                reference,
                name,
                jobs=options.jobs,
            )
            findings.extend(found)
    # a whole recording holds the commands of the CLI tests' helper
    if not options.test:
        findings.extend(replay.unrecorded(recording))
    # a whole replay of v1 alone accounts for every test and every kind of v1
    if not (engines or options.test):
        collected = homes.collect(named, dispatcher, work=work / 'collect')
        found, document['homes'] = homes.check(
            collected,
            recording,
            result.report,
            document['formats'],
            routes,
            folder=named.folder,
            ported=commands,
        )
        findings.extend(found)
        found, document['kind_list'] = kinds.check(
            listed,
            recording,
            result.report,
            routes,
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
    left = result.report['unrecorded']
    if left:
        total = sum(left.values())
        print(f'{_REPLAY}: {total} commands of {len(left)} tests left no record')
    if 'formats' in document:
        made = document['formats']
        print(f'{_REPLAY}: {made["calls"]} format calls, {made["distinct"]} distinct')
    for name, count in document.get('controls', {}).items():
        print(
            f'{_REPLAY}: control {name}: {count["reported"]} of'
            f' {count["doctored"]} doctored calls reported'
        )
    count = len(result.report['outside'])
    print(f'{_REPLAY}: {count} calls ran on input outside the supported set')
    if 'homes' in document:
        held = document['homes']
        places = ', '.join(f'{count} {home}' for home, count in held['homes'].items())
        print(
            f'{_REPLAY}: {held["tests"]} tests of v1: {places};'
            f' {len(held["skipped"])} skipped here'
        )
        places = ', '.join(
            f'{count} to {place}' for place, count in held['routed'].items()
        )
        print(f'{_REPLAY}: routed: {places}')
        for family, homed in document['kind_list'].items():
            places = ', '.join(f'{count} {home}' for home, count in homed.items())
            label = family.removesuffix('s')
            print(f'{_REPLAY}: {sum(homed.values())} {label} kinds: {places}')
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


def _fixtures(parser: argparse.ArgumentParser, options: argparse.Namespace) -> int:
    """Write the fixture families, run each, and return the exit code."""
    # read the registry and the sides
    try:
        entries = registry.load()
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
        names.append(options.engine)
    try:
        chosen = _sides(names, declared, path=options.sides)
    except ValueError as e:
        parser.error(f'{e}')
    # refuse the work folder and the families, and probe every side, before
    # a family is written
    work = options.work.resolve() / _FIXTURES
    known = {family.name: family for family in fixtures.families()}
    named = options.family
    if options.every:
        if named is not None:
            parser.error('--every runs every family, and --family names some.')
        named = [name for name, family in known.items() if not family.entry]
    unknown = [name for name in named or () if name not in known]
    if unknown:
        parser.error(f'No fixture family is named {unknown!r}; they are {[*known]!r}.')
    # a tree a second engine refuses is no corpus: v1's own commands fail on
    # some, and the matrix holds a failure against the side
    refused = {name: known[name].entry for name in named or () if known[name].entry}
    if refused:
        parser.error(
            f'No run compares on a tree a second engine refuses, by registry'
            f' entry: {refused!r}.'
        )
    try:
        fixtures.validate(work)
        header = _header(chosen, now=options.now)
    except ValueError as e:
        parser.error(f'{e}')
    header['registry'] = {id: entry.status for id, entry in entries.items()}
    # write the families: v1 against itself under two hash seeds, or v1
    # against the engine, which also runs on one thread
    try:
        corpora = fixtures.generate(work / _TREES, names=named)
    except ValueError as e:
        parser.error(f'{e}')
    reference, *engines = chosen
    sides = selfcheck._seeded(reference)
    if engines:
        sides = [reference, engines[0], replay.threaded(engines[0])]
    written = [corpus.label for corpus in corpora]
    left = [
        family.name
        for family in fixtures.families()
        if (not family.entry) and (family.name not in written)
    ]
    if named is None:
        for name in left:
            print(f'{_FIXTURES}: left out, the file system cannot hold it: {name}')
        header['left_out'] = left
    # run each family and write its result file
    options.out.mkdir(parents=True, exist_ok=True)
    failed = False
    for corpus in corpora:
        folder = work / _RUNS / corpus.label
        plan = runner.prepare(corpus, work=folder, seed=options.seed)
        result = runner.run(plan, sides, now=options.now)
        runner.discard(plan)
        document = {**header, **result.report}
        text = json.dumps(document, indent=2)
        (options.out / f'{corpus.label}.json').write_text(text + '\n', encoding='utf-8')
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
        # two builds of v1 have nothing registered between them
        if result.differences or (result.excused and not engines):
            failed = True
    if failed:
        return 1
    shutil.rmtree(work)
    return 0


def _perturb(parser: argparse.ArgumentParser, options: argparse.Namespace) -> int:
    """Perturb trees by a seed, run each, and return the exit code."""
    loop = options.mode == _LOOP
    # read the registry, the kind list, the seeds, the sides and the corpora
    try:
        entries = registry.load()
        listed = kinds.load()
        perturb.validate(listed)
        seeds = _perturb_seeds(options)
        declared = {}
        if options.sides is not None:
            declared = config.load_sides(options.sides)
        corpora = {}
        if options.corpora is not None:
            corpora = config.load_corpora(options.corpora)
    except ValueError as e:
        parser.error(f'{e}')
    # resolve the corpora to run on
    labels = options.corpus or list(corpora)
    if options.corpora is None:
        if options.corpus:
            parser.error('--corpus names a corpus, and no --corpora file declares one.')
    else:
        if loop and (options.family or options.every):
            parser.error('--corpora names the trees of the loop, and so does --family.')
        unknown = [label for label in labels if label not in corpora]
        if unknown:
            parser.error(f'{options.corpora} declares no corpus named {unknown!r}.')
        twice = sorted({label for label in labels if labels.count(label) > 1})
        if twice:
            parser.error(f'--corpus names a corpus twice: {twice!r}.')
        if not labels:
            parser.error(f'{options.corpora} declares no corpus.')
    if loop and options.every and (options.family is not None):
        parser.error('--every runs every tree, and --family names some.')
    if loop and (options.jobs is not None) and (options.jobs < 1):
        parser.error(f'--jobs must be at least 1, got {options.jobs}.')
    # resolve the sides: v1, and the engine if the run names one
    names = [suite.REFERENCE]
    if options.engine is not None:
        if options.engine == suite.REFERENCE:
            parser.error(f'--engine names {options.engine!r}, the side v1 runs as.')
        names.append(options.engine)
    try:
        chosen = _sides(names, declared, path=options.sides)
    except ValueError as e:
        parser.error(f'{e}')
    # refuse the work folder and the corpora, and probe every side, before a
    # tree is written
    work = options.work.resolve() / _PERTURB
    try:
        fixtures.validate(work)
        for label in labels:
            if loop:
                perturb.carvable(corpora[label])
            else:
                runner.validate(corpora[label], work=work / _RUNS / label)
        header = _header(chosen, now=options.now)
    except ValueError as e:
        parser.error(f'{e}')
    header['registry'] = {id: entry.status for id, entry in entries.items()}
    # write the trees of a loop: the small ones of the harness, or the wiki of
    # each corpus in a repository of its own
    trees = [corpora[label] for label in labels]
    if loop and (options.corpora is not None):
        trees = [perturb.carve(tree, work / _TREES) for tree in trees]
    elif loop:
        try:
            trees, left = perturb.trees(
                work / _TREES,
                names=options.family,
                every=options.every,
            )
        except ValueError as e:
            parser.error(f'{e}')
        for name in left:
            print(f'{_PERTURB}: left out, the file system cannot hold it: {name}')
        header['left_out'] = left
    # v1 against itself under two hash seeds, or v1 against the engine, which
    # also runs on one thread
    reference, *engines = chosen
    sides = selfcheck._seeded(reference)
    if engines:
        sides = [reference, engines[0], replay.threaded(engines[0])]
    # a fresh seed is known nowhere else until its tree has run
    fresh = options.seed is None
    if loop:
        fresh = options.fresh is not None
    if fresh:
        drawn = ' '.join(f'{seed}' for seed in seeds)
        print(f'{_PERTURB}: fresh seeds: {drawn}', flush=True)
    # a loop prints one line for each tree and seed, and none for each stage
    if loop:
        logging.getLogger('parity').setLevel(logging.WARNING)
    # run each tree under each seed: a corpus alone, the trees of a loop
    # several at once
    pairs = [(tree, seed) for tree in trees for seed in seeds]
    jobs = 1
    if loop:
        jobs = options.jobs or os.cpu_count() or 1
    trial = functools.partial(
        perturb.run,
        sides=sides,
        work=work / _RUNS,
        now=options.now,
        loop=loop,
        second=bool(engines),
    )

    def attempt(tree: Corpus, seed: int) -> Union[perturb.Trial, Exception]:
        """Run one tree under one seed, and return the trial or the error that ended it."""
        # NOTE: an error must not take the results of the other trials with
        #   it: a loop runs for hours, and one copy that fails would end it
        try:
            return trial(tree, seed)
        except Exception as e:
            traceback.print_exc()
            return e

    options.out.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    rows: dict[str, list[dict[str, Any]]] = {tree.label: [] for tree in trees}
    shown = []
    failed = 0
    errors = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        tried = pool.map(attempt, *zip(*pairs))
        for (tree, seed), done in zip(pairs, tried):
            label = tree.label
            if isinstance(done, Exception):
                error = f'{type(done).__name__}: {done}'
                print(f'{label} seed {seed}: error: {error}')
                rows[label].append({'seed': seed, 'error': error})
                errors += 1
            else:
                rows[label].append(_tried(done, header, out=options.out, loop=loop))
                shown.extend(done.shown.values())
                failed += done.failed
            # a tree of a loop has one result file, with a row for each seed
            # that has ended
            if loop:
                document = {**header, 'corpus': label, 'seeds': rows[label]}
                text = json.dumps(document, indent=2)
                path = options.out / f'{label}.json'
                path.write_text(text + '\n', encoding='utf-8')
    # a run of every tree holds the kind list to the kinds the trees show
    findings = []
    if not (options.corpus or (loop and options.family) or errors):
        findings = perturb.unshown(listed, shown)
    for finding in findings:
        line = _line(finding)
        print(f'  {line}')
    # write the result file of the run, and print how much ran
    seconds = round(time.monotonic() - started, 1)
    document = {
        **header,
        'mode': options.mode,
        'seeds': seeds,
        'trees': [tree.label for tree in trees],
        'failed': failed,
        'errors': errors,
        'findings': [dataclasses.asdict(finding) for finding in findings],
        'seconds': seconds,
    }
    path = options.out / f'{_PERTURB}.json'
    path.write_text(json.dumps(document, indent=2) + '\n', encoding='utf-8')
    s = 's' if len(seeds) != 1 else ''
    on = 'trees' if len(trees) != 1 else 'tree'
    ended = f', {errors} ended in an error' if errors else ''
    print(
        f'{_PERTURB}: {len(seeds)} seed{s} on {len(trees)} {on}: {failed} of'
        f' {len(pairs)} failed{ended}, {seconds:.0f} s'
    )
    if errors:
        return 2
    if failed or findings:
        return 1
    shutil.rmtree(work)
    return 0


def _tried(
    done: perturb.Trial,
    header: Mapping[str, Any],
    *,
    out: pathlib.Path,
    loop: bool,
) -> dict[str, Any]:
    """Print what one tree showed under one seed, and write its result file.

    A corpus has one result file, ``<out>/<label>.json``. In a loop only a
    tree that failed under a seed gets one, ``<out>/<label>/<seed>.json``,
    and every other is the row this returns. The streams of a case that
    differs are saved beside the result file. A search by the
    perturbation's tag that found nothing is a finding; any other search
    that found nothing is printed and fails nothing.
    """
    result = done.result
    row = {
        'seed': done.seed,
        'differences': len(result.differences),
        'findings': len(done.findings),
        'excused': len(result.excused),
        'seconds': done.seconds,
    }
    target = out / f'{done.label}.json'
    streams = out / done.label
    if loop:
        target = out / done.label / f'{done.seed}.json'
        streams = out / done.label / f'{done.seed}'
    if done.failed or not loop:
        document = {
            **header,
            **result.report,
            'perturbation': dataclasses.asdict(done.perturbation),
            'kinds': done.shown,
            'findings': [dataclasses.asdict(finding) for finding in done.findings],
            'seconds': done.seconds,
        }
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(document, indent=2) + '\n', encoding='utf-8')
        _save(result.streams, streams)
    # print the differences, the findings and the searches that found nothing
    count = len(result.differences)
    s = 's' if count != 1 else ''
    print(f'{done.label} seed {done.seed}: {count} difference{s}')
    for difference in result.differences:
        line = _line(difference)
        print(f'  {line}')
    for excused in result.excused:
        line = _line(excused)
        print(f'  registered: {line}')
    for finding in done.findings:
        line = _line(finding)
        print(f'  {line}')
    for name in _no_hits(result.report):
        print(f'  found nothing: {name}')
    return row


def _perturb_seeds(options: argparse.Namespace) -> list[int]:
    """Return the seeds of a perturbation run.

    A corpus takes the seed the run names, or a fresh one. A loop takes the
    seeds the run names, as many fresh ones as it asks for, or the committed
    list.

    Raises:
        ValueError: If a loop names seeds and asks for fresh ones, if it
            asks for fewer than one, or if the committed list breaks a
            rule of :func:`parity.perturb.load_seeds`.

    """
    fresh = random.SystemRandom()
    if options.mode == _CORPUS:
        if options.seed is not None:
            return [options.seed]
        return [fresh.randrange(_SEEDS)]
    if options.fresh is not None:
        if options.seed:
            raise ValueError('--fresh draws the seeds, and --seed names some.')
        if options.fresh < 1:
            raise ValueError(f'--fresh must be at least 1, got {options.fresh}.')
        return [fresh.randrange(_SEEDS) for _ in range(options.fresh)]
    if options.seed:
        return list(dict.fromkeys(options.seed))
    return list(perturb.load_seeds())


def _interpreters(parser: argparse.ArgumentParser, options: argparse.Namespace) -> int:
    """Compare the alt side with the oracle on each corpus, and return the exit code."""
    # read the registry, the oracle file and the corpora
    try:
        entries = registry.load()
        interpreters.validate(oracle.load())
        corpora = config.load_corpora(options.corpora)
    except ValueError as e:
        parser.error(f'{e}')
    # resolve the corpora to run on
    labels = options.corpus or list(corpora)
    unknown = [label for label in labels if label not in corpora]
    if unknown:
        parser.error(f'{options.corpora} declares no corpus named {unknown!r}.')
    twice = sorted({label for label in labels if labels.count(label) > 1})
    if twice:
        parser.error(f'--corpus names a corpus twice: {twice!r}.')
    if not labels:
        parser.error(f'{options.corpora} declares no corpus.')
    # resolve the three v1 sides
    names = [interpreters.REFERENCE, interpreters.COMPARED, interpreters.CROSSED]
    try:
        chosen = _sides(names, {}, path=None)
    except ValueError as e:
        parser.error(f'{e}')
    # refuse every corpus the harness cannot snapshot, and probe every side,
    # before the first one runs
    try:
        for label in labels:
            runner.validate(corpora[label], work=options.work / label)
        header = _header(chosen, now=options.now)
    except ValueError as e:
        parser.error(f'{e}')
    header['registry'] = {id: entry.status for id, entry in entries.items()}
    # ask the two interpreters what they make of every code point
    reference, compared, _ = chosen
    changed = interpreters.changed(reference, compared)
    properties = collections.Counter(
        name for differing in changed.values() for name in differing
    )
    header['points'] = {'changed': len(changed), 'properties': dict(properties)}
    count = len(changed)
    s = 's' if count != 1 else ''
    print(f'{_INTERPRETERS}: the sides differ on {count} code point{s}', flush=True)
    # run each corpus and write its result file
    options.out.mkdir(parents=True, exist_ok=True)
    failed = False
    for label in labels:
        trial = interpreters.run(
            corpora[label],
            sides=chosen,
            changed=changed,
            work=options.work / label,
            now=options.now,
            seed=options.seed,
        )
        document = {**header, **interpreters.report(trial, changed)}
        text = json.dumps(document, indent=2)
        (options.out / f'{label}.json').write_text(text + '\n', encoding='utf-8')
        _save(trial.result.streams, options.out / label)
        if trial.again is not None:
            _save(trial.again.streams, options.out / label / interpreters.CROSSED)
        # print the differences with their causes, and the searches that
        # found nothing
        causes = {cause.item: cause.cause for cause in trial.causes}
        count = len(trial.result.differences)
        s = 's' if count != 1 else ''
        print(f'{label}: {count} difference{s}')
        for difference in trial.result.differences:
            line = _line(difference)
            if difference in causes:
                line = f'{line} ({causes[difference]})'
            print(f'  {line}')
        for excused in trial.result.excused:
            line = _line(excused)
            print(f'  registered: {line} ({causes[excused]})')
        for name in _no_hits(trial.result.report):
            print(f'  found nothing: {name}')
        # print what the second run shows of the crossed side alone
        if trial.again is not None:
            found = [item for item in trial.alone if isinstance(item, Difference)]
            count = len(found)
            s = 's' if count != 1 else ''
            print(f'{label}: {count} difference{s} of the crossed side alone')
            for item in trial.alone:
                line = _line(item)
                if isinstance(item, Excused):
                    line = f'registered: {line}'
                print(f'  {line}')
        # print the names that hold a changed code point
        count = len(trial.hits)
        s = 's' if count != 1 else ''
        print(f'{label}: {count} name{s} of {trial.names} with a changed code point')
        for hit in trial.hits:
            path = hit.path.encode('utf-8', 'backslashreplace').decode('utf-8')
            spelled = ' '.join(interpreters.spell(point) for point in hit.points)
            print(f'  {path}: {spelled}')
        failed = failed or trial.failed
    if failed:
        return 1
    return 0


def _container(parser: argparse.ArgumentParser, options: argparse.Namespace) -> int:
    """Run a command in the Linux container and return its exit code."""
    # a `--` that parts the command from the options is not part of it
    command = options.argv
    if command[:1] == ['--']:
        command = command[1:]
    try:
        return container.run(command, commit=options.commit, out=options.out)
    except ValueError as e:
        parser.error(f'{e}')


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
    """Return one difference, excused command or finding as a line of the summary.

    A path may hold bytes that are no valid UTF-8, which a string carries as
    lone surrogates and no stream prints: the line spells each as an escape.
    """
    fields = dataclasses.astuple(difference)
    line = ': '.join(field for field in fields if field)
    return line.encode('utf-8', 'backslashreplace').decode('utf-8')


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
