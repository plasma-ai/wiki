"""Functions for running a gate.

A gate is the harness run whole on real wikis: its layers, one after the
other, each a set of harness commands. :func:`layers` names the commands
of a full gate and of a daily one, and :func:`run` runs them, each as a
process of its own, and times each layer: the processor time its processes
took, the time on the clock, and how many processors that comes to.
"""

from __future__ import annotations

import concurrent.futures
import dataclasses
import os
import pathlib
import subprocess
import sys
import time
from collections.abc import Iterator, Sequence
from typing import Optional

from . import suite

__all__ = []

#: the two gates
FULL = 'full'
DAILY = 'daily'

# the parts of a gate: the oracle file level with v1, the harness checked
# with v1 alone, two engines on the corpora, and the merge history
_ORACLE = 'oracle'
_SELF = 'self-check'
_CORPUS = 'corpora'
_HISTORY = 'history'
# the folder that holds the harness, which every step runs from
_ROOT = pathlib.Path(__file__).resolve().parent.parent


@dataclasses.dataclass(frozen=True)
class Step:
    """One harness command of a layer.

    Attributes:
        name: Label of the step, unique in its layer: a corpus, or the
            layer's own name.
        argv: The arguments of ``python -m parity``, whole.
        log: The file that takes what the command prints.

    """

    name: str
    argv: tuple[str, ...]
    log: pathlib.Path


@dataclasses.dataclass(frozen=True)
class Layer:
    """The steps of one layer, which run side by side.

    Attributes:
        name: Label of the layer.
        part: The part of the gate it belongs to.
        steps: The commands.

    """

    name: str
    part: str
    steps: tuple[Step, ...]


@dataclasses.dataclass(frozen=True)
class Timed:
    """One step, as it ran.

    Attributes:
        name: Label of the step.
        code: The exit code of its command.
        seconds: How long the command took.
        cpu: The processor seconds the command and every process it
            waited for took.

    """

    name: str
    code: int
    seconds: float
    cpu: float


@dataclasses.dataclass(frozen=True)
class Done:
    """One layer, as it ran.

    Attributes:
        layer: The layer.
        steps: Its steps, as they ran.
        seconds: How long the layer took, from its first step's start to
            its last step's end.
        load: The load average of the host when the layer started.

    """

    layer: Layer
    steps: tuple[Timed, ...]
    seconds: float
    load: float

    @property
    def cpu(self: Done) -> float:
        """Return the processor seconds the layer's steps took together."""
        return round(sum(step.cpu for step in self.steps), 1)

    @property
    def parallelism(self: Done) -> float:
        """Return how many processors the layer kept busy, on average."""
        if not self.seconds:
            return 0.0
        return round(self.cpu / self.seconds, 2)

    @property
    def code(self: Done) -> int:
        """Return the exit code of the layer: the highest of its steps'."""
        return max(step.code for step in self.steps)


def layers(
    kind: str,
    *,
    corpora: pathlib.Path,
    labels: Sequence[str],
    loop: Sequence[str],
    work: pathlib.Path,
    out: pathlib.Path,
    cache: pathlib.Path,
    engine: Optional[str] = None,
    sides: Optional[pathlib.Path] = None,
    fresh: int = 200,
    sample: int = 20,
    jobs: Optional[int] = None,
    now: Optional[str] = None,
) -> list[Layer]:
    """Return the layers of a gate, in the order they run.

    A full gate checks the harness with v1 alone first: the self-check and
    the registry check, the replay of v1's tests, the fixture families, the
    perturbation in both modes and a sample of the merges. With an engine
    it then compares v1 with the engine over the corpora, the families and
    the perturbation, and it ends with the merge history. A daily gate
    holds the comparison with the engine, without the loop of fresh seeds,
    and the merges the cache does not hold yet.

    Args:
        kind: :data:`FULL` or :data:`DAILY`.
        corpora: The corpora file.
        labels: The corpora to run on.
        loop: The corpora whose wikis the loop of fresh seeds runs on.
            The first also takes the registry check, which one corpus
            proves. Empty leaves the loop out.
        work: Folder each step makes its work folder in.
        out: Folder each step writes its results in.
        cache: Folder of the cache of the merge history.
        engine: Name of the side compared against v1. ``None`` leaves out
            every layer that compares two engines.
        sides: The sides file that declares the engine, if one does.
        fresh: How many fresh seeds the loop runs on each of its wikis.
        sample: The fixed sample of the merges: one merge in this many.
        jobs: How many trees or merges a step runs at once. ``None``
            leaves each command its default.
        now: RFC 3339 instant every side runs at. ``None`` leaves each
            command its default.

    Returns:
        The layers. One whose commands take a corpus has a step for each.

    """
    shared = ['--corpora', f'{corpora}']
    chosen = [part for label in labels for part in ('--corpus', label)]
    clock = [] if now is None else ['--now', now]
    pace = [] if jobs is None else ['--jobs', f'{jobs}']
    second = []
    if engine is not None:
        second = ['--engine', engine]
        if sides is not None:
            second += ['--sides', f'{sides}']

    def layer(
        name: str, part: str, argv: Sequence[str], *, each: bool = False
    ) -> Layer:
        """Return a layer of one command, or of that command on each corpus."""
        steps = []
        for label in labels if each else [name]:
            narrowed = ['--corpus', label] if each else []
            folder = pathlib.Path(name) / label if each else pathlib.Path(name)
            places = ['--work', f'{work / folder}', '--out', f'{out / folder}']
            steps.append(
                Step(
                    name=label,
                    argv=(*argv, *narrowed, *places),
                    log=out / folder.with_suffix('.txt'),
                )
            )
        return Layer(name=name, part=part, steps=tuple(steps))

    reference = ['--side', suite.REFERENCE]
    looped = [part for label in loop for part in ('--corpus', label)]
    replayed = ['merges', *shared, *chosen, *clock, *pace]
    # v2 is level with v1, as the remote-tracking references hold it
    check = Step(name='oracle', argv=('oracle', 'check'), log=out / 'oracle.txt')
    result = [Layer(name='oracle', part=_ORACLE, steps=(check,))]
    # the harness checks itself with v1 alone
    if kind == FULL:
        result.append(
            layer(
                'selfcheck',
                _SELF,
                ['selfcheck', *reference, *shared, *clock],
                each=True,
            )
        )
        proven = ['--corpus', loop[0] if loop else labels[0]]
        result.append(
            layer('registry', _SELF, ['registry', *reference, *shared, *proven, *clock])
        )
        result.append(layer('replay', _SELF, ['replay']))
        result.append(layer('fixtures', _SELF, ['fixtures', *clock]))
        perturbed = ['perturb', 'corpus', *shared, *clock]
        result.append(layer('perturb-corpus', _SELF, perturbed, each=True))
        if loop:
            seeds = ['--fresh', f'{fresh}']
            loops = ['perturb', 'loop', *shared, *looped, *seeds, *pace, *clock]
            result.append(layer('perturb-loop', _SELF, loops))
        sampled = [*replayed, '--sample', f'{sample}']
        result.append(layer('merges-sample', _SELF, sampled))
    # v1 against the engine, on the corpora and on what the harness makes of them
    if engine is not None:
        between = ['--a', suite.REFERENCE, '--b', engine, '--registered']
        if sides is not None:
            between += ['--sides', f'{sides}']
        matrix = ['matrix', *between, *shared, *clock]
        result.append(layer('matrix', _CORPUS, matrix, each=True))
        result.append(layer('fixtures-engine', _CORPUS, ['fixtures', *second, *clock]))
        perturbed = ['perturb', 'corpus', *second, *shared, *clock]
        result.append(layer('perturb-corpus-engine', _CORPUS, perturbed, each=True))
        if loop and (kind == FULL):
            seeds = ['--fresh', f'{fresh}']
            loops = [
                'perturb',
                'loop',
                *second,
                *shared,
                *looped,
                *seeds,
                *pace,
                *clock,
            ]
            result.append(layer('perturb-loop-engine', _CORPUS, loops))
        # the engine on the merges of the day, each against v1 as it runs
        # there; the layer below then keeps what v1 did
        if kind == DAILY:
            newest = [*replayed, *second, '--cache', f'{cache}', '--new']
            result.append(layer('merges-engine', _HISTORY, newest))
    # v1 on every merge the cache does not hold, then the engine on them all
    result.append(layer('merges', _HISTORY, [*replayed, '--cache', f'{cache}']))
    if (engine is not None) and (kind == FULL):
        held = [*replayed, *second, '--cache', f'{cache}']
        result.append(layer('merges-engine', _HISTORY, held))
    return result


def run(planned: Sequence[Layer], *, jobs: Optional[int] = None) -> Iterator[Done]:
    """Run each layer and time it.

    The layers run one after the other, so a layer's time is its own. The
    steps of a layer run side by side, each as a process of its own that
    prints to the step's log.

    Args:
        planned: The layers, from :func:`layers`.
        jobs: How many steps of a layer run at once. ``None`` runs them all.

    Yields:
        Each layer as it ran, when its last step ends.

    """
    for layer in planned:
        load, *_ = os.getloadavg()
        started = time.monotonic()
        workers = jobs or len(layer.steps)
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            steps = tuple(pool.map(_step, layer.steps))
        seconds = round(time.monotonic() - started, 1)
        yield Done(layer=layer, steps=steps, seconds=seconds, load=round(load, 2))


# ------ helper functions


def _step(step: Step) -> Timed:
    """Run one step to its end and return how it ran."""
    step.log.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with open(step.log, 'wb') as log:
        process = subprocess.Popen(
            [sys.executable, '-m', 'parity', *step.argv],
            cwd=_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        # wait for the process here, to be told what it and the processes
        # it waited for took
        _, status, usage = os.wait4(process.pid, 0)
        process.returncode = os.waitstatus_to_exitcode(status)
    return Timed(
        name=step.name,
        code=process.returncode,
        seconds=round(time.monotonic() - started, 1),
        cpu=round(usage.ru_utime + usage.ru_stime, 1),
    )
