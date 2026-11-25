"""Command line of the parity harness.

usage: python -m parity matrix --sides FILE --a NAME --b NAME --corpora FILE --work DIR --out DIR
       python -m parity selfcheck --sides FILE --side NAME --corpora FILE --work DIR --out DIR

``matrix`` compares two sides over the matrix; ``selfcheck`` runs one side
against itself, clean and doctored. Both exit 0 when every result is the
expected one, and 1 otherwise. A run that would compare nothing -- no
corpus, an unknown stage, a side that does not start -- exits 2 before it
snapshots anything.
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
from collections.abc import Mapping, Sequence
from typing import Any, Optional

from . import config, matrix, runner, selfcheck, side
from .runner import Corpus
from .side import Side

__all__ = ['main']

# the instant every side's clock is fixed at, unless the caller names one
_NOW = '2026-09-18T12:34:56Z'


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the harness and return its exit code."""
    parser = argparse.ArgumentParser(prog='python -m parity', description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('matrix', 'selfcheck'):
        command = commands.add_parser(name)
        command.add_argument('--sides', type=pathlib.Path, required=True)
        command.add_argument('--corpora', type=pathlib.Path, required=True)
        command.add_argument('--corpus', action='append')
        command.add_argument('--work', type=pathlib.Path, required=True)
        command.add_argument('--out', type=pathlib.Path, required=True)
        command.add_argument('--now', default=_NOW)
        command.add_argument('--seed', type=int, default=0)
        if name == 'matrix':
            command.add_argument('--a', required=True)
            command.add_argument('--b', required=True)
            command.add_argument('--stage', action='append', choices=matrix.STAGES)
            command.add_argument('--keep', action='store_true')
        else:
            command.add_argument('--side', required=True)
    options = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    sides = config.load_sides(options.sides)
    corpora = config.load_corpora(options.corpora)
    labels = options.corpus or list(corpora)
    unknown = [label for label in labels if label not in corpora]
    if unknown:
        parser.error(f'{options.corpora} declares no corpus named {unknown!r}')
    if not labels:
        parser.error(f'{options.corpora} declares no corpus')
    if options.command == 'matrix':
        chosen = [sides[options.a], sides[options.b]]
    else:
        chosen = [sides[options.side]]
    try:
        header = _header(chosen, now=options.now)
    except ValueError as e:
        parser.error(f'{e}')
    options.out.mkdir(parents=True, exist_ok=True)
    failed = False
    for label in labels:
        corpus = corpora[label]
        work = options.work / label
        if options.command == 'matrix':
            document, passed = _matrix(corpus, chosen, work=work, options=options)
        else:
            document, passed = _selfcheck(corpus, chosen[0], work=work, options=options)
        document = {**header, **document}
        text = json.dumps(document, indent=1)
        (options.out / f'{label}.json').write_text(text + '\n', encoding='utf-8')
        failed = failed or not passed
    if failed:
        return 1
    return 0


# ------ helper functions


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
        plan,
        sides,
        now=options.now,
        only=options.stage,
        keep=options.keep,
    )
    if not options.keep:
        runner.discard(plan)
    for name, stream in result.streams.items():
        target = options.out / corpus.label / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(stream)
    count = len(result.differences)
    print(f'{corpus.label}: {count} difference{"s" if count != 1 else ""}')
    for difference in result.differences:
        print(f'  {_line(difference)}')
    return dict(result.report), not result.differences


def _selfcheck(
    corpus: Corpus,
    base: Side,
    *,
    work: pathlib.Path,
    options: argparse.Namespace,
) -> tuple[dict[str, Any], bool]:
    """Run the self-check on one corpus."""
    checks = selfcheck.run(
        corpus,
        base,
        work=work,
        now=options.now,
        seed=options.seed,
    )
    rows = []
    for check in checks:
        verdict = 'passed' if check.passed else 'FAILED'
        print(f'{corpus.label}: {check.name}: {verdict}')
        for difference in check.found:
            print(f'  {_line(difference)}')
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


def _line(difference: runner.Difference) -> str:
    """Return one difference as a line of the printed summary."""
    fields = dataclasses.astuple(difference)
    return ': '.join(field for field in fields if field)


def _header(sides: Sequence[Side], *, now: str) -> dict[str, Any]:
    """Return what a result file records about the host and the sides."""
    git = subprocess.run(['git', '--version'], capture_output=True, text=True)
    system = subprocess.run(
        ['git', 'config', '--system', '--list'],
        capture_output=True,
        text=True,
    )
    host = {
        'platform': platform.platform(),
        'python': sys.version,
        'sqlite': sqlite3.sqlite_version,
        'git': git.stdout.strip(),
        'git_system_config': system.stdout.splitlines(),
    }
    return {
        'host': host,
        'now': now,
        'sides': [_side_record(item, now=now) for item in sides],
    }


def _side_record(item: Side, *, now: str) -> Mapping[str, Any]:
    """Return one side's argv, variables and the version it reports.

    Raises:
        ValueError: If the side's ``--version`` fails or prints nothing.
            Such a side runs no wiki command, and two of them would fail
            every case alike.

    """
    with tempfile.TemporaryDirectory() as home:
        env = side.environment(item, pathlib.Path(home), now=now)
        probe = side.run(item, ['--version'], cwd=pathlib.Path(home), env=env)
    version = probe.stdout.decode('utf-8', 'replace').strip()
    if (probe.code != 0) or not version:
        error = probe.stderr.decode('utf-8', 'replace').strip()
        raise ValueError(
            f'side {item.name!r} does not start: --version exited'
            f' {probe.code} and printed {version!r} ({error!r} on stderr)'
        )
    return {
        'name': item.name,
        'argv': list(item.argv),
        'env': dict(item.env),
        'notice': item.notice,
        'version': version,
    }


if __name__ == '__main__':
    sys.exit(main())
