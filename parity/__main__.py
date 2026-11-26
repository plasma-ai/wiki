"""Command-line interface for ``parity``.

usage: python -m parity matrix --a NAME --b NAME [--sides FILE] --corpora FILE --work DIR --out DIR
       python -m parity selfcheck --side NAME [--sides FILE] --corpora FILE --work DIR --out DIR
       python -m parity oracle ensure [--side NAME]
       python -m parity oracle check [--skip NAME]

``matrix`` compares two sides over the matrix; ``selfcheck`` runs one side
against itself, clean and doctored. Both exit 0 when every result is the
expected one, and 1 otherwise. A run that would compare nothing -- no
corpus, an unknown corpus, stage or side, one side named twice, a sides or
corpora file of the wrong shape, a ``--now`` no engine takes, a checkout
or a work folder the harness refuses, a side or a ``describe`` command
that does not start, a side that does not prove its identity, a v1 side
the oracle rules refuse -- exits 2 before it snapshots anything. A side is
one the sides file declares, or a v1 side of ``oracle.toml``.

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
import sqlite3
import subprocess
import sys
import tempfile
import traceback
from collections.abc import Mapping, Sequence
from typing import Any, Optional

from . import config, oracle, runner, selfcheck, side, snapshot
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

matrix and selfcheck exit 0 when every result is the expected one, and 1
otherwise. A run that would compare nothing exits 2 with a message, before it
snapshots anything. oracle ensure exits 2 when it refuses a checkout or a
venv, and oracle check exits 1 when a check fails. An error of the harness
itself prints its traceback and exits 2.
"""


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
            side_help = 'Name of the side to run against itself.'
            command.add_argument('--side', required=True, help=side_help)
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
    # run each corpus and write its result file
    options.out.mkdir(parents=True, exist_ok=True)
    failed = False
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
    """Run the matrix on one corpus and save the streams that differ."""
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
    for name in _no_hits(result.report):
        print(f'  found nothing: {name}')
    return dict(result.report), not result.differences


def _selfcheck(
    corpus: Corpus,
    base: Side,
    *,
    work: pathlib.Path,
    options: argparse.Namespace,
) -> tuple[dict[str, Any], bool]:
    """Run the self-check on one corpus and save the streams of a failed check."""
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


def _line(difference: Difference) -> str:
    """Return one difference as a line of the printed summary."""
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
    # read the host's git under the caller's environment; a host with no git
    # records an empty version and no system config
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
