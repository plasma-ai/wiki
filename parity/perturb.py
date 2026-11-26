"""Functions for perturbing a tree.

A converged wiki gives the commands little to do: ``update`` writes almost
nothing and ``lint`` reports almost nothing. A perturbation changes a tree by
a seed, the way hands, tools and merges change one, so that the read-only
commands have something to report and ``update`` something to take in. It
runs no engine: an operator edits files, and what the engines make of the
edits is what a run compares.

:func:`apply` runs every operator on one tree. Two more trees come from the
one it leaves: :func:`unindex` deletes every index, and :func:`conflict`
plants merge conflict markers, which ``update`` refuses. One seed, one tree
and one path for the wiki to stand at give one perturbation, byte for byte.
"""

from __future__ import annotations

import dataclasses
import functools
import json
import os
import pathlib
import posixpath
import random
import subprocess
import time
import tomllib
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Optional

from . import fixtures, matrix, runner, snapshot
from .fixtures import Family
from .kinds import FAMILIES, PERTURBATION
from .matrix import _FAILS, Stage, _case, _named
from .replay import Finding
from .runner import _SLOT, _TREE, WIKI_SETTINGS, Corpus, Plan
from .side import Side

__all__ = []

#: the committed file that holds the seeds every pull request runs
PATH = pathlib.Path(__file__).parent / 'seeds.toml'
#: the word every page of the perturbation's own holds, and its paragraphs
WORD = 'perturbword'
#: the tag the perturbation gives the pages it tags
TAG = 'perturbtag'
#: the tree every operator without a stage changes, and the stage that runs
#: on it in a loop
EDITED = 'edited'
#: the two trees a staged operator makes of that one, and their stages
UNINDEXED = 'unindexed'
CONFLICTED = 'conflicted'
#: the example wiki of the repository, as a tree of the loop
EXAMPLE = 'hello'

# how often an operator applies at most: the seed draws the count
_MOST = 2
# the two modification times of a file the perturbation writes, in seconds
# since the epoch: long before any stamp a wiki holds (2001-09-09), so the
# file reads as stamped after its last edit, and long after (2033-05-18), so
# it reads as edited since
_EARLY = 1_000_000_000
_LATE = 2_000_000_000
# what the names the perturbation makes start with: lowercase ASCII letters,
# with a number behind them, pass the default naming policy and one that asks
# for identifiers
_NAME = 'perturbed'
# a character no naming policy lets a name hold: it is link grammar
_DENIED = '['
# the characters a link target cannot hold: they end it or split it
_GRAMMAR = frozenset('[]|#')
# the names of the files the excluded and gitignored rows point at, and the
# patterns that name them
_EXCLUDED = 'perturbedexcluded'
_IGNORED = 'perturbedignored'
# how long a desc must be before the Python engine notes its length
_LONG = 600
_INDEX = '_index.md'
_FENCE = '---'
_DELIMITER = '***'
# the lines a merge leaves around a conflict
_CONFLICT = (
    '<<<<<<< ours',
    'A line of ours.',
    '=======',
    'A line of theirs.',
    '>>>>>>> theirs',
)


@dataclasses.dataclass(frozen=True)
class Operator:
    """One kind of change a perturbation makes.

    Attributes:
        name: Name of the operator.
        change: Makes the change once on a tree, drawing where from the
            generator it is handed. ``None`` for an operator with a stage:
            :func:`unindex` and :func:`conflict` make its change, on the
            tree of that stage.
        notices: The notice kinds the change may put into the tree.
        issues: The issue kinds the change may put into the tree.
        stage: Name of the stage whose tree takes the change. Empty for a
            change that every tree of the perturbation holds.
        repository: Whether the change needs a git repository. A tree with
            none is left without it.

    """

    name: str
    change: Optional[Callable[[_Tree, random.Random], None]] = None
    notices: tuple[str, ...] = ()
    issues: tuple[str, ...] = ()
    stage: str = ''
    repository: bool = False


@dataclasses.dataclass(frozen=True)
class Perturbation:
    """What one perturbation did to a tree.

    Attributes:
        seed: The seed.
        repository: Whether the tree is a git repository.
        applied: The paths each operator changed, relative to the wiki
            root, by the operator's name. An operator the tree cannot take
            has none.
        expected: The kinds the changes put into each tree, by the name of
            the tree and by family: :data:`EDITED` for the tree the
            perturbation leaves, and the name of its stage for a tree a
            staged operator makes of that one.
        page: A page the perturbation edited, for ``read``.
        tagged: A body word and a tag of one page the perturbation tagged.
        conflicts: The files :func:`conflict` plants its markers in.

    """

    seed: int
    repository: bool
    applied: Mapping[str, tuple[str, ...]]
    expected: Mapping[str, Mapping[str, tuple[str, ...]]]
    page: str
    tagged: tuple[str, ...]
    conflicts: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class Trial:
    """One tree under one seed, as a run found it.

    Attributes:
        label: Label of the tree.
        seed: The seed.
        perturbation: What the perturbation did.
        result: What the run of the stages found.
        findings: The kinds a change put into a tree that the tree does
            not show.
        shown: What the tap printed for each tree of the perturbation, by
            the tree's name.
        failed: Whether the sides differ or a finding stands. Between two
            builds of v1 a command the registry excuses fails too.
        seconds: How long the trial took.

    """

    label: str
    seed: int
    perturbation: Perturbation
    result: runner.Result
    findings: tuple[Finding, ...]
    shown: Mapping[str, Any]
    failed: bool
    seconds: float


def run(
    corpus: Corpus,
    seed: int,
    *,
    sides: Sequence[Side],
    work: pathlib.Path,
    now: str,
    loop: bool,
    second: bool,
) -> Trial:
    """Perturb ``corpus`` under ``seed``, run its stages and hold it to its kinds.

    Args:
        corpus: The wiki to perturb.
        seed: The seed.
        sides: The sides; the first is a v1 side, the reference the others
            are compared against, and its tap names the kinds.
        work: Folder the trial makes ``<label>/<seed>`` in, for its
            snapshot and its clones.
        now: RFC 3339 instant for ``WIKI_FREEZE_TIME``.
        loop: Whether the trial is one of a loop. Outside a loop the
            snapshot of a trial that failed stays in its folder: the
            checkout it was taken from may move on.
        second: Whether a side is a second engine, so that what the
            registry excuses passes.

    Returns:
        What the trial found.

    Raises:
        ValueError: If :func:`parity.runner.validate` refuses the corpus or
            the folder.

    """
    started = time.monotonic()
    folder = work / corpus.label / f'{seed}'
    plan, done = prepare(corpus, work=folder, seed=seed, loop=loop)
    result = runner.run(plan, sides, now=now)
    reference, *_ = sides
    findings, shown = check(plan, done, reference, now=now)
    failed = bool(result.differences or findings or (result.excused and not second))
    if loop or not failed:
        runner.discard(plan)
    return Trial(
        label=corpus.label,
        seed=seed,
        perturbation=done,
        result=result,
        findings=tuple(findings),
        shown=shown,
        failed=failed,
        seconds=round(time.monotonic() - started, 1),
    )


def apply(
    checkout: pathlib.Path,
    *,
    root: str,
    seed: int,
    stands: pathlib.Path,
) -> Perturbation:
    """Perturb the wiki at ``root`` below ``checkout``, in place.

    Every operator applies at least once, and the seed draws where and how
    often. Each operator draws from a generator of its own, so a new
    operator moves no other's draws.

    Args:
        checkout: The checkout to change: a clone, never a live tree.
        root: The wiki root, relative to the checkout.
        seed: The seed.
        stands: The path the wiki root has when the commands run. A link
            through an absolute path names it.

    Returns:
        What the perturbation did.

    """
    tree = _Tree(checkout, root=root, stands=stands)
    for operator in operators():
        if operator.change is None:
            continue
        tree.operator = operator.name
        tree.applied[operator.name] = []
        if operator.repository and not tree.repository:
            continue
        generator = random.Random(f'{seed}:{operator.name}')
        for _ in range(generator.randint(1, _MOST)):
            operator.change(tree, generator)
    # choose what the stages of a run name, and where the markers go
    generator = random.Random(f'{seed}:stages')
    conflicts = (tree.any_page(generator), tree.any_index(generator))
    staged = {
        operator.stage: {'notices': operator.notices, 'issues': operator.issues}
        for operator in operators()
        if operator.stage
    }
    expected = {
        EDITED: {family: tuple(sorted(tree.expected[family])) for family in FAMILIES},
        **staged,
    }
    return Perturbation(
        seed=seed,
        repository=tree.repository,
        applied={name: tuple(paths) for name, paths in tree.applied.items()},
        expected=expected,
        page=tree.edited,
        tagged=tree.tagged,
        conflicts=conflicts,
    )


def prepare(
    corpus: Corpus,
    *,
    work: pathlib.Path,
    seed: int,
    loop: bool,
) -> tuple[Plan, Perturbation]:
    """Snapshot ``corpus`` into the new folder ``work`` and perturb the snapshot.

    The stages of the plan are those of the matrix, on the tree the
    perturbation leaves, and then the perturbation's own (:func:`stages`).
    In a loop the short stage of the perturbation stands in for the matrix.
    A stage of the corpus's own names pages the perturbation may have
    moved, so none runs.

    Args:
        corpus: The wiki to perturb.
        work: New folder for the snapshot and the clones.
        seed: The seed, of the perturbation and of the matrix sample.
        loop: Whether the run is one of a loop.

    Returns:
        The plan to hand to :func:`parity.runner.run`, and what the
        perturbation did.

    Raises:
        ValueError: If :func:`parity.runner.validate` refuses the corpus or
            the folder.

    """
    done = []

    def _change(checkout: pathlib.Path) -> None:
        """Perturb the snapshot, for the wiki as it stands in a slot."""
        # an engine reads an absolute link against the root's resolved path
        stands = work.resolve() / _SLOT / _TREE / corpus.root
        done.append(apply(checkout, root=corpus.root, seed=seed, stands=stands))

    bare = dataclasses.replace(corpus, stages=(), alone=loop)
    plan = runner.prepare(bare, work=work, seed=seed, change=_change)
    (perturbation,) = done
    own = stages(perturbation, root=corpus.root, loop=loop)
    return dataclasses.replace(plan, stages=(*plan.stages, *own)), perturbation


def stages(done: Perturbation, *, root: str, loop: bool) -> tuple[Stage, ...]:
    """Return the perturbation's own stages for one perturbed wiki.

    ``unindexed`` runs on the tree with every index deleted, where
    ``update`` builds each one again. ``conflicted`` runs on the tree with
    conflict markers, where ``update`` refuses. In a loop ``edited`` runs
    first, on the tree as the perturbation leaves it: the read-only
    commands, each once, then ``update`` and what it leaves.
    """
    word, tag = done.tagged
    edited = [
        _case(['lint', '--json']),
        _case(['lint']),
        _case(['update', '--check', '--full']),
        _case(['map']),
        _case(['search', WORD, '--json']),
        _case(['search', word, '--tag', tag]),
        _case(['match', WORD, '--lines']),
        _case(['read', done.page]),
        _case(['update', '--full'], writes=True),
        _case(['update', '--check']),
        _case(['lint', '--json']),
    ]
    unindexed = [
        _case(['lint', '--json']),
        _case(['update', '--check', '--full']),
        _case(['update', '--full'], writes=True),
        _case(['update', '--check']),
        _case(['lint', '--json']),
        _case(['map']),
    ]
    conflicted = [
        _case(['lint', '--json']),
        _case(['update', '--check'], codes=_FAILS),
        _case(['update'], codes=_FAILS),
        _case(['map']),
    ]
    result = [
        Stage(
            name=UNINDEXED,
            cases=_named(unindexed),
            change=functools.partial(unindex, root=root),
        ),
        Stage(
            name=CONFLICTED,
            cases=_named(conflicted),
            change=functools.partial(conflict, root=root, paths=done.conflicts),
        ),
    ]
    if loop:
        result.insert(0, Stage(name=EDITED, cases=_named(edited)))
    return tuple(result)


def check(
    plan: Plan,
    done: Perturbation,
    reference: Side,
    *,
    now: str,
) -> tuple[list[Finding], dict[str, Any]]:
    """Hold a perturbation to the kinds its trees show v1.

    The tap of ``reference`` runs once on each tree of the perturbation:
    the one it leaves, and the one each stage with a change makes of it. A
    kind a change put into a tree and the tap does not meet there is a
    finding: the operator did not do what it names.

    Args:
        plan: The plan of the perturbed wiki, from :func:`prepare`.
        done: What the perturbation did.
        reference: A v1 side, whose tap names the kinds.
        now: RFC 3339 instant for ``WIKI_FREEZE_TIME``.

    Returns:
        The findings, and what the tap printed for each tree, by the
        tree's name.

    """
    trees: dict[str, Optional[Stage]] = {EDITED: None}
    for stage in plan.stages:
        if stage.change is not None:
            trees[stage.name] = stage
    findings = []
    shown = {}
    for name, stage in trees.items():
        shown[name] = runner.tap(plan, reference, now=now, stage=stage)
        for family in FAMILIES:
            label = family.removesuffix('s')
            for kind in done.expected[name][family]:
                if kind not in shown[name][family]:
                    subject = f'{plan.corpus.label} seed {done.seed}: {label} {kind}'
                    detail = f'the tree {name} does not show it'
                    findings.append(Finding('kind', subject, detail))
    return findings, shown


def targets() -> dict[str, frozenset[str]]:
    """Return the kinds the operators name, by family."""
    return {
        family: frozenset(
            kind for operator in operators() for kind in getattr(operator, family)
        )
        for family in FAMILIES
    }


def validate(homes: Mapping[str, Mapping[str, str]]) -> None:
    """Hold the kind list to the operators.

    Args:
        homes: The kind list, from :func:`parity.kinds.load`.

    Raises:
        ValueError: If a kind has the perturbation for a home and no
            operator names it, or an operator names a kind whose home is a
            test: a kind has one home.

    """
    named = targets()
    for family in FAMILIES:
        label = family.removesuffix('s')
        listed = {
            kind for kind, home in homes.get(family, {}).items() if home == PERTURBATION
        }
        for kind in sorted(listed - named[family]):
            raise ValueError(
                f'The kind list gives the {label} kind {kind} the home'
                f' {PERTURBATION!r}, and no operator names it.'
            )
        for kind in sorted(named[family] - listed):
            raise ValueError(
                f'An operator names the {label} kind {kind}, and the kind list'
                f' gives it another home than {PERTURBATION!r}.'
            )


def unshown(
    homes: Mapping[str, Mapping[str, str]],
    shown: Sequence[Mapping[str, Any]],
) -> list[Finding]:
    """Return a finding for each kind of the perturbation that no tree of a run shows.

    Args:
        homes: The kind list, from :func:`parity.kinds.load`.
        shown: What the tap printed for each tree of the run.

    """
    findings = []
    for family in FAMILIES:
        label = family.removesuffix('s')
        met = {kind for tree in shown for kind in tree[family]}
        for kind, home in sorted(homes.get(family, {}).items()):
            if (home == PERTURBATION) and (kind not in met):
                detail = 'no tree of the run shows it'
                findings.append(Finding('kind', f'{label} {kind}', detail))
    return findings


def load_seeds(path: Optional[pathlib.Path] = None) -> tuple[int, ...]:
    """Return the seeds of the TOML file at ``path``.

    Raises:
        ValueError: If the file holds another key than ``seeds``, or if
            ``seeds`` is not a list of whole numbers from zero up, each
            once, with at least one.

    """
    path = path or PATH
    data = tomllib.loads(path.read_text(encoding='utf-8'))
    unknown = sorted(set(data) - {'seeds'})
    if unknown:
        raise ValueError(f'{path.name} has the unknown key {unknown[0]!r}.')
    seeds = data.get('seeds')
    whole = isinstance(seeds, list) and all(
        (type(seed) is int) and (seed >= 0) for seed in seeds
    )
    if (not whole) or (not seeds) or (len(set(seeds)) != len(seeds)):
        raise ValueError(
            f'{path.name}: seeds must be a list of whole numbers from zero up,'
            f' each once, got {seeds!r}.'
        )
    return tuple(seeds)


def trees(
    folder: pathlib.Path,
    *,
    names: Optional[Sequence[str]] = None,
    every: bool = False,
) -> tuple[list[Corpus], list[str]]:
    """Write the small trees of the loop below the new folder ``folder``.

    They are the repository's example wiki, as a committed checkout, and
    the fixture families ``update`` takes: a perturbation is built for a
    wiki that ``update`` takes, so a family that runs alone is no tree of
    the loop.

    Args:
        folder: New folder for the checkouts.
        names: Names of the trees to write. ``None`` writes every tree the
            file system at ``folder`` can hold.
        every: Whether to refuse a file system that cannot hold every
            tree.

    Returns:
        One corpus per tree written, and the names of the trees left out.

    Raises:
        ValueError: If :func:`parity.fixtures.validate` refuses ``folder``,
            if no tree has one of the names, if a name is given twice, or
            if the file system cannot hold a tree that is named or that
            ``every`` asks for.

    """
    fixtures.validate(folder)
    known = [
        family.name
        for family in fixtures.families()
        if not (family.entry or family.alone)
    ]
    given = list(names or ())
    unknown = [name for name in given if name not in (EXAMPLE, *known)]
    if unknown:
        raise ValueError(
            f'No tree of the loop is named {unknown!r}; the trees are'
            f' {[EXAMPLE, *known]!r}.'
        )
    twice = sorted({name for name in given if given.count(name) > 1})
    if twice:
        raise ValueError(f'Trees of the loop are named twice: {twice!r}.')
    # ask the file system which families it can hold, unless they are named
    chosen = [name for name in given if name != EXAMPLE]
    left = []
    if names is None:
        held = next(parent for parent in folder.resolve().parents if parent.is_dir())
        lacks = fixtures.lacking(held)
        needs = {family.name: family.needs for family in fixtures.families()}
        chosen = [name for name in known if every or not (needs[name] & lacks)]
        left = [name for name in known if name not in chosen]
    corpora = [
        dataclasses.replace(corpus, stages=())
        for corpus in fixtures.generate(folder, names=chosen)
    ]
    if (names is None) or (EXAMPLE in given):
        fixtures._write(Family(name=EXAMPLE, tree=_example), folder / EXAMPLE)
        example = Corpus(label=EXAMPLE, checkout=folder / EXAMPLE, root=EXAMPLE)
        corpora.insert(0, example)
    return corpora, left


def carve(corpus: Corpus, folder: pathlib.Path) -> Corpus:
    """Copy the wiki of ``corpus`` into a small repository of its own.

    A loop snapshots its tree once for every seed, and a wiki may lie in a
    checkout many times its size. The new checkout ``<folder>/<label>``
    holds the wiki at the path it has in its checkout and the ``.gitignore``
    from the top of that checkout, committed as they stand. Nothing else of
    the checkout comes along: its history, its other folders, and what git
    holds of the wiki's own files.

    Args:
        corpus: The wiki to copy; its checkout is read and never written.
        folder: Folder for the new checkout.

    Returns:
        The corpus of the new checkout.

    Raises:
        ValueError: If the wiki root of the corpus is not a folder.
        RuntimeError: If the copy or the commit fails.

    """
    source = corpus.checkout / corpus.root
    if os.path.islink(source) or not os.path.isdir(source):
        raise ValueError(f'Corpus {corpus.label!r}: {source} is not a folder.')
    checkout = folder / corpus.label
    root = checkout / corpus.root
    root.parent.mkdir(parents=True)
    snapshot.clone(source, root)
    ignore = corpus.checkout / '.gitignore'
    if ignore.is_file():
        snapshot.clone(ignore, checkout / '.gitignore')
    fixtures._commit(checkout, f'the wiki of {corpus.label}')
    return Corpus(label=corpus.label, checkout=checkout, root=corpus.root)


def unindex(checkout: pathlib.Path, *, root: str) -> None:
    """Delete every index of the wiki at ``root`` below ``checkout``."""
    for folder, folders, names in os.walk(checkout / root):
        folders[:] = [name for name in folders if not name.startswith('.')]
        if _INDEX in names:
            os.remove(os.path.join(folder, _INDEX))


def conflict(checkout: pathlib.Path, *, root: str, paths: Sequence[str]) -> None:
    """Plant merge conflict markers at the end of each of ``paths``.

    Args:
        checkout: The checkout to change.
        root: The wiki root, relative to the checkout.
        paths: The files, relative to the wiki root.

    """
    for relpath in paths:
        path = checkout / root / relpath
        text = _read(path)
        lines = '\n'.join(_CONFLICT)
        _write(path, f'{_ended(text)}\n{lines}\n', late=True)


def operators() -> tuple[Operator, ...]:
    """Return the operators, in the order they apply.

    A folder is renamed before anything else is drawn from the tree. An
    index is cut short next, so that nothing is added to its folder. The
    operators that need a page or an index as the engine left it draw before
    those that take any.
    """
    return (
        Operator(
            name='rename-folder',
            change=_rename_folder,
            notices=('link_add',),
            issues=('requires_update',),
        ),
        Operator(
            name='truncate-index',
            change=_truncate_index,
            notices=('index_truncated',),
            issues=('truncated_index',),
        ),
        Operator(
            name='dirty-edit',
            change=_dirty_edit,
            notices=('restamp',),
            issues=('requires_update',),
        ),
        Operator(
            name='strip-frontmatter',
            change=_strip_frontmatter,
            notices=('page_adopt',),
            issues=('bare_page',),
        ),
        Operator(
            name='blank-stamps', change=_blank_stamps, issues=('requires_update',)
        ),
        Operator(
            name='shuffle-keys', change=_shuffle_keys, issues=('requires_update',)
        ),
        Operator(name='add-tags', change=_add_tags),
        Operator(name='missing-desc', change=_missing_desc, notices=('desc_missing',)),
        Operator(name='long-desc', change=_long_desc, notices=('desc_long',)),
        Operator(
            name='bare-file',
            change=_bare_file,
            notices=('page_adopt', 'link_add'),
            issues=('bare_page',),
        ),
        Operator(name='crlf-file', change=_crlf_file, notices=('crlf_notice',)),
        Operator(name='other-file', change=_other_file, notices=('link_add',)),
        Operator(name='stale-link', change=_stale_link, notices=('link_stale',)),
        Operator(
            name='directory-link', change=_directory_link, issues=('directory_link',)
        ),
        Operator(
            name='absolute-link', change=_absolute_link, issues=('absolute_link',)
        ),
        Operator(name='outside-link', change=_outside_link, issues=('outside_link',)),
        Operator(
            name='region-marker', change=_region_marker, issues=('region_marker',)
        ),
        Operator(
            name='invalid-page',
            change=_invalid_page,
            notices=('name_skip',),
            issues=('invalid_page_name',),
        ),
        Operator(
            name='invalid-folder',
            change=_invalid_folder,
            notices=('name_skip',),
            issues=('invalid_folder_name',),
        ),
        Operator(
            name='drift-row',
            change=_drift_row,
            notices=('desc_overwrite',),
            issues=('requires_update',),
        ),
        Operator(
            name='drop-row',
            change=_drop_row,
            notices=('link_add',),
            issues=('requires_update',),
        ),
        Operator(
            name='broken-row',
            change=_broken_row,
            notices=('link_prune',),
            issues=('broken_link',),
        ),
        Operator(
            name='symlink-row',
            change=_symlink_row,
            notices=('symlink_skip',),
            issues=('symlink_link_target',),
        ),
        Operator(
            name='excluded-row',
            change=_excluded_row,
            notices=('exclude_skip',),
            issues=('excluded_link_target',),
        ),
        Operator(
            name='gitignored-row',
            change=_gitignored_row,
            notices=('gitignore_skip',),
            issues=('gitignored_link_target',),
            repository=True,
        ),
        Operator(
            name='delete-indexes',
            notices=('index_create',),
            issues=('missing_index',),
            stage=UNINDEXED,
        ),
        Operator(
            name='conflict-markers',
            issues=('conflict_markers',),
            stage=CONFLICTED,
        ),
    )


# ------ helper functions


class _Tree:
    """A wiki under perturbation: what it holds, and what was done to it.

    The pages, indexes and folders it deals are those the engine manages,
    as far as the files say: a page with a closed block that carries a
    ``name``, an index with such a block, a delimiter and a row, and a
    folder whose index stands, each listed in the index above it, and that
    folder in the one above, up to the root. A converged wiki lists what
    it manages, and gives a file it excludes no row. A tree that runs out
    of them gets new ones, in the form the engine writes with its default
    settings.
    """

    def __init__(
        self: _Tree,
        checkout: pathlib.Path,
        *,
        root: str,
        stands: pathlib.Path,
    ) -> None:
        """Initialize the tree.

        Args:
            checkout: The checkout to change.
            root: The wiki root, relative to the checkout.
            stands: The path the wiki root has when the commands run.

        """
        self.checkout = checkout
        self.root = checkout / root
        self.stands = stands
        self.repository = os.path.isdir(checkout / '.git')
        # the name of the operator under way, and what each one changed
        self.operator = ''
        self.applied: dict[str, list[str]] = {}
        self.expected: dict[str, set[str]] = {family: set() for family in FAMILIES}
        # what the stages of a run name
        self.edited = ''
        self.tagged: tuple[str, ...] = ()
        # how many names the perturbation has made, what it made, the folders
        # it renamed (the new path and the old), and the files it left with
        # the late time
        self._made = 0
        self._own: set[str] = set()
        self._renamed: list[tuple[str, str]] = []
        self._late: set[str] = set()
        # the targets of the rows each index held when it was first read
        self._listings: dict[str, frozenset[str]] = {}
        # the indexed folders, the pages and the indexes, each checked when
        # it is drawn; those an operator has taken; and the files git tracks
        self._folders: list[str] = []
        self._pages: list[str] = []
        self._indexes: list[str] = []
        self._taken: set[str] = set()
        self._tracked = self._list_tracked()
        self._scan()

    def name(self: _Tree) -> str:
        """Return a name the perturbation has not made yet."""
        self._made += 1
        return f'{_NAME}{self._made}'

    def folder(self: _Tree, generator: random.Random, *, below: bool = False) -> str:
        """Return a folder whose index stands, relative to the wiki root.

        An index that was cut short takes no row, so what an operator adds
        to its folder would show less than the operator names.

        Args:
            generator: The generator to draw with.
            below: Whether the folder must lie below the root and spell as
                a link target.

        """
        folders = list(self._folders)
        if below:
            folders = [folder for folder in folders if folder and _spells(folder)]
        folder = self._draw(generator, folders, self._is_folder)
        if folder is None:
            folder = self.grow(generator)
        return folder

    def page(self: _Tree, generator: random.Random, *, held: bool = False) -> str:
        """Take a page that no operator has taken, and return it.

        Args:
            generator: The generator to draw with.
            held: Whether to take a page that git holds with the stamp it
                has now, where one is left.

        """
        free = [page for page in self._pages if page not in self._taken]
        page = None
        if held:
            tracked = [page for page in free if page in self._tracked]
            page = self._draw(generator, tracked, self.held)
        if page is None:
            page = self._draw(generator, free, self._is_page)
        if page is None:
            page = self._add_page(generator)
        self._taken.add(page)
        return page

    def any_page(self: _Tree, generator: random.Random) -> str:
        """Return a page whose path spells as a link target, taken or not."""
        pages = [page for page in self._pages if _spells(page)]
        page = self._draw(generator, pages, self._is_page)
        if page is None:
            page = self._add_page(generator)
        return page

    def index(
        self: _Tree,
        generator: random.Random,
        *,
        rows: Optional[Callable[[Sequence[str]], Sequence[int]]] = None,
        ends: bool = False,
    ) -> str:
        """Take an index that no operator has taken, and return it.

        Args:
            generator: The generator to draw with.
            rows: Returns where the rows stand, among an index's lines,
                that the operator can change. An index with none is passed
                over. A new index has one: its row is for a page that
                stands and has a desc.
            ends: Whether the operator ends the index: it gets no row
                afterwards.

        """

        def takes(index: str) -> bool:
            """Return whether the engine manages the index and the operator can change it."""
            if not self._is_index(index):
                return False
            return (rows is None) or bool(rows(self.read(index).split('\n')))

        free = [index for index in self._indexes if index not in self._taken]
        index = self._draw(generator, free, takes)
        if index is None:
            index = posixpath.join(self.grow(generator), _INDEX)
        self._taken.add(index)
        if ends:
            self._indexes.remove(index)
        return index

    def any_index(self: _Tree, generator: random.Random) -> str:
        """Return an index whose path spells as a link target, taken or not."""
        indexes = [index for index in self._indexes if _spells(index)]
        index = self._draw(generator, indexes, self._is_index)
        if index is None:
            index = posixpath.join(self.grow(generator), _INDEX)
        return index

    def grow(self: _Tree, generator: random.Random) -> str:
        """Write a new folder below the root, with an index and a page, and return it."""
        folder = self.name()
        index = posixpath.join(folder, _INDEX)
        page = posixpath.join(folder, self.name())
        row = f'[[{page}|{posixpath.basename(page)}]]: {_desc("page")}'
        indexed = fixtures._index(folder, _desc('folder'), [row]).decode('utf-8')
        text = fixtures._page(page, _desc('page'), _body('page')).decode('utf-8')
        self.write(index, indexed, generator=generator, late=False)
        self.write(f'{page}.md', text, generator=generator, late=False)
        self._folders.append(folder)
        self._indexes.append(index)
        self._pages.append(f'{page}.md')
        self._own.update([folder, index, f'{page}.md'])
        return folder

    def rename(self: _Tree, generator: random.Random) -> str:
        """Rename a folder below the root, with all it holds, and return its new path."""
        folders = [folder for folder in self._folders if folder]
        folder = self._draw(generator, folders, self._is_folder)
        if folder is None:
            folder = self.grow(generator)
        target = f'{folder}moved'
        while os.path.lexists(self.root / target):
            target += 'moved'
        os.rename(self.root / folder, self.root / target)
        # every path the tree lists below the folder moved with it
        for listed in (self._folders, self._pages, self._indexes):
            listed[:] = [_moved(relpath, folder, target) for relpath in listed]
        self._own = {_moved(relpath, folder, target) for relpath in self._own}
        self._listings = {
            _moved(index, folder, target): rows
            for index, rows in self._listings.items()
        }
        self._renamed.append((target, folder))
        return target

    def held(self: _Tree, relpath: str) -> bool:
        """Return whether ``relpath`` is a page that git holds with the stamp it has now.

        The engine restamps a page that differs from the one ``HEAD`` holds
        while its ``updated`` stamp is still that one's. A checkout may
        hold a page that was edited and stamped and not committed.
        """
        if (relpath not in self._tracked) or not self._is_page(relpath):
            return False
        result = subprocess.run(
            ['git', '-C', f'{self.root}', 'show', f'HEAD:./{relpath}'],
            capture_output=True,
        )
        if result.returncode != 0:
            return False
        stamps = []
        for text in (os.fsdecode(result.stdout), self.read(relpath)):
            fields, _ = _split(text)
            stamps.append([field for field in fields if _key(field) == 'updated'])
        return bool(stamps[0]) and (stamps[0] == stamps[1])

    def read(self: _Tree, relpath: str) -> str:
        """Return the text of the file at ``relpath``."""
        return _read(self.root / relpath)

    def write(
        self: _Tree,
        relpath: str,
        text: str,
        *,
        generator: random.Random,
        late: Optional[bool] = None,
    ) -> None:
        """Write ``text`` to the file at ``relpath`` and note the change.

        Args:
            relpath: The file, relative to the wiki root.
            text: The whole text of the file.
            generator: The generator to draw with.
            late: Whether the file reads as edited since any stamp it
                holds. ``None`` lets the seed draw it. A file that an
                earlier change left late stays late.

        """
        if late is None:
            late = generator.random() < 0.5
        if late or (relpath in self._late):
            self._late.add(relpath)
            late = True
        # an index keeps listing what it listed before its first change
        if posixpath.basename(relpath) == _INDEX:
            self._listing(relpath)
        path = self.root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        _write(path, text, late=late)
        self.note(relpath)

    def note(self: _Tree, relpath: str) -> None:
        """Note that the operator under way changed the path ``relpath``."""
        self.applied[self.operator].append(relpath)

    def expect(
        self: _Tree,
        *,
        notices: Sequence[str] = (),
        issues: Sequence[str] = (),
    ) -> None:
        """Note the kinds a change put into the tree."""
        self.expected['notices'].update(notices)
        self.expected['issues'].update(issues)

    # ------ helper methods

    def _scan(self: _Tree) -> None:
        """List the indexed folders of the tree, their pages and their indexes.

        Dot folders, symlinks and a wiki declared inside the wiki are left
        alone: no engine manages what lies there as part of this wiki. So
        is a name that opens with a dash, which a command line would read
        as an option.
        """
        pending = ['']
        while pending:
            folder = pending.pop()
            names = sorted(os.listdir(self.root / folder))
            for name in reversed(names):
                path = self.root / folder / name
                plain = os.path.isdir(path) and not os.path.islink(path)
                declared = os.path.isfile(path / WIKI_SETTINGS)
                if plain and not (name.startswith(('.', '-')) or declared):
                    pending.append(posixpath.join(folder, name))
            if _INDEX not in names:
                continue
            self._folders.append(folder)
            self._indexes.append(posixpath.join(folder, _INDEX))
            for name in names:
                path = self.root / folder / name
                regular = os.path.isfile(path) and not os.path.islink(path)
                page = name.endswith('.md') and (name != _INDEX)
                if page and regular and not name.startswith('-'):
                    self._pages.append(posixpath.join(folder, name))

    def _list_tracked(self: _Tree) -> frozenset[str]:
        """Return the files of the wiki that git tracks, relative to its root."""
        if not self.repository:
            return frozenset()
        result = subprocess.run(
            ['git', '-C', f'{self.root}', 'ls-files', '-z'],
            capture_output=True,
            check=True,
        )
        names = result.stdout.split(b'\0')
        return frozenset(os.fsdecode(name) for name in names if name)

    def _draw(
        self: _Tree,
        generator: random.Random,
        candidates: list[str],
        takes: Callable[[str], bool],
    ) -> Optional[str]:
        """Draw one of ``candidates`` that passes the test ``takes``, if one does.

        A candidate is read when it is drawn, so a tree is not read whole
        to find the few files a perturbation changes.
        """
        while candidates:
            chosen = generator.choice(candidates)
            if takes(chosen):
                return chosen
            candidates.remove(chosen)
        return None

    def _is_page(self: _Tree, relpath: str) -> bool:
        """Return whether ``relpath`` is a page the engine manages."""
        if relpath in self._own:
            return True
        fields, _ = _split(self.read(relpath))
        named = any(_key(field) == 'name' for field in fields)
        return named and self._listed(relpath)

    def _is_index(self: _Tree, relpath: str) -> bool:
        """Return whether ``relpath`` is the index of a folder the engine manages, with a row."""
        if relpath in self._own:
            return True
        text = self.read(relpath)
        fields, _ = _split(text)
        named = any(_key(field) == 'name' for field in fields)
        rowed = bool(_rows(text.split('\n')))
        return named and rowed and self._listed(posixpath.dirname(relpath))

    def _is_folder(self: _Tree, relpath: str) -> bool:
        """Return whether ``relpath`` is a folder the engine manages, whose index has a closed block."""
        fields, _ = _split(self.read(posixpath.join(relpath, _INDEX)))
        return bool(fields) and ((relpath in self._own) or self._listed(relpath))

    def _listed(self: _Tree, relpath: str) -> bool:
        """Return whether the indexes list the page or folder ``relpath``, up to the root.

        The index of its folder held its row before the perturbation
        changed that index, the index above held that folder's, and so on.
        A row spells a path as it was before the perturbation renamed a
        folder above it.
        """
        entry = relpath
        while entry:
            folder = posixpath.dirname(entry)
            was = entry
            for target, source in reversed(self._renamed):
                was = _moved(was, target, source)
            # a row names a page by its stem and a folder by its index
            row = posixpath.join(was, '_index')
            if entry == relpath and relpath.endswith('.md'):
                row = was.removesuffix('.md')
            if row not in self._listing(posixpath.join(folder, _INDEX)):
                return False
            entry = folder
        return True

    def _listing(self: _Tree, index: str) -> frozenset[str]:
        """Return the targets of the rows ``index`` held when it was first read.

        A page stays one the engine manages when an operator cuts the index
        of its folder short, or takes its row out.
        """
        if index not in self._listings:
            lines = []
            if (self.root / index).is_file():
                lines = self.read(index).split('\n')
            targets = frozenset(_target(lines[at]) for at in _rows(lines))
            self._listings[index] = targets
        return self._listings[index]

    def _add_page(self: _Tree, generator: random.Random) -> str:
        """Write a new page into a folder whose index stands, and return it."""
        name = posixpath.join(self.folder(generator), self.name())
        text = fixtures._page(name, _desc('page'), _body('page')).decode('utf-8')
        self.write(f'{name}.md', text, generator=generator, late=False)
        self._pages.append(f'{name}.md')
        self._own.add(f'{name}.md')
        return f'{name}.md'


def _example() -> Mapping[bytes, bytes]:
    """Return the example wiki of the repository, by path below its checkout.

    It holds the files git tracks there: a command run in the example
    leaves a cache beside them.

    Raises:
        RuntimeError: If git cannot list the files.

    """
    source = pathlib.Path(__file__).parent.parent / 'examples' / EXAMPLE
    listed = snapshot._git(['ls-files', '-z'], cwd=source)
    tree = {}
    for relpath in sorted(filter(None, listed.split('\0'))):
        path = posixpath.join(EXAMPLE, relpath)
        tree[path.encode('utf-8')] = (source / relpath).read_bytes()
    return tree


def _rename_folder(tree: _Tree, generator: random.Random) -> None:
    """Rename one folder below the root, with all it holds.

    Every name below it is stale afterwards, and its row in the index
    above points at nothing.
    """
    tree.note(tree.rename(generator))
    tree.expect(notices=['link_add'], issues=['requires_update'])


def _dirty_edit(tree: _Tree, generator: random.Random) -> None:
    """Add a paragraph to a page and leave its stamp as it was.

    The first edit of a tree reads as made since the stamp; the seed draws
    the others.
    """
    page = tree.page(generator, held=True)
    late = (not tree.edited) or (generator.random() < 0.5)
    text = _ended(tree.read(page))
    tree.write(page, f'{text}\n{_body("paragraph")}\n', generator=generator, late=late)
    tree.edited = tree.edited or page
    if late and tree.held(page):
        tree.expect(notices=['restamp'], issues=['requires_update'])


def _strip_frontmatter(tree: _Tree, generator: random.Random) -> None:
    """Take the frontmatter block off a page."""
    page = tree.page(generator)
    _, rest = _split(tree.read(page))
    tree.write(page, rest.lstrip('\n'), generator=generator)
    tree.expect(notices=['page_adopt'], issues=['bare_page'])


def _blank_stamps(tree: _Tree, generator: random.Random) -> None:
    """Leave both stamps of a page with no value."""
    page = tree.page(generator)
    fields, rest = _split(tree.read(page))
    for key in ('created', 'updated'):
        fields = [[f'{key}:'] if _key(field) == key else field for field in fields]
    tree.write(page, _join(fields, rest), generator=generator)
    tree.expect(issues=['requires_update'])


def _shuffle_keys(tree: _Tree, generator: random.Random) -> None:
    """Put the fields of a page's block in another order."""
    page = tree.page(generator)
    fields, rest = _split(tree.read(page))
    # turn the fields by a drawn count: no draw leaves the order as it was
    turn = generator.randrange(1, len(fields)) if len(fields) > 1 else 0
    fields = fields[turn:] + fields[:turn]
    tree.write(page, _join(fields, rest), generator=generator)
    if turn:
        tree.expect(issues=['requires_update'])


def _add_tags(tree: _Tree, generator: random.Random) -> None:
    """Give a page tags, so a search by tag finds something."""
    page = tree.page(generator)
    fields, rest = _split(tree.read(page))
    extra = generator.choice(('alpha', 'beta', 'gamma'))
    tags = [f'tags: [{TAG}, {extra}]']
    kept = [field for field in fields if _key(field) != 'tags']
    # tags stand before the stamps, where the engine writes them
    at = len(kept)
    for number, field in enumerate(kept):
        if _key(field) == 'created':
            at = number
            break
    tree.write(page, _join([*kept[:at], tags, *kept[at:]], rest), generator=generator)
    if not tree.tagged:
        words = matrix._words(rest)
        tree.tagged = (generator.choice(words) if words else WORD, TAG)


def _missing_desc(tree: _Tree, generator: random.Random) -> None:
    """Put the placeholder in place of a page's desc."""
    page = tree.page(generator)
    fields, rest = _split(tree.read(page))
    fields = [['desc: ...'] if _key(field) == 'desc' else field for field in fields]
    tree.write(page, _join(fields, rest), generator=generator)
    tree.expect(notices=['desc_missing'])


def _long_desc(tree: _Tree, generator: random.Random) -> None:
    """Give a page a desc longer than the engine notes."""
    page = tree.page(generator)
    fields, rest = _split(tree.read(page))
    words = ' '.join(['long'] * (_LONG // len('long ')))
    desc = [f'desc: A {words} desc.']
    fields = [desc if _key(field) == 'desc' else field for field in fields]
    tree.write(page, _join(fields, rest), generator=generator)
    tree.expect(notices=['desc_long'])


def _bare_file(tree: _Tree, generator: random.Random) -> None:
    """Add a page with no block to an indexed folder."""
    folder = tree.folder(generator)
    relpath = posixpath.join(folder, f'{tree.name()}.md')
    tree.write(relpath, f'# A bare page\n\n{_body("bare page")}\n', generator=generator)
    tree.expect(notices=['page_adopt', 'link_add'], issues=['bare_page'])


def _crlf_file(tree: _Tree, generator: random.Random) -> None:
    """Add a page whose lines end in CR LF to an indexed folder."""
    folder = tree.folder(generator)
    name = posixpath.join(folder, tree.name())
    text = fixtures._page(name, _desc('page'), _body('page')).decode('utf-8')
    tree.write(f'{name}.md', text.replace('\n', '\r\n'), generator=generator)
    tree.expect(notices=['crlf_notice'])


def _other_file(tree: _Tree, generator: random.Random) -> None:
    """Add a file that is no page to an indexed folder."""
    folder = tree.folder(generator)
    relpath = posixpath.join(folder, f'{tree.name()}.txt')
    tree.write(relpath, f'{_body("file")}\n', generator=generator)
    tree.expect(notices=['link_add'])


def _stale_link(tree: _Tree, generator: random.Random) -> None:
    """Add a link to a page that does not exist to a page's body."""
    target = posixpath.join(f'{_NAME}missing', tree.name())
    _append(tree, generator, f'A stale link: [[{target}]].')
    tree.expect(notices=['link_stale'])


def _directory_link(tree: _Tree, generator: random.Random) -> None:
    """Add a link that names a folder, not its index, to a page's body."""
    folder = tree.folder(generator, below=True)
    _append(tree, generator, f'A directory link: [[{folder}]].')
    tree.expect(issues=['directory_link'])


def _absolute_link(tree: _Tree, generator: random.Random) -> None:
    """Add a link to a page by its absolute path to a page's body."""
    page = tree.any_page(generator)
    target = tree.stands / page.removesuffix('.md')
    _append(tree, generator, f'An absolute link: [[{target}]].')
    tree.expect(issues=['absolute_link'])


def _outside_link(tree: _Tree, generator: random.Random) -> None:
    """Add a link that leaves the wiki to a page's body."""
    target = f'../{_NAME}outside/{tree.name()}'
    _append(tree, generator, f'An outside link: [[{target}]].')
    tree.expect(issues=['outside_link'])


def _region_marker(tree: _Tree, generator: random.Random) -> None:
    """Add the end of a region that never started to a page's body."""
    _append(tree, generator, '<!-- end: no-lint -->')
    tree.expect(issues=['region_marker'])


def _invalid_page(tree: _Tree, generator: random.Random) -> None:
    """Add a page whose name no naming policy takes."""
    folder = tree.folder(generator)
    name = posixpath.join(folder, f'{tree.name()}{_DENIED}')
    text = fixtures._page(name, _desc('page'), _body('page')).decode('utf-8')
    tree.write(f'{name}.md', text, generator=generator)
    tree.expect(notices=['name_skip'], issues=['invalid_page_name'])


def _invalid_folder(tree: _Tree, generator: random.Random) -> None:
    """Add a folder whose name no naming policy takes, with a page inside."""
    folder = posixpath.join(tree.folder(generator), f'{tree.name()}{_DENIED}')
    relpath = posixpath.join(folder, f'{tree.name()}.md')
    tree.write(relpath, f'# A page\n\n{_body("page")}\n', generator=generator)
    tree.expect(notices=['name_skip'], issues=['invalid_folder_name'])


def _truncate_index(tree: _Tree, generator: random.Random) -> None:
    """Cut an index short, inside its block or before it."""
    index = tree.index(generator, ends=True)
    text = tree.read(index)
    block = text.index(f'\n{_FENCE}\n', len(_FENCE))
    tree.write(index, text[: generator.randrange(block + 1)], generator=generator)
    tree.expect(notices=['index_truncated'], issues=['truncated_index'])


def _drift_row(tree: _Tree, generator: random.Random) -> None:
    """Change the desc of one row of an index.

    The row is one whose page has a desc of its own, which the engine
    writes over the row's.
    """

    def described(lines: Sequence[str]) -> list[int]:
        """Return where the rows stand whose page has a desc of its own."""
        result = []
        for at in _rows(lines):
            # the row's page: a page by its stem, or the index of a folder
            path = tree.root / f'{_target(lines[at])}.md'
            if path.is_file():
                fields, _ = matrix._head(path)
                if fields.get('desc', '').strip() not in ('', '...'):
                    result.append(at)
        return result

    index = tree.index(generator, rows=described)
    lines = tree.read(index).split('\n')
    at = generator.choice(described(lines))
    link, _, _ = lines[at].partition(']]')
    lines[at : _row_end(lines, at)] = [f'{link}]]: A desc that drifted from its page.']
    tree.write(index, '\n'.join(lines), generator=generator)
    tree.expect(notices=['desc_overwrite'], issues=['requires_update'])


def _drop_row(tree: _Tree, generator: random.Random) -> None:
    """Take one row out of an index.

    The row is one whose entry stands, so the engine adds it again.
    """

    def standing(lines: Sequence[str]) -> list[int]:
        """Return where the rows stand whose entry is there."""
        result = []
        for at in _rows(lines):
            # the row's entry: a page by its stem, the index of a folder, or
            # a file by its name
            target = _target(lines[at])
            if (tree.root / f'{target}.md').is_file() or (tree.root / target).is_file():
                result.append(at)
        return result

    index = tree.index(generator, rows=standing)
    lines = tree.read(index).split('\n')
    at = generator.choice(standing(lines))
    # a row stands with the empty line behind it
    del lines[at : _row_end(lines, at) + 1]
    tree.write(index, '\n'.join(lines), generator=generator)
    tree.expect(notices=['link_add'], issues=['requires_update'])


def _broken_row(tree: _Tree, generator: random.Random) -> None:
    """Add a row for a page that does not exist to an index."""
    index = tree.any_index(generator)
    name = tree.name()
    _add_row(tree, generator, index=index, name=name, desc='A row for nothing.')
    tree.expect(notices=['link_prune'], issues=['broken_link'])


def _symlink_row(tree: _Tree, generator: random.Random) -> None:
    """Add a symlink to a page, and a row for it, to an index's folder."""
    index = tree.any_index(generator)
    name = tree.name()
    relpath = posixpath.join(posixpath.dirname(index), f'{name}.md')
    path = tree.root / relpath
    target = tree.root / tree.any_page(generator)
    os.symlink(os.path.relpath(target, path.parent), path)
    os.utime(path, (_EARLY, _EARLY), follow_symlinks=False)
    tree.note(relpath)
    _add_row(tree, generator, index=index, name=name, desc='A row for a symlink.')
    tree.expect(notices=['symlink_skip'], issues=['symlink_link_target'])


def _excluded_row(tree: _Tree, generator: random.Random) -> None:
    """Add a page an exclude pattern names, and a row for it.

    The pattern goes into the wiki's settings, beside what they hold.
    """
    index = tree.any_index(generator)
    name = f'{_EXCLUDED}{tree.name()}'
    relpath = posixpath.join(posixpath.dirname(index), f'{name}.md')
    tree.write(relpath, f'{_body("excluded page")}\n', generator=generator)
    # name the pattern in the settings, which a wiki may lack
    path = tree.root / WIKI_SETTINGS
    settings = json.loads(_read(path)) if path.is_file() else {}
    patterns = settings.setdefault('exclude', {}).setdefault('patterns', [])
    if f'{_EXCLUDED}*' not in patterns:
        patterns.append(f'{_EXCLUDED}*')
        text = json.dumps(settings, indent=2) + '\n'
        tree.write(WIKI_SETTINGS, text, generator=generator)
    _add_row(
        tree, generator, index=index, name=name, desc='A row for an excluded page.'
    )
    tree.expect(notices=['exclude_skip'], issues=['excluded_link_target'])


def _gitignored_row(tree: _Tree, generator: random.Random) -> None:
    """Add a page the repository ignores, and a row for it.

    The pattern goes into the ``.gitignore`` at the top of the checkout,
    behind what it holds.
    """
    index = tree.any_index(generator)
    name = f'{_IGNORED}{tree.name()}'
    relpath = posixpath.join(posixpath.dirname(index), f'{name}.md')
    tree.write(relpath, f'{_body("ignored page")}\n', generator=generator)
    path = tree.checkout / '.gitignore'
    held = _read(path) if path.is_file() else ''
    if f'{_IGNORED}*' not in held.split('\n'):
        _write(path, f'{_ended(held)}{_IGNORED}*\n', late=False)
    _add_row(tree, generator, index=index, name=name, desc='A row for an ignored page.')
    tree.expect(notices=['gitignore_skip'], issues=['gitignored_link_target'])


def _append(tree: _Tree, generator: random.Random, line: str) -> None:
    """Add ``line`` as a paragraph to the body of a drawn page."""
    page = tree.any_page(generator)
    text = _ended(tree.read(page))
    tree.write(page, f'{text}\n{line}\n', generator=generator)


def _add_row(
    tree: _Tree,
    generator: random.Random,
    *,
    index: str,
    name: str,
    desc: str,
) -> None:
    """Add a row for the entry ``name`` of the index's folder, above the delimiter."""
    lines = tree.read(index).split('\n')
    target = posixpath.join(posixpath.dirname(index), name)
    at = lines.index(_DELIMITER)
    lines[at:at] = [f'[[{target}|{name}]]: {desc}', '']
    tree.write(index, '\n'.join(lines), generator=generator)


def _moved(relpath: str, source: str, target: str) -> str:
    """Return ``relpath`` as it is spelled once the folder ``source`` is ``target``."""
    if relpath == source:
        return target
    if relpath.startswith(f'{source}/'):
        return f'{target}{relpath[len(source) :]}'
    return relpath


def _rows(lines: Sequence[str]) -> list[int]:
    """Return where the rows of an index open: the lines above its delimiter that link down.

    A formatter may wrap a row behind its colon, so the desc need not
    stand on the line that opens the row.
    """
    if _DELIMITER not in lines:
        return []
    return [
        number
        for number, line in enumerate(lines[: lines.index(_DELIMITER)])
        if line.startswith('[[') and (']]:' in line)
    ]


def _target(row: str) -> str:
    """Return the target of the link that opens a row of an index."""
    target, _, _ = row.removeprefix('[[').partition('|')
    return target


def _row_end(lines: Sequence[str], at: int) -> int:
    """Return where the row that opens at line ``at`` ends: the empty line behind it."""
    end = at
    while lines[end]:
        end += 1
    return end


def _split(text: str) -> tuple[list[list[str]], str]:
    """Split a page into the fields of its block and the text behind the block.

    A field is the line that opens with its key and every line up to the
    next key. A page with no closed block has no fields, and all of its
    text lies behind.
    """
    lines = text.split('\n')
    if (lines[0] != _FENCE) or (_FENCE not in lines[1:]):
        return [], text
    end = lines.index(_FENCE, 1)
    fields: list[list[str]] = []
    for line in lines[1:end]:
        if matrix._FIELD.match(line) or not fields:
            fields.append([line])
        else:
            fields[-1].append(line)
    return fields, '\n'.join(lines[end + 1 :])


def _join(fields: Sequence[Sequence[str]], rest: str) -> str:
    """Return a page of the fields of its block and the text behind the block."""
    lines = [line for field in fields for line in field]
    return '\n'.join([_FENCE, *lines, _FENCE, rest])


def _key(field: Sequence[str]) -> str:
    """Return the key of a field of a block, or nothing for lines that open with none."""
    opened = matrix._FIELD.match(field[0])
    if opened is None:
        return ''
    return opened[1]


def _spells(relpath: str) -> bool:
    """Return whether a path can stand in a link as it is written."""
    return not (_GRAMMAR & set(relpath))


def _desc(what: str) -> str:
    """Return the desc of a page or folder the perturbation adds."""
    return f'A {what} the perturbation adds.'


def _body(what: str) -> str:
    """Return the text of a page, file or paragraph the perturbation adds."""
    return f'A {WORD} {what} the perturbation adds.'


def _ended(text: str) -> str:
    """Return ``text`` with a line end behind its last line, if it has a line."""
    if text and not text.endswith('\n'):
        return f'{text}\n'
    return text


def _read(path: pathlib.Path) -> str:
    """Return the text of the file at ``path``, as it is.

    Line ends stay as they are, and so does a byte that is no valid UTF-8:
    a file the wiki excludes may hold anything.
    """
    with open(path, encoding='utf-8', errors='surrogateescape', newline='') as handle:
        return handle.read()


def _write(path: pathlib.Path, text: str, *, late: bool) -> None:
    """Write ``text`` to ``path`` as it is, at one of the two fixed times."""
    with open(
        path, 'w', encoding='utf-8', errors='surrogateescape', newline=''
    ) as handle:
        handle.write(text)
    moment = _LATE if late else _EARLY
    os.utime(path, (moment, moment))
