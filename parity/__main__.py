"""Command line of the parity harness.

usage: python -m parity matrix --a NAME --b NAME [--sides FILE] --corpora FILE --work DIR --out DIR
       python -m parity selfcheck --side NAME [--sides FILE] --corpora FILE --work DIR --out DIR
       python -m parity registry --side NAME [--sides FILE] --corpora FILE --work DIR --out DIR
       python -m parity oracle ensure [--side NAME]
       python -m parity oracle check [--skip NAME]

``matrix`` compares two sides over the matrix; ``selfcheck`` runs one side
against itself, clean and doctored; ``registry`` runs one side against a
stand-in on the fixtures of the registry's classifiers. Each exits 0 when
every result is the expected one, and 1 otherwise. A run that would compare
nothing -- no corpus, an unknown stage, a side that does not start, a
registry file the harness cannot hold to its classifiers -- exits 2 before
it snapshots anything. A side is one the sides file declares, or a v1 side
of ``oracle.toml``. ``registry --release`` also exits 1 while an entry of
the registry is not ready for a release.

``oracle ensure`` builds the oracle checkout and the venvs of its sides, and
exits 2 when it refuses a checkout or a venv. ``oracle check`` holds
``oracle.toml`` level with v1 and exits 1 when a check fails.
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
from typing import Any, Optional, Union

from . import config, matrix, oracle, registry, runner, selfcheck, side
from .runner import Corpus
from .side import Side

__all__ = ['main']

# the instant every side's clock is fixed at, unless the caller names one
_NOW = '2026-09-18T12:34:56Z'


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the harness and return its exit code."""
    parser = argparse.ArgumentParser(prog='python -m parity', description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('matrix', 'selfcheck', 'registry'):
        command = commands.add_parser(name)
        command.add_argument('--sides', type=pathlib.Path)
        command.add_argument('--corpora', type=pathlib.Path, required=True)
        command.add_argument('--corpus', action='append')
        command.add_argument('--work', type=pathlib.Path, required=True)
        command.add_argument('--out', type=pathlib.Path, required=True)
        command.add_argument('--now', default=_NOW)
        if name == 'registry':
            command.add_argument('--release', action='store_true')
        else:
            command.add_argument('--seed', type=int, default=0)
        if name == 'matrix':
            command.add_argument('--a', required=True)
            command.add_argument('--b', required=True)
            command.add_argument('--stage', action='append', choices=matrix.STAGES)
            command.add_argument('--keep', action='store_true')
        else:
            command.add_argument('--side', required=True)
    command = commands.add_parser('oracle')
    actions = command.add_subparsers(dest='action', required=True)
    action = actions.add_parser('ensure')
    action.add_argument('--side', action='append')
    action = actions.add_parser('check')
    action.add_argument('--skip', action='append', choices=oracle.CHECKS, default=[])
    options = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    if options.command == 'oracle':
        return _oracle(options, parser)
    corpora = config.load_corpora(options.corpora)
    labels = options.corpus or list(corpora)
    unknown = [label for label in labels if label not in corpora]
    if unknown:
        parser.error(f'{options.corpora} declares no corpus named {unknown!r}')
    if not labels:
        parser.error(f'{options.corpora} declares no corpus')
    if options.command == 'matrix':
        names = [options.a, options.b]
    else:
        names = [options.side]
    try:
        entries = registry.load()
        chosen = _sides(names, options.sides)
        header = _header(chosen, now=options.now)
    except ValueError as e:
        parser.error(f'{e}')
    header['registry'] = {id: entry.status for id, entry in entries.items()}
    options.out.mkdir(parents=True, exist_ok=True)
    failed = False
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
            document, passed = _selfcheck(corpus, chosen[0], work=work, options=options)
        document = {**header, **document}
        text = json.dumps(document, indent=1)
        (options.out / f'{label}.json').write_text(text + '\n', encoding='utf-8')
        failed = failed or not passed
    if failed:
        return 1
    return 0


# ------ helper functions


def _oracle(options: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
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
        parser.error(f'{named.path.name} names no side {unknown!r}')
    try:
        oracle.ensure(named, names=options.side)
    except ValueError as e:
        parser.error(f'{e}')
    return 0


def _sides(names: Sequence[str], path: Optional[pathlib.Path]) -> list[Side]:
    """Return the sides ``names``: declared in the file at ``path``, or v1 sides.

    Raises:
        ValueError: If the file declares a side under the name of a v1 side
            of ``oracle.toml``, if no side has one of the names, or if a v1
            side is not what ``oracle.toml`` names.

    """
    declared = config.load_sides(path) if path is not None else {}
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
        plan,
        sides,
        now=options.now,
        only=options.stage,
        keep=options.keep,
    )
    if not options.keep:
        runner.discard(plan)
    _save(result.streams, options.out / corpus.label)
    count = len(result.differences)
    print(f'{corpus.label}: {count} difference{"s" if count != 1 else ""}')
    for difference in result.differences:
        print(f'  {_line(difference)}')
    for excused in result.excused:
        print(f'  registered: {_line(excused)}')
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
    """Run the self-check, or the registry check, on one corpus.

    The streams of a failed check are saved.
    """
    if options.command == 'registry':
        checks = selfcheck.registry(corpus, base, work=work, now=options.now)
    else:
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
        for excused in check.excused:
            print(f'  registered: {_line(excused)}')
        for name in _no_hits(check.report):
            print(f'  found nothing: {name}')
        if not check.passed:
            _save(check.streams, options.out / corpus.label / check.name)
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


def _line(difference: Union[runner.Difference, runner.Excused]) -> str:
    """Return one difference, or one excused command, as a line of the summary."""
    fields = dataclasses.astuple(difference)
    return ': '.join(field for field in fields if field)


def _no_hits(report: Mapping[str, Any]) -> list[str]:
    """Return the cases built to find something that found nothing, by stage."""
    return [
        f'{stage["name"]}: {name}'
        for stage in report['stages']
        for name in stage['no_hit']
    ]


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
    """Return one side's argv, variables, identity, version and description.

    Raises:
        ValueError: If the side's ``--version`` fails or prints nothing
            (such a side runs no wiki command, and two of them would fail
            every case alike), if the side does not prove it is the engine
            it names, or if its ``describe`` command fails.

    """
    described = None
    with tempfile.TemporaryDirectory() as home:
        env = side.environment(item, pathlib.Path(home), now=now)
        probe = side.run(item, ['--version'], cwd=pathlib.Path(home), env=env)
        if item.describe:
            described = subprocess.run(
                item.describe,
                cwd=home,
                env=env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
            )
    version = probe.stdout.decode('utf-8', 'replace').strip()
    if (probe.code != 0) or not version:
        error = probe.stderr.decode('utf-8', 'replace').strip()
        raise ValueError(
            f'side {item.name!r} does not start: --version exited'
            f' {probe.code} and printed {version!r} ({error!r} on stderr)'
        )
    if not probe.identity:
        raise ValueError(
            f'side {item.name!r} is not the engine it names: --version left'
            f' no identity record of {dict(item.identity)!r}'
        )
    if (described is not None) and (described.returncode != 0):
        raise ValueError(
            f'side {item.name!r}: describe exited {described.returncode}'
            f' ({described.stderr.strip()!r} on stderr)'
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
