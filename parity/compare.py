"""Functions for comparing what two sides did on one stage."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from typing import Optional

from . import cache
from .matrix import Stage
from .side import Outcome

__all__ = [
    'BASELINE',
    'END',
    'Record',
    'Run',
    'Difference',
    'stage',
]

#: the case a difference carries when the trees differ before any command
BASELINE = '(baseline)'
#: the case a difference carries when a tree changed outside every command
END = '(end)'

# manifest content by path as a replay holds it: ``None`` for a path that is gone
_State = dict[str, Optional[tuple]]


@dataclasses.dataclass(frozen=True)
class Record:
    """What one command did on one side.

    Attributes:
        outcome: The streams and the exit code.
        touched: The paths the command touched.
        changes: The manifest content the command changed, by path;
            ``None`` for a path that is gone.
        view: The caches after the command.

    """

    outcome: Outcome
    touched: tuple[str, ...]
    changes: Mapping[str, Optional[tuple]]
    view: cache.View


@dataclasses.dataclass(frozen=True)
class Run:
    """One side's pass over a stage.

    Attributes:
        name: Label of the run: the side, and the side whose tree it ran on
            when that is another one.
        baseline: Manifest content before the first command, by path.
        view: The caches before the first command.
        records: One record per case of the stage.
        final: Manifest content after the last command, from a scan that
            hashed every file again.

    """

    name: str
    baseline: Mapping[str, tuple]
    view: cache.View
    records: tuple[Record, ...]
    final: Mapping[str, tuple]


@dataclasses.dataclass(frozen=True, order=True)
class Difference:
    """One thing two sides did differently.

    Attributes:
        stage: Name of the stage.
        run: Name of the run that differs from the stage's first run, or
            that breaks a rule on its own.
        case: Name of the case, or :data:`BASELINE` or :data:`END`.
        aspect: ``stdout``, ``stderr``, ``exit``, ``tree`` (manifest
            content), ``touched`` (the touched set), ``wrote`` (a read-only
            command touched a path outside the cache folder) or ``cache``.
        detail: The path or the cache key that differs; empty for a stream
            or an exit code.

    """

    stage: str
    run: str
    case: str
    aspect: str
    detail: str = ''


def stage(plan: Stage, runs: Sequence[Run], *, folder: str) -> list[Difference]:
    """Compare the first run of a stage against each of the others.

    Args:
        plan: The stage the runs covered.
        runs: The runs, each over a tree at the same absolute path.
        folder: The wiki's cache folder, as a manifest path.

    Returns:
        Every difference, in case order.

    """
    result = []
    for other in runs[1:]:
        result.extend(_pair(plan, runs[0], other, folder=folder))
    for run in runs:
        result.extend(_alone(plan, run, folder=folder))
    return result


# ------ helper functions


def _pair(plan: Stage, first: Run, second: Run, *, folder: str) -> list[Difference]:
    """Compare two runs of one stage, command by command."""
    found: list[tuple[str, str, str]] = []
    # the trees and caches both sides start from
    states: tuple[_State, _State] = (dict(first.baseline), dict(second.baseline))
    for relpath in sorted(set(states[0]) | set(states[1])):
        if _differs(states, relpath, folder=folder):
            found.append((BASELINE, 'tree', relpath))
    # NOTE: a tree another stage wrote holds pages each side stamped at its own
    #   moment, so the bytes of the counts cache are compared on a snapshot only
    wrote = plan.base is not None
    for detail in cache.compare(first.view, second.view, raw=not wrote):
        found.append((BASELINE, 'cache', detail))
    for case, one, two in zip(plan.cases, first.records, second.records):
        # streams and exit code
        if one.outcome.stdout != two.outcome.stdout:
            found.append((case.name, 'stdout', ''))
        if one.outcome.stderr != two.outcome.stderr:
            found.append((case.name, 'stderr', ''))
        if one.outcome.code != two.outcome.code:
            found.append((case.name, 'exit', ''))
        # manifest content of every path either side changed
        states[0].update(one.changes)
        states[1].update(two.changes)
        for relpath in sorted(set(one.changes) | set(two.changes)):
            if _differs(states, relpath, folder=folder):
                found.append((case.name, 'tree', relpath))
        # the touched set
        for relpath in sorted(set(one.touched) ^ set(two.touched)):
            found.append((case.name, 'touched', relpath))
        # the caches, whenever either side wrote anything at all
        for record in (one, two):
            if any(not _within(relpath, folder) for relpath in record.touched):
                wrote = True
        if one.touched or two.touched or one.changes or two.changes:
            for detail in cache.compare(one.view, two.view, raw=not wrote):
                found.append((case.name, 'cache', detail))
    return [Difference(plan.name, second.name, *item) for item in found]


def _alone(plan: Stage, run: Run, *, folder: str) -> list[Difference]:
    """Check one run against the rules that need no second side."""
    found: list[tuple[str, str, str]] = []
    state: _State = dict(run.baseline)
    for case, record in zip(plan.cases, run.records):
        state.update(record.changes)
        # a read-only command writes nothing outside the caches
        if case.writes:
            continue
        for relpath in record.touched:
            if not _within(relpath, folder):
                found.append((case.name, 'wrote', relpath))
    # the tree holds what the commands left: the scans between commands hash
    # only the files whose stat changed, and the last scan hashes them all
    live = {relpath: content for relpath, content in state.items() if content}
    for relpath in sorted(set(live) | set(run.final)):
        if live.get(relpath) != run.final.get(relpath):
            found.append((END, 'tree', relpath))
    return [Difference(plan.name, run.name, *item) for item in found]


def _within(relpath: str, folder: str) -> bool:
    """Return whether ``relpath`` is the cache folder or lies below it."""
    return (relpath == folder) or relpath.startswith(f'{folder}/')


def _differs(
    states: tuple[_State, _State],
    relpath: str,
    *,
    folder: str,
) -> bool:
    """Return whether two trees hold ``relpath`` differently, outside the caches."""
    if _within(relpath, folder):
        return False
    return states[0].get(relpath) != states[1].get(relpath)
