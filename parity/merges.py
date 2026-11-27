"""Functions for replaying the merges of a repository's history.

A wiki is merged by git, and the engine is part of that merge: git hands
each index both sides changed to the engine's merge driver, and the tree the
merge leaves is one the engine must then take. The trees real merges leave
were written by every older version of the engine, so no generated tree is
as far from converged, in as many ways, as they are.

:func:`prepare` snapshots the repository of a checkout and lists the merge
commits of its history. :func:`run` replays each one on every side: it checks
out the first parent in a repository of the harness's own, merges the other
parents with the side's merge driver, and runs ``update --check``, ``update``
and ``update --check`` again on each wiki the merged tree declares. What the
reference side did on a merge is kept in a cache, so a later run asks it of
the other sides only.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import logging
import math
import multiprocessing
import os
import pathlib
import pickle
import queue
import resource
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import zlib
from collections.abc import Iterator, Mapping, Sequence
from typing import Any, Optional

from . import cache, classify, compare, manifest, runner, side, snapshot
from .cache import CACHE
from .classify import Excused, Judge
from .compare import BASELINE, END, Difference, Record, Run
from .manifest import Entry
from .matrix import Case, Stage
from .oracle import Oracle
from .runner import _CWD, _HOME, _SNAPSHOT, _TREE, WIKI_SETTINGS, Corpus
from .side import TRACE, Outcome, Side

__all__ = []

logger = logging.getLogger('parity')

#: the stage of the merge itself, and its one case
MERGE = 'merge'

# the files of the harness that make a pass and a cache entry: the steps of a
# pass, the environment and the launcher of a side, the git calls, the scans,
# the folders of a slot, and the classes an entry holds. A cache entry is
# keyed on their bytes, so a change to one of them replays every merge on the
# reference side again; a module a pass comes to run through joins them
_SOURCES = (
    'cache.py',
    'compare.py',
    'launcher.py',
    'manifest.py',
    'matrix.py',
    'merges.py',
    'runner.py',
    'side.py',
    'snapshot.py',
)
# the folder of the harness, which holds them
_HARNESS = pathlib.Path(__file__).resolve().parent
# the repository of a worker while it replays no merge
_WORKER = 'worker-{index}'
# the home its judge runs under
_JUDGE = 'judge-{index}'
# how many merges a worker takes at once, at most: neighbors, whose trees are
# close, and few enough that the workers end together though the merges of a
# history differ in what they cost
_NEIGHBORS = 8
# seconds between two looks at the workers while no merge has ended
_PATIENCE = 5
# the name and the driver git holds for the attribute `merge=wiki`
_DRIVER_NAME = 'wiki merge (auto-resolve generated sections)'
_DRIVER_ARGS = '_merge %O %A %B %L %P'
# who commits a merge that ends clean
_IDENTITY = {
    'GIT_AUTHOR_NAME': 'parity',
    'GIT_AUTHOR_EMAIL': 'parity@example.invalid',
    'GIT_COMMITTER_NAME': 'parity',
    'GIT_COMMITTER_EMAIL': 'parity@example.invalid',
}
# the exit codes of a merge: clean, or left with conflicts
_MERGED = frozenset({0, 1})
# the exit codes of the commands on a merged tree: such a tree may hold
# anything, conflict markers among it, which every command refuses with 2
_CHECKED = frozenset({0, 1, 2})
_UPDATED = frozenset({0, 2})
# the file of the oracle that holds its version, and the line that states it
_VERSION_FILE = 'wiki/__init__.py'
_VERSION_LINE = b'__version__'
# the folder of the oracle's engine files, and the one below it that holds
# no engine file
_ENGINE = 'wiki'
_SKILLS = 'wiki/skills/'


@dataclasses.dataclass(frozen=True)
class History:
    """The history of one checkout, with the wikis the corpora declare in it.

    Attributes:
        label: Neutral label of the history: the labels of its corpora.
        checkout: The repository checkout whose history is replayed.
        roots: The wiki roots the corpora declare, relative to the checkout.

    """

    label: str
    checkout: pathlib.Path
    roots: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class Merge:
    """One merge commit.

    Attributes:
        commit: The commit.
        parents: Its parents, the first one first.

    """

    commit: str
    parents: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class Plan:
    """A snapshot of one repository and the merges to replay from it.

    Attributes:
        history: The history the snapshot holds.
        work: Folder of the snapshot and of the workers' repositories.
        at: Folder every merge is replayed in, each below its commit.
        head: The commit the snapshot's ``HEAD`` names.
        merges: Every merge commit ``HEAD`` reaches, oldest first.

    """

    history: History
    work: pathlib.Path
    at: pathlib.Path
    head: str
    merges: tuple[Merge, ...]

    @property
    def objects(self: Plan) -> pathlib.Path:
        """Return the object store of the snapshot."""
        return self.work / _SNAPSHOT / '.git' / 'objects'


@dataclasses.dataclass(frozen=True)
class Stamp:
    """What a cache entry is keyed on beside its merge.

    Attributes:
        engine: Digest of the oracle's engine files: every file under
            ``wiki/`` outside ``wiki/skills``, with the version line left
            out, so a release that changes no engine file keeps the cache.
        constraints: Digest of the file that pins the packages of the
            reference side's venv.
        python: Version of the reference side's interpreter.
        git: What ``git --version`` prints.
        harness: Digest of the files of the harness that make a pass and
            an entry.

    """

    engine: str
    constraints: str
    python: str
    git: str
    harness: str


@dataclasses.dataclass(frozen=True)
class Replayed:
    """One merge, as a run found it.

    Attributes:
        commit: The merge commit.
        differences: Every difference between the sides that no entry of
            the registry explains.
        excused: The commands whose differences a registry entry explains.
        report: The merge as JSON data.
        streams: The streams of each case whose streams differ, by a file
            name that carries the stage, the case number and the run.
        cached: Whether the reference side's part was read from the cache.
        error: The error that ended the replay, or ``None``.

    """

    commit: str
    differences: tuple[Difference, ...] = ()
    excused: tuple[Excused, ...] = ()
    report: Mapping[str, Any] = dataclasses.field(default_factory=dict)
    streams: Mapping[str, bytes] = dataclasses.field(default_factory=dict)
    cached: bool = False
    error: Optional[str] = None


@dataclasses.dataclass(frozen=True)
class _Pass:
    """One side's pass over one merge.

    Attributes:
        runs: The run of the merge stage, then of each wiki's stage.
        stages: The stages, one for each run.
        digest: Digest of the tree the merge started from: the first
            parent, as git checks it out.
        unmerged: The entries of the index that the merge left unmerged.
        calls: How often git called the merge driver, where the side's
            identity records count it.
        stray: The paths a command changed outside the wiki it ran on.
        seconds: How long the pass took.
        cpu: The processor seconds the side's own processes took: the
            merge with its driver, and the commands.

    """

    runs: tuple[Run, ...]
    stages: tuple[Stage, ...]
    digest: str
    unmerged: tuple[str, ...]
    calls: Optional[int]
    stray: tuple[str, ...]
    seconds: float
    cpu: float


def histories(corpora: Sequence[Corpus]) -> list[History]:
    """Return the histories ``corpora`` lie in, one for each checkout.

    Raises:
        ValueError: If a checkout is no repository: it has no history.

    """
    grouped: dict[pathlib.Path, list[Corpus]] = {}
    for corpus in corpora:
        if not os.path.isdir(corpus.checkout / '.git'):
            raise ValueError(
                f'Corpus {corpus.label!r} lies in no repository, so it has no'
                f' history to replay.'
            )
        grouped.setdefault(corpus.checkout.resolve(), []).append(corpus)
    # the label and the roots are sorted: a cache entry is keyed on the roots,
    # whatever order a run names the corpora in
    return [
        History(
            label='+'.join(sorted(corpus.label for corpus in held)),
            checkout=checkout,
            roots=tuple(sorted({corpus.root for corpus in held})),
        )
        for checkout, held in grouped.items()
    ]


def validate(history: History, *, work: pathlib.Path) -> None:
    """Refuse a history that :func:`prepare` cannot snapshot into ``work``.

    Raises:
        ValueError: If ``work`` exists, or if the checkout cannot be
            snapshotted (see :func:`parity.snapshot.validate`).

    """
    if os.path.lexists(work):
        raise ValueError(f'Work folder {work} exists; the harness makes it new.')
    return snapshot.validate(history.checkout, work / _SNAPSHOT)


def prepare(history: History, *, work: pathlib.Path, at: pathlib.Path) -> Plan:
    """Snapshot the repository of ``history`` into the new folder ``work``.

    Only the repository is cloned, not the files of the checkout: a replay
    checks out trees of the history, and nothing of the tree the checkout
    stands at today.

    Args:
        history: The history to replay.
        work: New folder for the snapshot and the workers' repositories.
        at: Folder every merge is replayed in, each below its commit.

    Returns:
        The plan to hand to :func:`run`.

    Raises:
        ValueError: If :func:`validate` refuses the history or the folder.

    """
    validate(history, work=work)
    (work / _SNAPSHOT).mkdir(parents=True)
    held = work / _SNAPSHOT / '.git'
    snapshot.clone(history.checkout / '.git', held)
    head = snapshot._git(['--git-dir', f'{held}', 'rev-parse', 'HEAD'], env=_plain())
    merges = tuple(listed(held))
    return Plan(history=history, work=work, at=at, head=head, merges=merges)


def listed(repository: pathlib.Path) -> list[Merge]:
    """Return every merge commit that ``HEAD`` reaches, oldest first.

    The command reads the repository and writes nothing there, so a run may
    ask it of a checkout before it snapshots one.

    Args:
        repository: The ``.git`` folder of a repository.

    """
    listing = snapshot._git(
        [
            '--git-dir',
            f'{repository}',
            'rev-list',
            '--merges',
            '--parents',
            '--topo-order',
            '--reverse',
            'HEAD',
        ],
        env=_plain(),
    )
    merges = []
    for line in listing.splitlines():
        commit, *parents = line.split()
        merges.append(Merge(commit=commit, parents=tuple(parents)))
    return merges


def sample(merges: Sequence[Merge], one_in: int) -> list[Merge]:
    """Return the merges of the fixed sample: one in ``one_in``, by commit.

    A merge is in the sample when its commit, read as a number, is a
    multiple of ``one_in``. So a merge is in the sample of every run or of
    none, however far the history has grown.
    """
    return [merge for merge in merges if int(merge.commit, 16) % one_in == 0]


def stamp(oracle: Oracle, name: str) -> Stamp:
    """Return what the cache is keyed on, of the oracle and of the host.

    Args:
        oracle: What ``oracle.toml`` names.
        name: Name of the v1 side whose runs the cache holds.

    Returns:
        The digests of the engine files and of the side's constraints
        file, the side's interpreter, the host's git, and the digest of the
        harness's own files.

    """
    env = _plain()
    checkout = oracle.checkout
    listing = snapshot._git(
        ['ls-tree', '-r', '-z', oracle.commit, '--', _ENGINE],
        cwd=checkout,
        env=env,
    )
    digest = hashlib.sha256()
    for row in listing.split('\0'):
        if not row:
            continue
        held, relpath = row.split('\t', 1)
        if relpath.startswith(_SKILLS):
            continue
        # the file that states the version counts without that line
        if relpath == _VERSION_FILE:
            lines = (checkout / relpath).read_bytes().splitlines(keepends=True)
            kept = [line for line in lines if not line.startswith(_VERSION_LINE)]
            held = hashlib.sha256(b''.join(kept)).hexdigest()
        digest.update(f'{held}\t{relpath}\0'.encode('utf-8', 'surrogateescape'))
    harness = hashlib.sha256()
    for relpath in _SOURCES:
        held = hashlib.sha256((_HARNESS / relpath).read_bytes()).hexdigest()
        harness.update(f'{held}\t{relpath}\0'.encode())
    build = oracle.builds[name]
    return Stamp(
        engine=digest.hexdigest(),
        constraints=hashlib.sha256(build.constraints.read_bytes()).hexdigest(),
        python=build.python,
        git=snapshot._git(['--version'], env=env),
        harness=harness.hexdigest(),
    )


def stored(
    plan: Plan,
    merges: Sequence[Merge],
    *,
    now: str,
    store: pathlib.Path,
    stamped: Stamp,
) -> list[Merge]:
    """Return the merges of ``merges`` whose reference pass the cache holds."""
    return [
        merge
        for merge in merges
        if _entry_path(store, _key(plan, merge, stamped, now=now)).is_file()
    ]


def run(
    plan: Plan,
    merges: Sequence[Merge],
    sides: Sequence[Side],
    *,
    now: str,
    second: bool = False,
    store: Optional[pathlib.Path] = None,
    stamped: Optional[Stamp] = None,
    jobs: int = 1,
) -> Iterator[Replayed]:
    """Replay ``merges`` on every side and compare the sides on each.

    A merge runs at a path of its own, which its commit names, so the
    paths in what a side prints are those of every other run of that
    merge. The workers take the merges in runs of neighbors: a worker
    keeps one repository, and the tree of a merge is close to the tree of
    the merge before it. A run is short enough that every worker has one.

    Args:
        plan: The snapshot, from :func:`prepare`.
        merges: The merges to replay, of the plan's.
        sides: The sides; the first is the reference the others are
            compared against.
        now: RFC 3339 instant for ``WIKI_FREEZE_TIME``.
        second: Whether a side is a second engine. Its merges take the
            reference side's part from the cache where the cache holds
            it, and what the registry excuses is listed apart. Without
            one, a merge the cache holds is not replayed at all, and a
            merge that shows no difference is stored.
        store: Folder of the cache. ``None`` reads and stores nothing.
        stamped: What the cache is keyed on, from :func:`stamp`; needed
            with ``store``.
        jobs: How many merges are replayed at once.

    Yields:
        Each merge as the run found it: the ones the cache answers alone
        first, then the others as each ends.

    Raises:
        ValueError: If two sides share a name.
        RuntimeError: If the workers end before every merge has.

    """
    names = [item.name for item in sides]
    if len(set(names)) != len(names):
        raise ValueError(f'Sides must have different names, got {names!r}.')
    # answer from the cache alone where no side has to run
    waiting = []
    for merge in merges:
        entry = None
        if (store is not None) and (stamped is not None) and not second:
            entry = _load(store, _key(plan, merge, stamped, now=now))
        if entry is None:
            waiting.append(merge)
        else:
            yield Replayed(commit=merge.commit, report=entry['report'], cached=True)
    if not waiting:
        return
    # deal the others out in runs of neighbors, which the workers take in turn
    size = min(_NEIGHBORS, math.ceil(len(waiting) / jobs))
    runs = [waiting[start : start + size] for start in range(0, len(waiting), size)]
    count = min(jobs, len(runs))
    # NOTE: a worker is a process started from nothing: the harness holds
    #   threads, and a forked copy of a process with threads may hang
    context = multiprocessing.get_context('spawn')
    tasks = context.Queue()
    results = context.Queue()
    for item in (*runs, *[None] * count):
        tasks.put(item)
    workers = [
        context.Process(
            target=_work,
            args=(plan, index, tasks, results, sides),
            kwargs={'now': now, 'second': second, 'store': store, 'stamped': stamped},
        )
        for index in range(count)
    ]
    for worker in workers:
        worker.start()
    left = len(waiting)
    while left:
        try:
            done = results.get(timeout=_PATIENCE)
        except queue.Empty:
            # a worker that died took its merges with it
            if not any(worker.is_alive() for worker in workers) and results.empty():
                raise RuntimeError(
                    f'The workers ended with {left} merges left.'
                ) from None
            continue
        left -= 1
        yield done
    for worker in workers:
        worker.join()


def summary(replayed: Sequence[Replayed]) -> dict[str, int]:
    """Return how much a run of merges replayed, and what the merges were.

    Returns:
        How many merges ran, how many of them came from the cache, ended in
        an error, differ or hold an excused command, and of those with a
        report: how many reached the merge driver, were left with
        conflicts, held a wiki that ``update`` refused, and held one that
        one ``update`` did not converge.

    """
    counts: dict[str, int] = dict.fromkeys(
        (
            'merges',
            'cached',
            'errors',
            'differing',
            'excused',
            'driven',
            'conflicted',
            'refused',
            'unconverged',
        ),
        0,
    )
    for item in replayed:
        counts['merges'] += 1
        counts['cached'] += item.cached
        counts['errors'] += item.error is not None
        counts['differing'] += bool(item.differences)
        counts['excused'] += bool(item.excused)
        report = item.report
        if not report:
            continue
        counts['driven'] += bool(report['driver_calls'])
        counts['conflicted'] += bool(report['unmerged'])
        counts['refused'] += bool(report['refused'])
        counts['unconverged'] += bool(report['unconverged'])
    return counts


# ------ helper functions


def _plain() -> dict[str, str]:
    """Return the environment of a git command of the harness's own.

    The caller's variables, under no config but a repository's own: the
    user's and the system's would add hooks, filters and drivers.
    """
    env = dict(os.environ)
    env['GIT_CONFIG_GLOBAL'] = os.devnull
    env['GIT_CONFIG_NOSYSTEM'] = '1'
    return env


def _key(plan: Plan, merge: Merge, stamped: Stamp, *, now: str) -> dict[str, Any]:
    """Return what the cache entry of ``merge`` is keyed on."""
    return {
        **dataclasses.asdict(stamped),
        'platform': sys.platform,
        'commit': merge.commit,
        'now': now,
        'at': f'{plan.at / merge.commit}',
        'roots': list(plan.history.roots),
    }


def _entry_path(store: pathlib.Path, key: Mapping[str, Any]) -> pathlib.Path:
    """Return the file of the cache entry keyed on ``key``."""
    text = json.dumps(key, sort_keys=True)
    name = hashlib.sha256(text.encode('utf-8')).hexdigest()
    return store / name[:2] / name


def _load(store: pathlib.Path, key: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """Return the cache entry keyed on ``key``, or ``None``."""
    path = _entry_path(store, key)
    if not path.is_file():
        return None
    # NOTE: an entry is the harness's own: `_save` wrote it, into a folder
    #   the run names, and it holds the records of a pass as they are compared
    entry = pickle.loads(zlib.decompress(path.read_bytes()))  # noqa: S301
    if entry['key'] != key:
        return None
    return entry


def _save(store: pathlib.Path, entry: Mapping[str, Any]) -> None:
    """Write a cache entry, whole or not at all."""
    path = _entry_path(store, entry['key'])
    path.parent.mkdir(parents=True, exist_ok=True)
    data = zlib.compress(pickle.dumps(dict(entry)))
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        handle.write(data)
    os.replace(handle.name, path)


def _work(
    plan: Plan,
    index: int,
    tasks: multiprocessing.Queue[Optional[Sequence[Merge]]],
    results: multiprocessing.Queue[Replayed],
    sides: Sequence[Side],
    *,
    now: str,
    second: bool,
    store: Optional[pathlib.Path],
    stamped: Optional[Stamp],
) -> None:
    """Replay runs of merges in one repository, as one worker, until none is left.

    Args:
        plan: The snapshot.
        index: The number of the worker, which names its folders.
        tasks: The runs of merges to take; ``None`` ends the worker.
        results: Where each merge goes as the worker found it.
        sides: The sides.
        now: RFC 3339 instant for ``WIKI_FREEZE_TIME``.
        second: Whether a side is a second engine.
        store: Folder of the cache, or ``None``.
        stamped: What the cache is keyed on, or ``None``.

    """
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    folder = plan.work / _WORKER.format(index=index)
    _repository(folder / _TREE, plan)
    # the judge: the reference side's interpreter, under the variables its
    # commands get
    judge = None
    if sides[0].judge:
        asked = plan.work / _JUDGE.format(index=index)
        asked.mkdir(exist_ok=True)
        env = side.environment(sides[0], asked, now=now)
        judge = Judge(sides[0].judge, env=env)
    held: dict[str, Entry] = {}
    while (merges := tasks.get()) is not None:
        for merge in merges:
            slot = plan.at / merge.commit
            slot.parent.mkdir(parents=True, exist_ok=True)
            folder.rename(slot)
            # NOTE: an error must not take the other merges with it: a replay
            #   runs for hours, and one checkout that fails would end it
            failed = False
            try:
                done, held = _replay(
                    plan,
                    merge,
                    sides,
                    slot=slot,
                    held=held,
                    judge=judge,
                    now=now,
                    second=second,
                    store=store,
                    stamped=stamped,
                )
            except Exception as e:
                traceback.print_exc()
                done = Replayed(commit=merge.commit, error=f'{type(e).__name__}: {e}')
                failed = True
            finally:
                slot.rename(folder)
            # a pass that was cut short may leave a lock or half a merge behind
            if failed:
                shutil.rmtree(folder)
                _repository(folder / _TREE, plan)
                held = {}
            _log(plan, done)
            results.put(done)
    shutil.rmtree(folder)


def _log(plan: Plan, done: Replayed) -> None:
    """Log how one merge ended."""
    label = plan.history.label
    commit = done.commit[:12]
    if done.error is not None:
        logger.info(f'{label} {commit}: error: {done.error}')
        return
    report = done.report
    count = len(done.differences)
    s = 's' if count != 1 else ''
    seconds = sum(report['seconds'].values())
    cpu = sum(report['cpu'].values())
    message = (
        f'{label} {commit}: {count} difference{s}, {report["driver_calls"]} driver'
        f' calls, {report["unmerged"]} unmerged, {seconds:.1f} s, {cpu:.1f} s of'
        f' the sides'
    )
    logger.info(message)


def _repository(tree: pathlib.Path, plan: Plan) -> None:
    """Make the repository a worker replays merges in.

    It borrows the snapshot's objects and holds nothing else of the
    checkout: no hook, no filter, no local config and no linked worktree,
    each of which a copy of the checkout's own repository would run or
    read. Git packs nothing here behind a worker's back.
    """
    tree.mkdir(parents=True)
    env = _plain()
    snapshot._git(['init', '-q'], cwd=tree, env=env)
    alternates = tree / '.git' / 'objects' / 'info' / 'alternates'
    alternates.write_text(f'{plan.objects}\n', encoding='utf-8')
    snapshot._git(['config', 'gc.auto', '0'], cwd=tree, env=env)
    snapshot._git(['config', 'maintenance.auto', 'false'], cwd=tree, env=env)


def _replay(
    plan: Plan,
    merge: Merge,
    sides: Sequence[Side],
    *,
    slot: pathlib.Path,
    held: dict[str, Entry],
    judge: Optional[Judge],
    now: str,
    second: bool,
    store: Optional[pathlib.Path],
    stamped: Optional[Stamp],
) -> tuple[Replayed, dict[str, Entry]]:
    """Replay one merge on every side, compare the sides, and use the cache.

    Returns:
        The merge as the run found it, and the manifest the slot ended
        with, for the next merge of the worker.

    """
    # read the reference side's part from the cache, with a second engine
    key = None
    entry = None
    if (store is not None) and (stamped is not None):
        key = _key(plan, merge, stamped, now=now)
        if second:
            entry = _load(store, key)
    reference, *others = sides
    passes = {}
    for item in others if entry is not None else sides:
        passes[item.name], held = _pass(plan, merge, item, slot, held, now=now)
    # stand the cached pass beside the others, on the trees they started from
    names = [item.name for item in sides]
    if entry is not None:
        first = passes[others[0].name]
        passes[reference.name] = _restored(entry, reference.name, first)
    done = [passes[name] for name in names]
    differences, excused, stages = _compare(done, judge=judge)
    if entry is not None:
        differences.extend(_moved(entry, done[1], reference.name))
    streams = {}
    for stage, runs in stages:
        streams.update(runner._differing_streams(stage, runs))
    report = _report(merge, done, stages, differences, excused)
    # store what the reference side did, once nothing stands against it
    clean = not (second or differences or excused)
    if clean and (store is not None) and (key is not None):
        _save(store, _entry(key, done[0], report))
    replayed = Replayed(
        commit=merge.commit,
        differences=tuple(differences),
        excused=tuple(excused),
        report=report,
        streams=streams,
        cached=entry is not None,
    )
    return replayed, held


def _pass(
    plan: Plan,
    merge: Merge,
    item: Side,
    slot: pathlib.Path,
    held: dict[str, Entry],
    *,
    now: str,
) -> tuple[_Pass, dict[str, Entry]]:
    """Replay one merge on one side.

    Args:
        plan: The snapshot.
        merge: The merge.
        item: The side.
        slot: The folder the merge runs in; its tree is the repository.
        held: The manifest the slot ended the last pass with.
        now: RFC 3339 instant for ``WIKI_FREEZE_TIME``.

    Returns:
        The pass, and the manifest the slot ended with.

    Raises:
        RuntimeError: If git cannot check out the first parent.

    """
    started = time.monotonic()
    tree = slot / _TREE
    plain = _plain()
    # check out the first parent, and nothing else
    first, *rest = merge.parents
    snapshot._git(['checkout', '-q', '-f', '--detach', first], cwd=tree, env=plain)
    snapshot._git(['clean', '-ffdxq'], cwd=tree, env=plain)
    for folder in (_HOME, _CWD):
        if (slot / folder).exists():
            shutil.rmtree(slot / folder)
        (slot / folder).mkdir()
    (slot / _HOME / '.gitconfig').write_bytes(b'')
    env = side.environment(item, slot / _HOME, now=now)
    # register the side's merge driver, as an engine registers its own
    driver = ' '.join(shlex.quote(part) for part in item.argv)
    snapshot._git(['config', 'merge.wiki.name', _DRIVER_NAME], cwd=tree, env=plain)
    snapshot._git(
        ['config', 'merge.wiki.driver', f'{driver} {_DRIVER_ARGS}'],
        cwd=tree,
        env=plain,
    )
    skip = frozenset({f'{_TREE}/.git'})
    before = manifest.scan(slot, skip=skip, previous=held)
    state: dict[str, Optional[tuple]] = {
        relpath: entry.content for relpath, entry in before.items()
    }
    # merge the other parents: git commits a merge that ends clean
    spent = _spent()
    outcome, calls = _merge(item, rest, tree=tree, env=env, now=now)
    cpu = _spent() - spent
    listing = snapshot._git(['ls-files', '-u', '-z'], cwd=tree, env=plain)
    unmerged = tuple(row for row in listing.split('\0') if row)
    after = manifest.scan(slot, skip=skip, previous=before)
    none = cache.view(slot / _CWD, {})
    record = Record(
        outcome=outcome,
        touched=tuple(manifest.touched(before, after)),
        changes=manifest.changes(before, after),
        view=none,
    )
    case = Case(name=MERGE, args=(MERGE,), codes=_MERGED, writes=True)
    stages = [Stage(name=MERGE, cases=(case,))]
    digest = _digest(state)
    baseline = _live(state)
    state.update(record.changes)
    declared = [
        root for root in plan.history.roots if (tree / root / WIKI_SETTINGS).is_file()
    ]
    runs = [
        Run(
            name=item.name,
            baseline=baseline,
            view=none,
            records=(record,),
            final=_live(state),
        )
    ]
    # run the commands on each wiki the merged tree declares
    for root in declared:
        stage = _stage(root)
        baseline = _held(state, declared, root)
        prefix = f'{_TREE}/{root}/'
        view = cache.view(tree / root, _below(after, prefix))
        first_view = view
        records = []
        for case in stage.cases:
            settings = (tree / root / WIKI_SETTINGS).read_bytes()
            args = [*case.args, '--path', f'{tree / root}']
            spent = _spent()
            outcome = side.run(item, args, cwd=slot / _CWD, env=env)
            cpu += _spent() - spent
            # scan where the command may write: its wiki, the home and the
            # neutral folder
            parts = (f'{_TREE}/{root}', _HOME, _CWD)
            old, new, after = _rescan(slot, after, parts)
            touched = manifest.touched(old, new)
            changes = manifest.changes(old, new)
            if touched or changes:
                view = cache.view(tree / root, _below(after, prefix), previous=view)
            records.append(
                Record(
                    outcome=outcome,
                    touched=tuple(touched),
                    changes=changes,
                    view=view,
                    settings=settings,
                )
            )
            state.update(changes)
        stages.append(stage)
        runs.append(
            Run(
                name=item.name,
                baseline=baseline,
                view=first_view,
                records=tuple(records),
                final=_held(state, declared, root),
            )
        )
    # scan the whole slot once more: a command writes in its own wiki only
    final = manifest.scan(slot, skip=skip, previous=after)
    stray = sorted({*manifest.touched(after, final), *manifest.changes(after, final)})
    done = _Pass(
        runs=tuple(runs),
        stages=tuple(stages),
        digest=digest,
        unmerged=unmerged,
        calls=calls,
        stray=tuple(stray),
        seconds=round(time.monotonic() - started, 1),
        cpu=round(cpu, 1),
    )
    return done, final


def _merge(
    item: Side,
    parents: Sequence[str],
    *,
    tree: pathlib.Path,
    env: Mapping[str, str],
    now: str,
) -> tuple[Outcome, Optional[int]]:
    """Merge ``parents`` into the tree's ``HEAD`` with the side's driver.

    Returns:
        What git printed and returned, and how often it called the driver:
        each call leaves the side's identity record. ``None`` for a side
        that leaves none.

    """
    # a clean merge is committed at the run's instant, by a fixed identity
    instant = dt.datetime.fromisoformat(now).timestamp()
    date = f'@{int(instant)} +0000'
    env = {
        **env,
        **_IDENTITY,
        'GIT_AUTHOR_DATE': date,
        'GIT_COMMITTER_DATE': date,
        'GIT_MERGE_AUTOEDIT': 'no',
    }
    cmd = [
        'git',
        'merge',
        '--no-ff',
        '--no-edit',
        '--no-stat',
        '--allow-unrelated-histories',
        *parents,
    ]
    calls = None
    identity = True
    with tempfile.TemporaryDirectory() as folder:
        trace = pathlib.Path(folder) / 'trace'
        if item.identity:
            env[TRACE] = f'{trace}'
        result = subprocess.run(
            cmd,
            cwd=tree,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=side._TIMEOUT,
        )
        # every call of the driver is the engine the side names
        if item.identity:
            lines = trace.read_bytes().splitlines() if trace.is_file() else []
            calls = len(lines)
            identity = all(_identified(line, item.identity) for line in lines)
    outcome = Outcome(
        stdout=result.stdout,
        stderr=result.stderr,
        code=result.returncode,
        identity=identity,
    )
    return outcome, calls


def _identified(line: bytes, expected: Mapping[str, str]) -> bool:
    """Return whether ``line`` of a trace file is the identity record ``expected``."""
    try:
        record = json.loads(line)
    except ValueError:
        return False
    return record == dict(expected)


def _stage(root: str) -> Stage:
    """Return the stage of the wiki at ``root`` on a merged tree."""
    cases = (
        Case(
            name='update --check --full',
            args=('update', '--check', '--full'),
            codes=_CHECKED,
        ),
        Case(
            name='update --full',
            args=('update', '--full'),
            codes=_UPDATED,
            writes=True,
        ),
        Case(name='update --check', args=('update', '--check'), codes=_CHECKED),
    )
    return Stage(name=root, cases=cases, base=MERGE)


def _rescan(
    slot: pathlib.Path,
    held: Mapping[str, Entry],
    parts: Sequence[str],
) -> tuple[dict[str, Entry], dict[str, Entry], dict[str, Entry]]:
    """Scan the folders ``parts`` of the slot again.

    Returns:
        What ``held`` lists below the folders, what they hold now, and
        ``held`` with the one in place of the other.

    """
    below = tuple(f'{part}/' for part in parts)
    old = {}
    rest = {}
    for relpath, entry in held.items():
        if relpath.startswith(below):
            old[relpath] = entry
        else:
            rest[relpath] = entry
    new = {}
    for part in parts:
        prefix = f'{part}/'
        known = {
            relpath[len(prefix) :]: entry
            for relpath, entry in old.items()
            if relpath.startswith(prefix)
        }
        scanned = manifest.scan(slot / part, previous=known)
        new.update({f'{prefix}{relpath}': entry for relpath, entry in scanned.items()})
    return old, new, {**rest, **new}


def _below(entries: Mapping[str, Entry], prefix: str) -> dict[str, Entry]:
    """Return the entries below ``prefix``, keyed by path relative to it."""
    return {
        relpath[len(prefix) :]: entry
        for relpath, entry in entries.items()
        if relpath.startswith(prefix)
    }


def _live(state: Mapping[str, Optional[tuple]]) -> dict[str, tuple]:
    """Return the manifest content of the paths ``state`` still holds."""
    return {
        relpath: content for relpath, content in state.items() if content is not None
    }


def _held(
    state: Mapping[str, Optional[tuple]],
    roots: Sequence[str],
    root: str,
) -> dict[str, tuple]:
    """Return the tree the stage of the wiki at ``root`` is compared on.

    The caches of the other wikis are left out: each side wrote them at its
    own moment, and they are compared by the cache rule in the stage of
    the wiki they belong to.
    """
    others = tuple(f'{_TREE}/{other}/{CACHE}/' for other in roots if other != root)
    return {
        relpath: content
        for relpath, content in state.items()
        if (content is not None) and not relpath.startswith(others)
    }


def _spent() -> float:
    """Return the processor seconds the processes this one waited for have taken."""
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return usage.ru_utime + usage.ru_stime


def _digest(state: Mapping[str, Optional[tuple]]) -> str:
    """Return a digest of the manifest content of a whole tree."""
    digest = hashlib.sha256()
    for relpath in sorted(state):
        content = state[relpath]
        if content is not None:
            row = f'{relpath}\0{content!r}\0'
            digest.update(row.encode('utf-8', 'surrogateescape'))
    return digest.hexdigest()


def _compare(
    passes: Sequence[_Pass],
    *,
    judge: Optional[Judge],
) -> tuple[list[Difference], list[Excused], list[tuple[Stage, list[Run]]]]:
    """Compare the first pass of a merge against each of the others.

    Returns:
        The differences no registry entry explains, the commands one
        does, and each stage of the first pass with its runs.

    """
    first, *others = passes
    differences = []
    excused = []
    stages = []
    for index, stage in enumerate(first.stages):
        # a later pass ran the stage only if its merged tree declares the wiki
        runs = [first.runs[index]]
        for other in others:
            names = [item.name for item in other.stages]
            if stage.name in names:
                runs.append(other.runs[names.index(stage.name)])
            else:
                differences.append(_undeclared(stage, other))
        folder = ''
        if stage.name != MERGE:
            folder = f'{_TREE}/{stage.name}/{CACHE}'
        found = compare.stage(stage, runs, folder=folder)
        # no entry of the registry speaks of a merge
        explained: list[Excused] = []
        if stage.name != MERGE:
            found, explained = classify.apply(stage, runs, found, judge=judge)
        differences.extend(found)
        excused.extend(explained)
        stages.append((stage, runs))
    # a wiki only a later pass's merged tree declares
    held = {stage.name for stage in first.stages}
    for other in others:
        for stage in other.stages:
            if stage.name not in held:
                differences.append(_undeclared(stage, other))
    # the index entries a merge left unmerged, and what a pass broke alone
    for other in others:
        name = other.runs[0].name
        for row in sorted(set(first.unmerged) ^ set(other.unmerged)):
            _, _, relpath = row.partition('\t')
            detail = f'{_TREE}/{relpath}'
            differences.append(Difference(MERGE, name, MERGE, 'unmerged', detail))
    for done in passes:
        last = done.stages[-1].name
        name = done.runs[0].name
        for relpath in done.stray:
            differences.append(Difference(last, name, END, 'tree', relpath))
    return differences, excused, stages


def _undeclared(stage: Stage, done: _Pass) -> Difference:
    """Return the difference of a wiki that one pass's merged tree alone declares."""
    relpath = f'{_TREE}/{stage.name}/{WIKI_SETTINGS}'
    return Difference(stage.name, done.runs[0].name, BASELINE, 'tree', relpath)


def _entry(key: Mapping[str, Any], done: _Pass, report: Mapping[str, Any]) -> dict:
    """Return the cache entry of one pass of the reference side."""
    return {
        'key': dict(key),
        'stages': done.stages,
        'digest': done.digest,
        'records': tuple(run.records for run in done.runs),
        'views': tuple(run.view for run in done.runs),
        'unmerged': done.unmerged,
        'calls': done.calls,
        'report': dict(report),
    }


def _restored(entry: Mapping[str, Any], name: str, beside: _Pass) -> _Pass:
    """Return the pass a cache entry holds, on the tree ``beside`` started from.

    An entry holds what the reference side did, and of the tree it did it
    on only the digest. So the pass stands on the tree the pass beside it
    started the merge from, and each later stage on what the entry's own
    commands made of that tree; :func:`_moved` reports a tree whose digest
    is not the entry's.
    """
    state: dict[str, Optional[tuple]] = dict(beside.runs[0].baseline)
    roots = [stage.name for stage in entry['stages'][1:]]
    runs = []
    for stage, records, view in zip(entry['stages'], entry['records'], entry['views']):
        # the merge is compared on the whole tree, as a live pass is
        others = roots if stage.name != MERGE else [stage.name]
        baseline = _held(state, others, stage.name)
        for record in records:
            state.update(record.changes)
        runs.append(
            Run(
                name=name,
                baseline=baseline,
                view=view,
                records=records,
                final=_held(state, others, stage.name),
            )
        )
    return _Pass(
        runs=tuple(runs),
        stages=entry['stages'],
        digest=entry['digest'],
        unmerged=entry['unmerged'],
        calls=entry['calls'],
        stray=(),
        seconds=0.0,
        cpu=0.0,
    )


def _moved(entry: Mapping[str, Any], beside: _Pass, name: str) -> list[Difference]:
    """Return a difference if the merge started from another tree than the entry's.

    The tree a merge starts from is git's work: the first parent, checked
    out. Where its digest is not the one the entry holds, the entry is of
    another tree than this run's, and no path names where.
    """
    if entry['digest'] == beside.digest:
        return []
    return [Difference(MERGE, name, BASELINE, 'tree')]


def _report(
    merge: Merge,
    passes: Sequence[_Pass],
    stages: Sequence[tuple[Stage, list[Run]]],
    differences: Sequence[Difference],
    excused: Sequence[Excused],
) -> dict[str, Any]:
    """Return one merge as JSON data."""
    first, *_ = passes
    rows = []
    for stage, runs in stages:
        found = [item for item in differences if item.stage == stage.name]
        explained = [item for item in excused if item.stage == stage.name]
        rows.append(runner._stage_report(stage, runs, found, explained))
    # the wikis whose update refused the merged tree, and those one update
    # left with something to do, on the reference side
    refused = []
    unconverged = []
    for stage, run in zip(first.stages[1:], first.runs[1:]):
        _, updated, checked = (record.outcome.code for record in run.records)
        if updated == 2:
            refused.append(stage.name)
        elif checked != 0:
            unconverged.append(stage.name)
    return {
        'commit': merge.commit,
        'parents': len(merge.parents),
        'driver_calls': first.calls,
        'unmerged': len(first.unmerged),
        'refused': refused,
        'unconverged': unconverged,
        'seconds': {done.runs[0].name: done.seconds for done in passes},
        'cpu': {done.runs[0].name: done.cpu for done in passes},
        'stages': rows,
        'differences': [dataclasses.asdict(item) for item in differences],
        'excused': [dataclasses.asdict(item) for item in excused],
    }
