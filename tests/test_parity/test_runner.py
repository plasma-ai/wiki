"""Test the ``parity.runner`` module.

The matrix runs themselves are covered through the self-check and the
command line; this module carries the planning refusals, the rules that
hold for one side by itself, and what the runner gives each stage: a
cold start, a run per pair of sides, and a scan of the whole slot.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

from parity import cache, matrix, runner, side

from .conftest import NOW, _needs_engine

__all__ = [
    'test_work_folder_below_a_wiki_is_refused',
    'test_unknown_stage_is_refused',
    'test_sides_that_fail_alike_are_reported',
    'test_cold_stage_starts_without_the_cache_folder',
    'test_continued_stage_runs_each_side_on_each_tree',
    'test_write_outside_the_tree_is_reported',
]

# a side that runs no wiki command and succeeds, for the tests that read
# what the runner does around a command
_IDLE = (sys.executable, '-c', 'pass')
# a side that writes one file into its home on every command
_STRAY = 'stray'
_STRAYING = (
    sys.executable,
    '-c',
    f'import os, pathlib; pathlib.Path(os.environ["HOME"], {_STRAY!r}).write_text("x")',
)


def test_work_folder_below_a_wiki_is_refused(tmp_path: pathlib.Path) -> None:
    """A work folder under a declared wiki is refused before any snapshot.

    Every side runs under a scratch home, so a ``.wiki/settings.json``
    above the work folder -- the user's own config home included -- reads
    as an enclosing wiki, and each command run from inside the snapshot's
    wiki would fail the same way on both sides and compare equal.
    """
    (tmp_path / '.wiki').mkdir()
    (tmp_path / '.wiki' / 'settings.json').write_text('{}\n')
    corpus = runner.Corpus(label='example', checkout=tmp_path / 'checkout', root='wiki')
    with pytest.raises(ValueError, match='lies below'):
        runner.prepare(corpus, work=tmp_path / 'work')
    assert not (tmp_path / 'work').exists()


@_needs_engine
def test_unknown_stage_is_refused(
    tmp_path: pathlib.Path,
    corpus: runner.Corpus,
) -> None:
    """A stage name the plan does not hold is an error, not an empty run.

    A run of no stage finds no difference, so a mistyped name would read
    as a pass.
    """
    plan = runner.prepare(corpus, work=tmp_path / 'work')
    with pytest.raises(ValueError, match='No stage is named'):
        runner.run(plan, [], now=NOW, only=['query', 'wirte'])


@_needs_engine
def test_sides_that_fail_alike_are_reported(
    tmp_path: pathlib.Path,
    corpus: runner.Corpus,
) -> None:
    """Two sides that run no wiki command do not pass by agreeing.

    Both sides exit 1 with no output on every command, so no stream, exit
    code or tree differs between them. Each command that is built to
    succeed, or to fail as an error, is still reported on each side, and
    each search and match that is built to find something is listed as
    one that found nothing.
    """
    argv = (sys.executable, '-c', 'raise SystemExit(1)')
    broken = [side.Side(name=name, argv=argv, notice=False) for name in ('one', 'two')]
    plan = runner.prepare(corpus, work=tmp_path / 'work')
    result = runner.run(plan, broken, now=NOW, only=['query'])
    assert {difference.aspect for difference in result.differences} == {'unexpected'}
    reported = {(difference.run, difference.case) for difference in result.differences}
    for name in ('one', 'two'):
        assert {(name, 'map'), (name, 'match (')} <= reported
    # exit 1 is what a search with no match returns: the cases built to find
    # something are listed, the ones built to find nothing are not
    (stage,) = result.report['stages']
    no_hit = stage['no_hit']
    term, *_ = plan.sample.terms
    assert f'search {term}' in no_hit
    assert not any(matrix._ABSENT in name for name in no_hit)


@_needs_engine
def test_cold_stage_starts_without_the_cache_folder(
    tmp_path: pathlib.Path,
    corpus: runner.Corpus,
) -> None:
    """A cold stage removes the cache folder its tree was cloned with.

    The snapshot holds the caches the checkout had, and ``write`` is the
    stage that builds both from nothing. A side that runs no wiki
    command leaves the tree as the stage gave it: without the folder.
    """
    idle = side.Side(name='idle', argv=_IDLE, notice=False)
    work = tmp_path / 'work'
    plan = runner.prepare(corpus, work=work)
    runner.run(plan, [idle], now=NOW, only=['write'], keep=True)
    assert (work / 'snapshot' / corpus.root / cache.CACHE).is_dir()
    tree = plan.kept / 'write' / 'idle' / 'tree'
    assert (tree / corpus.root).is_dir()
    assert not (tree / corpus.root / cache.CACHE).exists()


@_needs_engine
def test_continued_stage_runs_each_side_on_each_tree(
    tmp_path: pathlib.Path,
    corpus: runner.Corpus,
) -> None:
    """A stage that continues another has one run per pair of sides.

    ``cross`` runs each side on the tree each side left after ``write``,
    so each engine reads the caches the other wrote. A run on the first
    side's trees alone would compare an engine only with itself.
    """
    idle = [side.Side(name=name, argv=_IDLE, notice=False) for name in ('one', 'two')]
    plan = runner.prepare(corpus, work=tmp_path / 'work')
    result = runner.run(plan, idle, now=NOW, only=['cross'])
    runs = {stage['name']: stage['runs'] for stage in result.report['stages']}
    assert runs == {
        'write': ['one', 'two'],
        'cross': ['one on one', 'one on two', 'two on one', 'two on two'],
    }


@_needs_engine
def test_write_outside_the_tree_is_reported(
    tmp_path: pathlib.Path,
    corpus: runner.Corpus,
) -> None:
    """A file a side writes into its home is a difference like one in the tree.

    The scan covers the whole slot, so a side that leaves a file in its
    home, where the other leaves none, differs in tree and touched set
    at that path.
    """
    idle = side.Side(name='idle', argv=_IDLE, notice=False)
    straying = side.Side(name='straying', argv=_STRAYING, notice=False)
    plan = runner.prepare(corpus, work=tmp_path / 'work')
    result = runner.run(plan, [idle, straying], now=NOW, only=['full'])
    found = {
        (difference.run, difference.aspect, difference.detail)
        for difference in result.differences
    }
    path = f'home/{_STRAY}'
    assert {('straying', 'tree', path), ('straying', 'touched', path)} <= found
