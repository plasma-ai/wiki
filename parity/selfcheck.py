"""The harness self-check: one side against itself, clean and doctored."""

from __future__ import annotations

import dataclasses
import json
import pathlib
import shutil
import sys
from collections.abc import Mapping
from typing import Any

from . import cache, doctor, runner
from .compare import Difference
from .matrix import Case
from .runner import Corpus, Plan
from .side import Side

__all__ = [
    'CHECKS',
    'Check',
    'run',
    'clean',
    'doctored',
]

#: the doctored checks: the name of each, what its doctored side changes
#: (a kind of :data:`doctor.KINDS`), and the stage whose last command it
#: changes it in -- nothing runs after that command, so the one change
#: cannot spread into another command's output
CHECKS = (
    ('stdout', 'stdout', 'full'),
    ('stderr', 'stderr', 'full'),
    ('exit', 'exit', 'full'),
    ('file', 'file', 'full'),
    ('mode', 'mode', 'full'),
    ('extra', 'extra', 'full'),
    ('rewrite', 'rewrite', 'full'),
    ('stray', 'extra', 'write'),
    ('counts', 'counts', 'write'),
    ('index', 'index', 'write'),
    ('cache-mode', 'mode', 'write'),
    ('schema', 'schema', 'write'),
    ('notice', 'notice', 'full'),
    ('identity', 'identity', 'full'),
)

# the two sides of one engine differ only in their hash seed
_SEEDS = ('1', '2')
# the paths a doctored side acts on, relative to the wiki root
_EXTRA = 'parity_doctored.md'
_COUNTS = f'{cache.CACHE}/word_counts.json'
_SEARCH = f'{cache.CACHE}/search.db'
# where the trees of a failed check are filed, below the work folder
_FAILED = 'failed'


@dataclasses.dataclass(frozen=True)
class Check:
    """One part of the self-check.

    Attributes:
        name: ``clean``, or ``doctored`` and the name of the check.
        expected: The differences the harness must report, and no other.
        found: The differences it reported.
        report: The matrix run as JSON data.
        streams: The streams of each case whose streams differ, as a
            matrix run returns them.

    """

    name: str
    expected: tuple[Difference, ...]
    found: tuple[Difference, ...]
    report: Mapping[str, Any]
    streams: Mapping[str, bytes]

    @property
    def passed(self: Check) -> bool:
        """Return whether the harness reported exactly what it must."""
        return sorted(self.expected) == sorted(self.found)


def run(
    corpus: Corpus,
    base: Side,
    *,
    work: pathlib.Path,
    now: str,
    seed: int = 0,
) -> list[Check]:
    """Check the harness on ``corpus`` with one engine on both sides.

    Args:
        corpus: The wiki to run on.
        base: The engine both sides run.
        work: New folder for the snapshot and the clones. It is removed at
            the end when every check passed. Otherwise it keeps the
            snapshot and, under ``failed/<check>``, the trees of every run
            of each failed check.
        now: RFC 3339 instant for ``WIKI_NOW``.
        seed: Seed of the matrix sample.

    Returns:
        The clean check, then one check per row of :data:`CHECKS`. The
        ``notice`` row is left out for an engine that prints no notice, and
        the ``identity`` row for one that writes no identity record.

    """
    plan = runner.prepare(corpus, work=work, seed=seed)
    result = [clean(plan, base, now=now, keep=True)]
    _settle(plan, result[-1])
    for name, kind, _ in CHECKS:
        if (kind == 'notice') and not base.notice:
            continue
        if (kind == 'identity') and not base.identity:
            continue
        result.append(doctored(plan, base, name, now=now, keep=True))
        _settle(plan, result[-1])
    if all(check.passed for check in result):
        runner.discard(plan)
    return result


def clean(plan: Plan, base: Side, *, now: str, keep: bool = False) -> Check:
    """Run the whole matrix with ``base`` on both sides, under two hash seeds.

    Args:
        plan: The snapshot to run on.
        base: The engine both sides run.
        now: RFC 3339 instant for ``WIKI_NOW``.
        keep: Whether to leave the tree of every run in the plan's
            :attr:`~parity.runner.Plan.kept` folder.

    Returns:
        The check, which expects no difference.

    """
    found = runner.run(plan, _seeded(base), now=now, keep=keep)
    return Check('clean', (), found.differences, found.report, found.streams)


def doctored(
    plan: Plan,
    base: Side,
    name: str,
    *,
    now: str,
    keep: bool = False,
) -> Check:
    """Run one stage with ``base`` against a doctored copy of itself.

    The doctored side changes one thing in the stage's last command.

    Args:
        plan: The snapshot to run on.
        base: The engine both sides run.
        name: Name of a row of :data:`CHECKS`.
        now: RFC 3339 instant for ``WIKI_NOW``.
        keep: Whether to leave the tree of every run in the plan's
            :attr:`~parity.runner.Plan.kept` folder.

    Returns:
        The check, which expects exactly the differences the one change
        must show as.

    Raises:
        ValueError: If the stage runs its last command twice: the doctored
            side tells its target by the command's arguments.

    """
    (kind, stage) = next(row[1:] for row in CHECKS if row[0] == name)
    (planned,) = (planned for planned in plan.stages if planned.name == stage)
    case = planned.cases[-1]
    target = runner.command(case, plan.root)
    commands = [runner.command(other, plan.root) for other in planned.cases]
    if commands.count(target) != 1:
        raise ValueError(f'Stage {stage} runs its last command more than once.')
    one, two = _seeded(base)
    # -P keeps the harness's own modules off the wrapper's import path
    argv = (
        sys.executable,
        '-P',
        f'{doctor.__file__}',
        kind,
        json.dumps(target),
        f'{plan.root / _acts_on(name)}',
        f'{len(two.argv)}',
        *two.argv,
    )
    changed = dataclasses.replace(two, name=f'doctored {name}', argv=argv)
    expected = tuple(
        Difference(stage, changed.name, case.name, aspect, detail)
        for aspect, detail in _shows_as(name, kind, case, plan.corpus)
    )
    found = runner.run(plan, [one, changed], now=now, only=[stage], keep=keep)
    return Check(
        f'doctored {name}',
        expected,
        found.differences,
        found.report,
        found.streams,
    )


# ------ helper functions


def _settle(plan: Plan, check: Check) -> None:
    """Remove the trees a passed check kept, and file those of a failed one."""
    if check.passed:
        shutil.rmtree(plan.kept)
        return
    target = plan.work / _FAILED / check.name
    target.parent.mkdir(exist_ok=True)
    plan.kept.rename(target)


def _seeded(base: Side) -> list[Side]:
    """Return ``base`` twice, each under its own fixed hash seed."""
    return [
        dataclasses.replace(
            base,
            name=f'{base.name} seed {value}',
            env={**base.env, 'PYTHONHASHSEED': value},
        )
        for value in _SEEDS
    ]


def _acts_on(name: str) -> str:
    """Return the path the doctored side of check ``name`` acts on, below the wiki root."""
    if name in ('extra', 'stray'):
        return _EXTRA
    if name in ('counts', 'cache-mode'):
        return _COUNTS
    if name in ('index', 'schema'):
        return _SEARCH
    return runner.WIKI_SETTINGS


def _shows_as(
    name: str,
    kind: str,
    case: Case,
    corpus: Corpus,
) -> list[tuple[str, str]]:
    """Return the aspect and detail of each difference a doctored command must show."""
    if kind in ('stdout', 'stderr', 'notice', 'identity'):
        return [(kind, '')]
    # the low bit flips in the one exit code the case allows
    if kind == 'exit':
        (code,) = case.codes
        return [('exit', ''), ('unexpected', f'exit {code ^ 1}')]
    if kind == 'counts':
        return [('cache', f'word_counts.json: {doctor.COUNTS_KEY}')]
    if kind == 'index':
        return [
            ('cache', f'search.db: files: {doctor.INDEX_PATH}'),
            ('cache', f'search.db: notes_fts: {doctor.INDEX_PATH}'),
        ]
    if kind == 'schema':
        return [('cache', f'search.db: schema: {doctor.SCHEMA_TABLE}')]
    target = _acts_on(name)
    # a path of the cache folder is compared by the cache rule alone
    if target.startswith(f'{cache.CACHE}/'):
        return [('cache', f'file list: {target[len(cache.CACHE) + 1 :]}')]
    relpath = runner.manifest_path(corpus, target)
    # a byte or a mode changed in place leaves the file's inode and mtime
    if kind in ('file', 'mode'):
        return [('tree', relpath)]
    # the same bytes under a new inode leave the manifest as it was
    if kind == 'rewrite':
        return [('touched', relpath)]
    result = [('tree', relpath), ('touched', relpath)]
    if not case.writes:
        result.append(('wrote', relpath))
    return result
