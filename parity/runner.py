"""Functions for running the matrix on one corpus."""

from __future__ import annotations

import dataclasses
import hashlib
import logging
import os
import pathlib
import shutil
import time
from collections.abc import Mapping, Sequence
from typing import Any, Optional

from . import cache, classify, compare, manifest, matrix, side, snapshot
from .cache import CACHE
from .classify import Excused, Judge
from .compare import Difference, Record, Run
from .manifest import Entry
from .matrix import Case, Sample, Stage
from .side import Side

__all__ = []

logger = logging.getLogger('parity')

#: the file that declares a wiki root, relative to it
WIKI_SETTINGS = '.wiki/settings.json'

# the clone of the checkout that every tree of a plan starts from
_SNAPSHOT = 'snapshot'
# the folder every side runs in, one at a time, so absolute paths match
_SLOT = 'slot'
# where the slots a run keeps are moved to
_KEPT = 'kept'
# the three folders of a slot: the clone, the scratch home, the neutral cwd
_TREE = 'tree'
_HOME = 'home'
_CWD = 'cwd'


@dataclasses.dataclass(frozen=True)
class Corpus:
    """One declared wiki inside a checkout.

    Attributes:
        label: Neutral label of the corpus.
        checkout: The repository checkout, or a plain folder, to snapshot.
        root: The wiki root, relative to the checkout.
        stages: Stages of the corpus's own, which run after the matrix's:
            a fixture family names its own pages there.
        alone: Whether the corpus's own stages run without the matrix's.

    """

    label: str
    checkout: pathlib.Path
    root: str
    stages: tuple[Stage, ...] = ()
    alone: bool = False


@dataclasses.dataclass(frozen=True)
class Plan:
    """A snapshot of one corpus and the stages to run on it.

    Attributes:
        corpus: The wiki the snapshot holds.
        work: Folder of the snapshot and of every clone.
        seed: Seed of the sample.
        sample: The folders, pages and words the stages name.
        stages: The stages of the matrix, then the corpus's own, in order.
        manifest: The manifest of the snapshot, keyed as a clone of it is.

    """

    corpus: Corpus
    work: pathlib.Path
    seed: int
    sample: Sample
    stages: tuple[Stage, ...]
    manifest: Mapping[str, Entry]

    @property
    def root(self: Plan) -> pathlib.Path:
        """Return the path every side finds the wiki at."""
        return self.work / _SLOT / _TREE / self.corpus.root

    @property
    def kept(self: Plan) -> pathlib.Path:
        """Return the folder that holds the trees a run keeps."""
        return self.work / _KEPT


@dataclasses.dataclass(frozen=True)
class Result:
    """What one matrix run found.

    Attributes:
        differences: Every difference between the sides that no entry of
            the registry explains.
        report: The run as JSON data: the sample, and each case's exit
            code, stream digests and touched count on every run.
        streams: The streams of each case whose streams differ, by a file
            name that carries the stage, the case number and the run.
        excused: The commands whose differences a registry entry explains.

    """

    differences: tuple[Difference, ...]
    report: Mapping[str, Any]
    streams: Mapping[str, bytes]
    excused: tuple[Excused, ...] = ()


def prepare(corpus: Corpus, *, work: pathlib.Path, seed: int = 0) -> Plan:
    """Snapshot ``corpus`` into the new folder ``work`` and plan its matrix.

    Args:
        corpus: The wiki to run on.
        work: New folder for the snapshot and the clones.
        seed: Seed of the matrix sample.

    Returns:
        The plan to hand to :func:`run`, and to :func:`discard` at the end.

    Raises:
        ValueError: If :func:`validate` refuses the corpus or the folder.

    """
    validate(corpus, work=work)
    work.mkdir(parents=True)
    source = work / _SNAPSHOT
    snapshot.snapshot(corpus.checkout, source)
    drawn = matrix.sample(source / corpus.root, seed=seed)
    # hash the snapshot once: every clone of it starts from these digests
    entries = manifest.scan(source, skip=frozenset({'.git'}))
    stages = corpus.stages
    if not corpus.alone:
        stages = (*matrix.stages(drawn), *stages)
    return Plan(
        corpus=corpus,
        work=work,
        seed=seed,
        sample=drawn,
        stages=stages,
        manifest={f'{_TREE}/{relpath}': entry for relpath, entry in entries.items()},
    )


def validate(corpus: Corpus, *, work: pathlib.Path) -> None:
    """Refuse a corpus that :func:`prepare` cannot snapshot into ``work``.

    The check copies nothing and makes no folder, so a caller with several
    corpora can refuse them all before it runs the first.

    Raises:
        ValueError: If a folder above ``work`` declares a wiki (a side runs
            under a scratch home, so even the user's own ``~/.wiki`` reads
            as one, and a command run from inside the snapshot's wiki
            would be refused as nested in it), if ``work`` exists, or if
            the checkout cannot be snapshotted (see
            :func:`parity.snapshot.validate`).

    """
    for parent in work.resolve().parents:
        if (parent / WIKI_SETTINGS).is_file():
            raise ValueError(
                f'Work folder {work} lies below {parent}, which holds'
                f' {WIKI_SETTINGS}; choose one outside it.'
            )
    if os.path.lexists(work):
        raise ValueError(f'Work folder {work} exists; the harness makes it new.')
    return snapshot.validate(corpus.checkout, work / _SNAPSHOT)


def run(
    plan: Plan,
    sides: Sequence[Side],
    *,
    now: str,
    only: Optional[Sequence[str]] = None,
    keep: bool = False,
) -> Result:
    """Run stages of the plan on every side and compare the sides.

    Every side runs every stage on its own clone of the snapshot, one side
    at a time at the same absolute path, so absolute paths in output match.
    A difference that an entry of the registry explains is excused, by the
    word of the first side's judge where the entry needs it.

    Args:
        plan: The snapshot and its stages, from :func:`prepare`.
        sides: The sides; the first is the reference the others are
            compared against.
        now: RFC 3339 instant for ``WIKI_FREEZE_TIME``.
        only: Names of the stages to run; a stage named here brings the
            stage it continues from. ``None`` runs them all.
        keep: Whether to leave the tree of every run in the plan's
            :attr:`~Plan.kept` folder, as ``<stage>/<run>``.

    Returns:
        The differences and the report.

    Raises:
        ValueError: If two sides share a name (a kept tree is filed under
            it), or if ``only`` names no stage of the plan: a run of no
            stage would report no difference.

    """
    # refuse sides that share a name
    names = [runner.name for runner in sides]
    if len(set(names)) != len(names):
        raise ValueError(f'Sides must have different names, got {names!r}.')
    # resolve the stages to run: each named one and the stage it continues
    stages = plan.stages
    if only is not None:
        known = [stage.name for stage in stages]
        unknown = sorted(set(only) - set(known))
        if unknown:
            raise ValueError(
                f'No stage is named {unknown!r}; the stages are {known!r}.'
            )
        bases = {stage.base for stage in stages if stage.name in only}
        stages = tuple(stage for stage in stages if stage.name in {*only, *bases})
    continued = {stage.base for stage in stages}
    slot = plan.work / _SLOT
    kept = plan.kept
    # the manifest each kept tree ended with, for the stage that continues it
    ended: dict[tuple[str, str], Mapping[str, Entry]] = {}
    # build the judge: the reference side's interpreter, under the variables
    # its commands get
    judge = None
    if sides and sides[0].judge:
        env = side.environment(sides[0], slot / _HOME, now=now)
        judge = Judge(sides[0].judge, env=env)
    differences = []
    excused = []
    streams = {}
    report = []
    for stage in stages:
        # each side on the snapshot, or each side on the tree each side left
        producers: list[Optional[Side]] = [None]
        if stage.base is not None:
            producers = list(sides)
        runs = []
        for runner in sides:
            for producer in producers:
                # resolve the tree the run starts from
                name = runner.name
                origin = plan.work / _SNAPSHOT
                entries = plan.manifest
                if (producer is not None) and (stage.base is not None):
                    name = f'{runner.name} on {producer.name}'
                    origin = kept / stage.base / producer.name
                    entries = ended[(stage.base, producer.name)]
                # run the stage and log how it ended
                started = time.monotonic()
                done, last = _run_stage(
                    stage=stage,
                    runner=runner,
                    name=name,
                    origin=origin,
                    entries=entries,
                    plan=plan,
                    now=now,
                )
                seconds = time.monotonic() - started
                label = plan.corpus.label
                exits = _exits(done)
                message = (
                    f'{label} {stage.name} {name}: {seconds:.1f} s, exit codes {exits}'
                )
                logger.info(message)
                # keep the slot for a later stage or the caller, or remove it
                runs.append(done)
                if stage.name in continued:
                    ended[(stage.name, name)] = last
                if keep or (stage.name in continued):
                    (kept / stage.name).mkdir(parents=True, exist_ok=True)
                    slot.rename(kept / stage.name / name)
                else:
                    shutil.rmtree(slot)
        # compare the runs of the stage
        folder = manifest_path(plan.corpus, CACHE)
        found = compare.stage(stage, runs, folder=folder)
        found, explained = classify.apply(stage, runs, found, judge=judge)
        differences.extend(found)
        excused.extend(explained)
        streams.update(_differing_streams(stage, runs))
        report.append(_stage_report(stage, runs, found, explained))
    # remove the kept trees and build the report
    if kept.exists() and not keep:
        shutil.rmtree(kept)
    document = {
        'corpus': plan.corpus.label,
        'seed': plan.seed,
        'sample': dataclasses.asdict(plan.sample),
        'stages': report,
    }
    return Result(
        differences=tuple(differences),
        report=document,
        streams=streams,
        excused=tuple(excused),
    )


def discard(plan: Plan) -> None:
    """Remove the plan's work folder: the snapshot and every tree kept in it."""
    return shutil.rmtree(plan.work)


def manifest_path(corpus: Corpus, relpath: str) -> str:
    """Return the manifest path of ``relpath``, a path below the wiki root."""
    return f'{_TREE}/{corpus.root}/{relpath}'


def command(case: Case, root: pathlib.Path) -> list[str]:
    """Return the wiki arguments of ``case`` for the wiki at ``root``.

    A case that runs from a neutral folder names the wiki with ``--path``;
    one that runs from inside the wiki names nothing.
    """
    if case.inside is not None:
        return list(case.args)
    return [*case.args, '--path', f'{root}']


# ------ helper functions


def _run_stage(
    stage: Stage,
    runner: Side,
    *,
    name: str,
    origin: pathlib.Path,
    entries: Mapping[str, Entry],
    plan: Plan,
    now: str,
) -> tuple[Run, dict[str, Entry]]:
    """Run every case of ``stage`` on a fresh clone of ``origin``.

    Returns:
        The run, and the manifest the clone ended with.

    """
    slot = plan.work / _SLOT
    # a snapshot becomes the slot's tree; a tree a stage left is a whole slot
    if stage.base is None:
        slot.mkdir()
        snapshot.clone(origin, slot / _TREE)
        (slot / _HOME).mkdir()
        (slot / _HOME / '.gitconfig').write_bytes(b'')
        (slot / _CWD).mkdir()
    else:
        snapshot.clone(origin, slot)
    if stage.settings is not None:
        (plan.root / WIKI_SETTINGS).write_text(stage.settings, encoding='utf-8')
    env = {**side.environment(runner, slot / _HOME, now=now), **stage.env}
    snapshot.refresh(slot / _TREE, env=env)
    if stage.cold and (plan.root / CACHE).exists():
        shutil.rmtree(plan.root / CACHE)
    # scan everything a command could write, the clone's own repository aside
    skip = frozenset({f'{_TREE}/.git'})
    before = manifest.scan(slot, skip=skip, origin=entries)
    baseline = {relpath: entry.content for relpath, entry in before.items()}
    view = cache.view(plan.root, _below(before, plan.corpus))
    first = view
    records = []
    for case in stage.cases:
        cwd = slot / _CWD
        if case.inside is not None:
            cwd = plan.root / case.inside
        settings = None
        if (plan.root / WIKI_SETTINGS).is_file():
            settings = (plan.root / WIKI_SETTINGS).read_bytes()
        args = command(case, plan.root)
        outcome = side.run(runner, args, cwd=cwd, env=env, notice=case.notice)
        after = manifest.scan(slot, skip=skip, previous=before)
        touched = manifest.touched(before, after)
        changes = manifest.changes(before, after)
        # a write that kept a file's times still changed its content
        if touched or changes:
            view = cache.view(plan.root, _below(after, plan.corpus), previous=view)
        record = Record(
            outcome=outcome,
            touched=tuple(touched),
            changes=changes,
            view=view,
            settings=settings,
        )
        records.append(record)
        before = after
    # hash every file again: the scans above trust a clone and a file's stat
    final = {
        relpath: entry.content
        for relpath, entry in manifest.scan(slot, skip=skip).items()
    }
    done = Run(
        name=name,
        baseline=baseline,
        view=first,
        records=tuple(records),
        final=final,
    )
    return done, before


def _below(
    entries: Mapping[str, Entry],
    corpus: Corpus,
) -> dict[str, Entry]:
    """Return the entries of the wiki root, keyed by path relative to it."""
    prefix = manifest_path(corpus, '')
    return {
        relpath[len(prefix) :]: entry
        for relpath, entry in entries.items()
        if relpath.startswith(prefix)
    }


def _exits(run: Run) -> dict[int, int]:
    """Return how many commands of ``run`` ended with each exit code."""
    codes = [record.outcome.code for record in run.records]
    return {code: codes.count(code) for code in sorted(set(codes))}


def _differing_streams(stage: Stage, runs: Sequence[Run]) -> dict[str, bytes]:
    """Return both streams of every run, for each case whose streams differ."""
    result = {}
    for index in range(len(stage.cases)):
        outcomes = [run.records[index].outcome for run in runs]
        pairs = {(outcome.stdout, outcome.stderr) for outcome in outcomes}
        if len(pairs) == 1:
            continue
        for run, outcome in zip(runs, outcomes):
            stem = f'{stage.name}/{index:03d} {run.name}'
            result[f'{stem}.out'] = outcome.stdout
            result[f'{stem}.err'] = outcome.stderr
    return result


def _stage_report(
    stage: Stage,
    runs: Sequence[Run],
    found: Sequence[Difference],
    explained: Sequence[Excused],
) -> dict[str, Any]:
    """Return one stage as JSON data."""
    # record what every run did on each case
    cases = []
    for index, case in enumerate(stage.cases):
        row: dict[str, Any] = {'name': case.name, 'args': list(case.args), 'runs': {}}
        for run in runs:
            record = run.records[index]
            row['runs'][run.name] = {
                'exit': record.outcome.code,
                'stdout': _stream_report(record.outcome.stdout),
                'stderr': _stream_report(record.outcome.stderr),
                'touched': len(record.touched),
            }
        cases.append(row)
    return {
        'name': stage.name,
        'runs': [run.name for run in runs],
        'exits': {
            run.name: {f'{code}': count for code, count in _exits(run).items()}
            for run in runs
        },
        'cases': cases,
        'no_hit': [
            case.name
            for index, case in enumerate(stage.cases)
            if case.hits and all(run.records[index].outcome.code == 1 for run in runs)
        ],
        'differences': [dataclasses.asdict(difference) for difference in found],
        'excused': [dataclasses.asdict(item) for item in explained],
    }


def _stream_report(stream: bytes) -> dict[str, Any]:
    """Return the size and digest of one stream."""
    return {'bytes': len(stream), 'sha256': hashlib.sha256(stream).hexdigest()}
