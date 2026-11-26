"""Functions for the interpreters run: the alt side of v1 against the oracle.

The alt side is the oracle commit under a second interpreter and its own
packages, so a wiki that moves from that side to an engine that follows the
oracle sees a change wherever the two sides differ. :func:`run` compares the
two over the matrix on a snapshot of one corpus. What differs runs once more
with the crossed side, the alt side's interpreter under the oracle side's
packages, which says whether the interpreter, a package or both are the
cause. :func:`changed` asks both interpreters what they make of every code
point, and :func:`scan` reads the names of the wiki for a code point they do
not agree on.
"""

from __future__ import annotations

import dataclasses
import functools
import itertools
import json
import os
import pathlib
import subprocess
import time
from collections.abc import Mapping, Sequence
from typing import Any, Optional, Union

from . import compare, points, runner
from .cache import CACHE
from .classify import _PAIRED, Excused
from .compare import END, Difference, Run
from .matrix import Stage
from .oracle import Oracle
from .runner import _SNAPSHOT, Corpus
from .side import Side

__all__ = []

#: the names of the three v1 sides of an interpreters run: the reference, the
#: side compared with it, and the side a difference runs on once more
REFERENCE = 'oracle'
COMPARED = 'alt'
CROSSED = 'crossed'
#: the three causes a difference between the first two sides can have
INTERPRETER = 'interpreter'
PACKAGES = 'packages'
BOTH = 'both'

# the folder git keeps a repository in, whose names are none of the wiki's
_GIT = '.git'


@dataclasses.dataclass(frozen=True)
class Cause:
    """What causes one thing the compared side did differently.

    Attributes:
        item: A difference between the two sides, or a command a registry
            entry explains.
        cause: :data:`PACKAGES` when the crossed side does there what the
            reference does, :data:`INTERPRETER` when it does what the
            compared side does, and :data:`BOTH` when it does neither.

    """

    item: Union[Difference, Excused]
    cause: str


@dataclasses.dataclass(frozen=True)
class Hit:
    """One name that holds a code point the two interpreters do not agree on.

    Attributes:
        path: The path of the named file or folder, relative to the wiki
            root.
        points: Those code points of the name, in order.

    """

    path: str
    points: tuple[int, ...]


@dataclasses.dataclass(frozen=True)
class Trial:
    """What the interpreters run of one corpus found.

    Attributes:
        label: Label of the corpus.
        result: The matrix, the compared side against the reference.
        again: The stages in which the sides differ, the crossed and the
            compared side against the reference; ``None`` when they differ
            in none.
        causes: The cause of each difference between the sides and of each
            excused command of the first run. A rule a run broke by itself
            (``wrote``, ``unexpected``, ``notice``, ``identity``, a tree at
            its end) is no difference between the sides and has none.
        alone: What the second run shows of the crossed side that the first
            run does not show of the compared side.
        names: How many names of the wiki the scan read.
        hits: The names that hold a changed code point.
        failed: Whether the trial is not the expected result: a difference,
            a command the registry excuses, or a hit.
        seconds: How long the trial took.

    """

    label: str
    result: runner.Result
    again: Optional[runner.Result]
    causes: tuple[Cause, ...]
    alone: tuple[Union[Difference, Excused], ...]
    names: int
    hits: tuple[Hit, ...]
    failed: bool
    seconds: float


def run(
    corpus: Corpus,
    *,
    sides: Sequence[Side],
    changed: Mapping[int, Sequence[str]],
    work: pathlib.Path,
    now: str,
    seed: int = 0,
) -> Trial:
    """Compare the alt side with the oracle on a snapshot of ``corpus``.

    Args:
        corpus: The wiki to run on.
        sides: The reference, the compared side and the crossed side, in
            that order.
        changed: The code points the first two sides do not agree on, from
            :func:`changed`.
        work: New folder for the snapshot and the clones. The snapshot of a
            trial that failed stays there: the checkout it was taken from
            may move on.
        now: RFC 3339 instant for ``WIKI_FREEZE_TIME``.
        seed: Seed of the matrix sample.

    Returns:
        What the trial found.

    Raises:
        ValueError: If :func:`parity.runner.validate` refuses the corpus or
            the folder.

    """
    started = time.monotonic()
    reference, compared, crossed = sides
    plan = runner.prepare(corpus, work=work, seed=seed)
    result = runner.run(plan, [reference, compared], now=now)
    # run what differs between the sides once more, with the crossed side
    shown = [*result.differences, *result.excused]
    paired = [item for item in shown if _between(item)]
    again = None
    causes = []
    alone = []
    if paired:
        stages = sorted({item.stage for item in paired})
        stands = _runs(reference, crossed=crossed, compared=compared)
        apart: tuple[set[Difference], set[Difference]] = (set(), set())
        each = functools.partial(
            _apart,
            stands=stands,
            folder=runner.manifest_path(corpus, CACHE),
            found=apart,
        )
        again = runner.run(
            plan=plan,
            sides=[reference, crossed, compared],
            now=now,
            only=stages,
            each=each,
        )
        causes = [Cause(item=item, cause=_cause(item, *apart)) for item in paired]
        # gather what the crossed side shows and the compared side did not
        known = set(shown)
        alone = [
            item
            for item in (*again.differences, *again.excused)
            if (item.run in stands)
            and (dataclasses.replace(item, run=stands[item.run]) not in known)
        ]
    names, hits = scan(work / _SNAPSHOT / corpus.root, changed)
    failed = bool(shown or hits)
    if not failed:
        runner.discard(plan)
    return Trial(
        label=corpus.label,
        result=result,
        again=again,
        causes=tuple(causes),
        alone=tuple(alone),
        names=names,
        hits=tuple(hits),
        failed=failed,
        seconds=round(time.monotonic() - started, 1),
    )


def validate(named: Oracle) -> None:
    """Refuse an oracle file whose crossed side does not cross the other two.

    A difference is laid to the interpreter where the crossed side does
    what the compared side does, and to a package where it does what the
    reference does. That holds only while the side runs the compared
    side's interpreter and the reference's packages.

    Raises:
        ValueError: If the file does not name the three sides, or if the
            crossed side names another interpreter than the compared side
            or other packages than the reference.

    """
    sides = (REFERENCE, COMPARED, CROSSED)
    missing = [name for name in sides if name not in named.builds]
    if missing:
        raise ValueError(f'{named.path.name} names no side {missing!r}.')
    reference = named.builds[REFERENCE]
    compared = named.builds[COMPARED]
    crossed = named.builds[CROSSED]
    if (crossed.python, crossed.unicode) != (compared.python, compared.unicode):
        raise ValueError(
            f'{named.path.name}: side {CROSSED} must name the interpreter of'
            f' side {COMPARED} ({compared.python}, Unicode {compared.unicode}),'
            f' got {crossed.python}, Unicode {crossed.unicode}.'
        )
    packages = (reference.constraints, reference.libyaml)
    if (crossed.constraints, crossed.libyaml) != packages:
        raise ValueError(
            f'{named.path.name}: side {CROSSED} must name the packages of side'
            f' {REFERENCE} ({reference.constraints.name}, libyaml'
            f' {reference.libyaml}), got {crossed.constraints.name}, libyaml'
            f' {crossed.libyaml}.'
        )


def changed(first: Side, second: Side) -> dict[int, tuple[str, ...]]:
    """Return the code points the interpreters of two sides do not agree on.

    Each side's ``points`` command prints what its interpreter makes of
    every code point (see :mod:`parity.points`).

    Returns:
        The names of the properties that differ, by code point, for every
        code point with one.

    Raises:
        RuntimeError: If a side's command fails, or does not answer for
            every code point.

    """
    ours, theirs = _table(first), _table(second)
    result = {}
    for point, (held, other) in enumerate(zip(ours, theirs)):
        if held == other:
            continue
        result[point] = tuple(
            name
            for name, value, against in zip(points.PROPERTIES, held, other)
            if value != against
        )
    return result


def scan(
    root: pathlib.Path,
    changed: Mapping[int, Sequence[str]],
) -> tuple[int, list[Hit]]:
    """Read the name of ``root`` and every name below it for a changed code point.

    The scan reads the name of the wiki root, which names the wiki where
    its index names none, and the name of every file and folder below it,
    whatever the wiki's exclusions make of it: an engine reads a name
    before it can exclude it. It follows no symlink and reads nothing below
    a ``.git`` folder.

    Args:
        root: The wiki root, in a snapshot.
        changed: The changed code points, from :func:`changed`.

    Returns:
        How many names the scan read, and the names that hold a changed
        code point, in path order; the path of the root is ``.``.

    """
    # list the root, then every file and folder below it
    paths = [f'{root}']
    for folder, folders, files in os.walk(root):
        if _GIT in folders:
            folders.remove(_GIT)
        paths.extend(os.path.join(folder, name) for name in (*folders, *files))
    hits = []
    for path in paths:
        name = os.path.basename(path)
        found = sorted({ord(char) for char in name} & changed.keys())
        if found:
            relpath = os.path.relpath(path, root)
            hits.append(Hit(path=relpath, points=tuple(found)))
    return len(paths), sorted(hits, key=lambda hit: hit.path)


def report(trial: Trial, changed: Mapping[int, Sequence[str]]) -> dict[str, Any]:
    """Return one trial as JSON data, in the form ``matrix`` writes a corpus.

    The first run is the document itself. ``rerun`` holds the stages of the
    second run, the cause of each difference between the sides and what
    the crossed side shows alone, or null where no second run was needed,
    and ``names`` the count of the scan and its hits, each with the
    properties that differ for its code points.
    """
    rerun = None
    if trial.again is not None:
        rerun = {
            'side': CROSSED,
            'stages': trial.again.report['stages'],
            'causes': [
                {**dataclasses.asdict(cause.item), 'cause': cause.cause}
                for cause in trial.causes
            ],
            'alone': [dataclasses.asdict(item) for item in trial.alone],
        }
    hits = [
        {
            'path': hit.path,
            'points': {spell(point): list(changed[point]) for point in hit.points},
        }
        for hit in trial.hits
    ]
    return {
        **trial.result.report,
        'rerun': rerun,
        'names': {'read': trial.names, 'hits': hits},
        'failed': trial.failed,
        'seconds': trial.seconds,
    }


def spell(point: int) -> str:
    """Return a code point as the Unicode standard writes it.

    >>> spell(0x1C89)
    'U+1C89'
    """
    return f'U+{point:04X}'


# ------ helper functions


def _table(item: Side) -> list[list[Any]]:
    """Return what the interpreter of ``item`` makes of every code point.

    Returns:
        The properties of each code point, in the order of
        :data:`parity.points.PROPERTIES`, by code point.

    Raises:
        RuntimeError: If the side's ``points`` command fails, or does not
            answer for every code point.

    """
    # NOTE: nothing is inherited: a PYTHONPATH of the caller's would change
    #   what the interpreter imports
    result = subprocess.run(
        item.points,
        env={},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        error = result.stderr.strip()
        raise RuntimeError(
            f'Side {item.name!r}: points failed (exit {result.returncode}): {error!r}'
        )
    table: list[list[Any]] = []
    for line in result.stdout.splitlines():
        first, last, properties = json.loads(line)
        if first != len(table):
            break
        table.extend(itertools.repeat(properties, last - first + 1))
    if len(table) != points.LAST + 1:
        raise RuntimeError(
            f'Side {item.name!r}: points answered for {len(table)} code'
            f' points in order, not for {points.LAST + 1}.'
        )
    return table


def _between(item: Union[Difference, Excused]) -> bool:
    """Return whether ``item`` is something two runs did differently.

    The rest is a rule one run broke by itself: an aspect no second run is
    read for, and a tree that does not hold what its commands left.
    """
    if isinstance(item, Excused):
        return True
    return (item.aspect in _PAIRED) and (item.case != END)


def _runs(reference: Side, *, crossed: Side, compared: Side) -> dict[str, str]:
    """Return the run of the compared side that each run of the crossed side stands for.

    A stage names a run after its side, and a run on the tree another run
    left after both (see :func:`parity.runner.run`). The crossed side
    stands where the compared side stood in the first run. The second run
    holds both, so a stage that continues another also runs each of the
    two on the tree the other left: such a run stands for none.
    """
    stands = {reference.name: reference.name, crossed.name: compared.name}
    runs = {crossed.name: compared.name}
    for name, producer in itertools.product(stands, repeat=2):
        if crossed.name in (name, producer):
            runs[f'{name} on {producer}'] = f'{stands[name]} on {stands[producer]}'
    return runs


def _apart(
    stage: Stage,
    runs: Sequence[Run],
    *,
    stands: Mapping[str, str],
    folder: str,
    found: tuple[set[Difference], set[Difference]],
) -> None:
    """Gather what each crossed run of a stage did differently from two others.

    A crossed run is compared with the first run of the stage, the
    reference, and with the run of the compared side it stands for, by the
    rules every two runs are compared by. No registry entry is asked: an
    excused command differs all the same.

    Args:
        stage: The stage the runs covered.
        runs: The runs of the second run; the first is the reference.
        stands: The run each crossed run stands for, from :func:`_runs`.
        folder: The wiki's cache folder, as a manifest path.
        found: The differences from the reference and those from the
            compared side, which the call adds to; each is named after the
            run its crossed run stands for.

    """
    named = {run.name: run for run in runs}
    for name in stands.keys() & named.keys():
        others = (runs[0], named[stands[name]])
        for other, differences in zip(others, found):
            differences.update(
                dataclasses.replace(item, run=stands[name])
                for item in compare.stage(stage, [other, named[name]], folder=folder)
                if _between(item)
            )


def _cause(
    item: Union[Difference, Excused],
    reference: set[Difference],
    compared: set[Difference],
) -> str:
    """Return the cause of one thing the compared side did differently.

    Args:
        item: A difference between the first two sides, or a command a
            registry entry explains.
        reference: What the crossed side did differently from the
            reference, from :func:`_apart`.
        compared: What it did differently from the compared side.

    Returns:
        :data:`PACKAGES` where the crossed side does what the reference
        does, :data:`INTERPRETER` where it does what the compared side
        does, and :data:`BOTH` where it does neither.

    """
    shows = []
    for found in (reference, compared):
        if isinstance(item, Excused):
            # an entry explains a command whole, so any aspect of it counts
            command = (item.stage, item.run, item.case)
            commands = {(other.stage, other.run, other.case) for other in found}
            shows.append(command in commands)
        else:
            shows.append(item in found)
    if not shows[0]:
        return PACKAGES
    if not shows[1]:
        return INTERPRETER
    return BOTH
