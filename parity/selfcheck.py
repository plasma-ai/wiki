"""The harness self-check: one side against itself, clean and doctored."""

from __future__ import annotations

import dataclasses
import json
import pathlib
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
    ('notice', 'notice', 'full'),
)

# the two sides of one engine differ only in their hash seed
_SEEDS = ('1', '2')
# the paths a doctored side acts on, relative to the wiki root
_EXTRA = 'parity_doctored.md'
_COUNTS = f'{cache.CACHE}/word_counts.json'
_SEARCH = f'{cache.CACHE}/search.db'


@dataclasses.dataclass(frozen=True)
class Check:
    """One part of the self-check.

    Attributes:
        name: ``clean``, or ``doctored`` and the name of the check.
        expected: The differences the harness must report, and no other.
        found: The differences it reported.
        report: The matrix run as JSON data.

    """

    name: str
    expected: tuple[Difference, ...]
    found: tuple[Difference, ...]
    report: Mapping[str, Any]

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
        work: New folder for the snapshot and the clones, removed at the
            end.
        now: RFC 3339 instant for ``WIKI_NOW``.
        seed: Seed of the matrix sample.

    Returns:
        The clean check, then one check per row of :data:`CHECKS`. The
        ``notice`` row is left out for an engine that prints no notice.

    """
    plan = runner.prepare(corpus, work=work, seed=seed)
    result = [clean(plan, base, now=now)]
    for name, kind, _ in CHECKS:
        if (kind == 'notice') and not base.notice:
            continue
        result.append(doctored(plan, base, name, now=now))
    runner.discard(plan)
    return result


def clean(plan: Plan, base: Side, *, now: str) -> Check:
    """Run the whole matrix with ``base`` on both sides, under two hash seeds.

    Returns:
        The check, which expects no difference.

    """
    found = runner.run(plan, _seeded(base), now=now)
    return Check('clean', (), found.differences, found.report)


def doctored(plan: Plan, base: Side, name: str, *, now: str) -> Check:
    """Run one stage with ``base`` against a doctored copy of itself.

    The doctored side changes one thing in the stage's last command.

    Args:
        plan: The snapshot to run on.
        base: The engine both sides run.
        name: Name of a row of :data:`CHECKS`.
        now: RFC 3339 instant for ``WIKI_NOW``.

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
        f'{plan.root / _acts_on(kind)}',
        f'{len(two.argv)}',
        *two.argv,
    )
    changed = dataclasses.replace(two, name=f'doctored {name}', argv=argv)
    expected = tuple(
        Difference(stage, changed.name, case.name, aspect, detail)
        for aspect, detail in _shows_as(kind, case, plan.corpus)
    )
    found = runner.run(plan, [one, changed], now=now, only=[stage])
    return Check(f'doctored {name}', expected, found.differences, found.report)


# ------ helper functions


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


def _acts_on(kind: str) -> str:
    """Return the path a doctored side of ``kind`` acts on, below the wiki root."""
    if kind == 'extra':
        return _EXTRA
    if kind == 'counts':
        return _COUNTS
    if kind == 'index':
        return _SEARCH
    return runner.WIKI_SETTINGS


def _shows_as(kind: str, case: Case, corpus: Corpus) -> list[tuple[str, str]]:
    """Return the aspect and detail of each difference a doctored command must show."""
    if kind in ('stdout', 'stderr', 'notice'):
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
    relpath = runner.manifest_path(corpus, _acts_on(kind))
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
