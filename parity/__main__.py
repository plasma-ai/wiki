"""Command-line interface for ``parity``.

usage: python -m parity matrix --sides FILE --a NAME --b NAME --corpora FILE --work DIR --out DIR
       python -m parity selfcheck --sides FILE --side NAME --corpora FILE --work DIR --out DIR

``matrix`` compares two sides over the matrix; ``selfcheck`` runs one side
against itself, clean and doctored. Both exit 0 when every result is the
expected one, and 1 otherwise. A run that would compare nothing -- no
corpus, an unknown corpus, stage or side, one side named twice, a sides or
corpora file of the wrong shape, a side or a ``describe`` command that does
not start -- exits 2 before it snapshots anything. An error of the harness
itself prints its traceback and exits 2 as well, so exit 1 always means
that a result is not the expected one.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import pathlib
import platform
import sqlite3
import subprocess
import sys
import tempfile
import traceback
from collections.abc import Mapping, Sequence
from typing import Any, Optional

from . import config, runner, selfcheck, side, snapshot
from .compare import Difference
from .matrix import STAGES
from .runner import Corpus
from .side import Side

__all__ = []

# the instant every side's clock is fixed at, unless the caller names one
_NOW = '2026-09-18T12:34:56Z'
# what --help prints above the commands, with the exit codes a gate reads
_DESCRIPTION = """\
Run two wiki engines on the same trees and report what they do differently.

Both commands exit 0 when every result is the expected one, and 1 otherwise.
A run that would compare nothing exits 2 with a message, before it snapshots
anything. An error of the harness itself prints its traceback and exits 2.
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
        sides_help = 'TOML file with one table per side.'
        command.add_argument(
            '--sides',
            type=pathlib.Path,
            required=True,
            help=sides_help,
        )
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
        command.add_argument('--now', default=_NOW, help=now_help)
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


def _run(parser: argparse.ArgumentParser, options: argparse.Namespace) -> int:
    """Run the parsed command on each corpus and return the exit code."""
    # read the sides and the corpora
    try:
        sides = config.load_sides(options.sides)
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
    unknown = [name for name in names if name not in sides]
    if unknown:
        parser.error(f'{options.sides} declares no side named {unknown!r}.')
    chosen = [sides[name] for name in names]
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
    """Return one side's argv, variables, reported version and description.

    Raises:
        ValueError: If the side's ``--version`` does not start, fails or
            prints nothing (such a side runs no wiki command, and two of
            them would fail every case alike), or if its ``describe``
            command does not start or fails.

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
        'version': version,
        'describe': described.stdout.strip() if described is not None else None,
    }


if __name__ == '__main__':
    sys.exit(main())
