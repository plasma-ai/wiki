"""Test the ``parity.merges`` module.

A history is built the way one grows: branches that each add a page and
converge the wiki, and merge commits that join them. The replay then merges
each one again with v1's own driver and runs v1 on what the merge leaves.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import sys

import pytest

from parity import __main__ as cli, merges, oracle, replay, runner, selfcheck, side

from .conftest import NOW, _declare, _git, _needs_git

__all__ = [
    'test_history_replays_alike_on_one_engine_and_fills_the_cache',
    'test_driver_that_merges_differently_is_reported',
    'test_engine_is_compared_with_what_the_cache_holds',
    'test_cache_follows_the_engine_files_and_not_the_version',
    'test_merges_command_replays_a_history',
    'test_merges_run_that_would_compare_nothing_is_refused',
    'test_history_of_a_checkout_that_is_no_repository_is_refused',
]

# a side whose merge driver adds a line to the file it merged, and that runs
# every other command as the side it wraps
DOCTORED = """
import subprocess, sys
result = subprocess.run(sys.argv[1:])
if '_merge' in sys.argv:
    merged = sys.argv[sys.argv.index('_merge') + 2]
    with open(merged, 'ab') as handle:
        handle.write(b'\\n')
sys.exit(result.returncode)
"""
# the index both branches of the second merge add a row to, as a manifest path
_INDEX = 'tree/hello/guides/_index.md'


@_needs_git
def test_history_replays_alike_on_one_engine_and_fills_the_cache(
    history: runner.Corpus,
    engine: side.Side,
    tmp_path: pathlib.Path,
) -> None:
    """v1 replays every merge alike under two hash seeds, and the cache keeps it.

    The three merges are what the history was built to hold: one that
    reaches no driver, one whose driver merges an index clean, and one
    whose driver leaves a page in conflict, which ``update`` refuses. A
    second run reads all
    three from the cache and runs no side; a run at another instant finds
    none there.
    """
    (told,) = merges.histories([history])
    plan = merges.prepare(told, work=tmp_path / 'work', at=tmp_path / 'at')
    sides = selfcheck._seeded(engine)
    stamped = merges.stamp(oracle.load(), engine.name)
    store = tmp_path / 'cache'
    done = list(
        merges.run(plan, plan.merges, sides, now=NOW, store=store, stamped=stamped)
    )
    assert [item.error for item in done] == [None, None, None]
    assert [item.differences for item in done] == [(), (), ()]
    reports = {item.commit: item.report for item in done}
    first, second, third = (reports[merge.commit] for merge in plan.merges)
    assert (first['driver_calls'], first['unmerged'], first['refused']) == (0, 0, [])
    assert (second['driver_calls'], second['unmerged']) == (1, 0)
    assert (second['refused'], second['unconverged']) == ([], [])
    # one path in three stages, and a tree no update takes
    assert (third['driver_calls'], third['unmerged']) == (1, 3)
    assert third['refused'] == [history.root]
    counts = merges.summary(done)
    assert (counts['merges'], counts['cached'], counts['driven']) == (3, 0, 2)
    assert (counts['conflicted'], counts['refused']) == (1, 1)
    # the cache answers a second run alone
    again = list(
        merges.run(plan, plan.merges, sides, now=NOW, store=store, stamped=stamped)
    )
    assert [item.cached for item in again] == [True, True, True]
    assert {item.commit: item.report for item in again} == reports
    # an entry is keyed on the instant
    later = '2026-01-02T03:04:06Z'
    other = list(
        merges.run(
            plan, plan.merges[:1], sides, now=later, store=store, stamped=stamped
        )
    )
    assert [item.cached for item in other] == [False]


@_needs_git
def test_driver_that_merges_differently_is_reported(
    history: runner.Corpus,
    engine: side.Side,
    tmp_path: pathlib.Path,
) -> None:
    """A driver that leaves another merged index shows as a tree difference.

    The doctored side merges as v1 does and adds a line to the file. The
    merge that reaches no driver shows nothing, and the one whose driver
    merges an index differs at that index.
    """
    (told,) = merges.histories([history])
    plan = merges.prepare(told, work=tmp_path / 'work', at=tmp_path / 'at')
    done = list(
        merges.run(plan, plan.merges, [engine, _doctored(engine, tmp_path)], now=NOW)
    )
    found = {item.commit: item.differences for item in done}
    first, second, _ = (found[merge.commit] for merge in plan.merges)
    assert first == ()
    assert _difference(_INDEX) in second


@_needs_git
def test_engine_is_compared_with_what_the_cache_holds(
    history: runner.Corpus,
    engine: side.Side,
    tmp_path: pathlib.Path,
) -> None:
    """A second engine is compared with v1's cached run as with a live one.

    The run of v1 alone fills the cache. The run with an engine then runs
    v1 on no merge, and reports of the doctored driver what a run against
    a live v1 reports.
    """
    (told,) = merges.histories([history])
    plan = merges.prepare(told, work=tmp_path / 'work', at=tmp_path / 'at')
    stamped = merges.stamp(oracle.load(), engine.name)
    store = tmp_path / 'cache'
    sides = selfcheck._seeded(engine)
    list(merges.run(plan, plan.merges, sides, now=NOW, store=store, stamped=stamped))
    doctored = _doctored(engine, tmp_path)
    sides = [engine, doctored, replay.threaded(doctored)]
    live = list(merges.run(plan, plan.merges, sides, now=NOW, second=True))
    held = list(
        merges.run(
            plan,
            plan.merges,
            sides,
            now=NOW,
            second=True,
            store=store,
            stamped=stamped,
        )
    )
    assert [item.cached for item in held] == [True, True, True]
    assert [item.cached for item in live] == [False, False, False]
    assert _difference(_INDEX) in {
        difference for item in held for difference in item.differences
    }
    assert {item.commit: sorted(item.differences) for item in held} == {
        item.commit: sorted(item.differences) for item in live
    }


@_needs_git
def test_cache_follows_the_engine_files_and_not_the_version(
    tmp_path: pathlib.Path,
) -> None:
    """The cache key moves with an engine file, and with nothing else of v1.

    A release that changes the version line, a skill or a file outside the
    package leaves every cached run of v1 valid; a change to an engine file
    or to a package pin does not.
    """
    checkout = tmp_path / 'oracle'
    files = {
        'wiki/__init__.py': "__version__ = '1.0.0'\nname = 'wiki'\n",
        'wiki/core.py': 'value = 1\n',
        'wiki/skills/skill.md': 'A skill.\n',
        'README.md': 'About.\n',
    }
    pins = tmp_path / 'pins.txt'
    pins.write_text('pyyaml==6.0\n', encoding='utf-8')

    def stamped(changes: dict[str, str]) -> merges.Stamp:
        """Commit the files with ``changes`` and return the stamp of that commit."""
        if not checkout.exists():
            checkout.mkdir()
            _git(checkout, 'init', '-q', '-b', 'main')
        for relpath, text in {**files, **changes}.items():
            path = checkout / relpath
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8')
        _git(checkout, 'add', '-A')
        _git(checkout, 'commit', '-q', '--allow-empty', '-m', 'files')
        build = oracle.Build('oracle', '3.14.6', '16.0.0', '0.2.5', pins)
        named = oracle.Oracle(
            path=tmp_path / 'oracle.toml',
            repo=tmp_path,
            checkout=checkout,
            commit=_git(checkout, 'rev-parse', 'HEAD'),
            version='1.0.0',
            base='main',
            pending=None,
            builds={'oracle': build},
            unmerged={},
        )
        return merges.stamp(named, 'oracle')

    base = stamped({})
    released = {'wiki/__init__.py': "__version__ = '1.1.0'\nname = 'wiki'\n"}
    assert stamped(released) == base
    assert stamped({'wiki/skills/skill.md': 'Another skill.\n'}) == base
    assert stamped({'README.md': 'More.\n'}) == base
    assert stamped({'wiki/core.py': 'value = 2\n'}).engine != base.engine
    renamed = {'wiki/__init__.py': "__version__ = '1.0.0'\nname = 'other'\n"}
    assert stamped(renamed).engine != base.engine
    pins.write_text('pyyaml==6.1\n', encoding='utf-8')
    moved = stamped({})
    assert (moved.engine, moved.constraints != base.constraints) == (base.engine, True)


@_needs_git
def test_merges_command_replays_a_history(
    history: runner.Corpus,
    tmp_path: pathlib.Path,
) -> None:
    """The command replays every merge, writes the result file and exits 0.

    The fixed sample is drawn by commit, so a sample of one merge in one
    is the whole history. A run that passed removes its work folder.
    """
    corpora = _declare(tmp_path, history)
    argv = [
        'merges',
        '--corpora',
        f'{corpora}',
        '--work',
        f'{tmp_path / "work"}',
        '--out',
        f'{tmp_path / "out"}',
        '--cache',
        f'{tmp_path / "cache"}',
        '--sample',
        '1',
        '--now',
        NOW,
    ]
    assert cli.main(argv) == 0
    document = json.loads((tmp_path / 'out' / 'example.json').read_text('utf-8'))
    assert document['history_merges'] == 3
    assert document['counts']['merges'] == 3
    assert (document['counts']['driven'], document['counts']['conflicted']) == (2, 1)
    assert [row['cached'] for row in document['merges']] == [False, False, False]
    assert sorted(document['cache']) == ['constraints', 'engine', 'git', 'python']
    assert not (tmp_path / 'work' / 'merges').exists()
    # a second run names one merge, and the cache holds it
    commit = document['merges'][1]['commit']
    argv[argv.index('--out') + 1] = f'{tmp_path / "again"}'
    assert cli.main([*argv, '--commit', commit[:12]]) == 0
    document = json.loads((tmp_path / 'again' / 'example.json').read_text('utf-8'))
    assert [(row['commit'], row['cached']) for row in document['merges']] == [
        (commit, True)
    ]


@pytest.mark.parametrize(
    ('extra', 'message'),
    [
        (['--sample', '0'], '--sample must be at least 1'),
        (['--jobs', '0'], '--jobs must be at least 1'),
        (['--corpus', 'missing'], 'declares no corpus named'),
        (['--corpus', 'example', '--corpus', 'example'], 'names a corpus twice'),
        (['--engine', 'oracle'], 'the side v1 runs as'),
        (['--engine', 'missing'], 'No side is named'),
    ],
)
def test_merges_run_that_would_compare_nothing_is_refused(
    history: runner.Corpus,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    extra: list[str],
    message: str,
) -> None:
    """A run that would replay nothing exits 2 before it snapshots a repository."""
    corpora = _declare(tmp_path, history)
    argv = [
        'merges',
        '--corpora',
        f'{corpora}',
        '--work',
        f'{tmp_path / "work"}',
        '--out',
        f'{tmp_path / "out"}',
        *extra,
    ]
    with pytest.raises(SystemExit) as exited:
        cli.main(argv)
    assert exited.value.code == 2
    assert message in capsys.readouterr().err
    assert not (tmp_path / 'work').exists()


def test_history_of_a_checkout_that_is_no_repository_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    """A wiki outside any repository has no merge to replay."""
    plain = runner.Corpus(label='plain', checkout=tmp_path, root='wiki')
    with pytest.raises(ValueError, match='lies in no repository'):
        merges.histories([plain])


# ------ helper functions


def _difference(relpath: str) -> merges.Difference:
    """Return the difference of a doctored driver at the index ``relpath``."""
    return merges.Difference(merges.MERGE, 'doctored', merges.MERGE, 'tree', relpath)


def _doctored(engine: side.Side, folder: pathlib.Path) -> side.Side:
    """Return ``engine`` behind a wrapper whose merge driver adds a line."""
    script = folder / 'doctored.py'
    script.write_text(DOCTORED, encoding='utf-8')
    argv = (sys.executable, '-P', f'{script}', *engine.argv)
    return dataclasses.replace(engine, name='doctored', argv=argv)
