"""Test the ``parity.merges`` module.

A history is built the way one grows: branches that each add a page and
converge the wiki, and merge commits that join them. The replay then merges
each one again with v1's own driver and runs v1 on what the merge leaves.
"""

from __future__ import annotations

import dataclasses
import json
import os
import pathlib
import shutil
import sys

import pytest

from parity import (
    __main__ as cli,
    compare,
    merges,
    oracle,
    replay,
    runner,
    selfcheck,
    side,
)

from .conftest import NOW, _declare, _git, _needs_git

__all__ = [
    'test_history_replays_alike_on_one_engine_and_fills_the_cache',
    'test_side_that_merges_differently_is_reported',
    'test_engine_is_compared_with_what_the_cache_holds',
    'test_entry_of_another_first_tree_is_reported',
    'test_workers_share_the_merges_of_a_long_history',
    'test_cache_follows_the_engine_files_and_not_the_version',
    'test_cache_follows_the_harness_files_of_a_pass',
    'test_merges_command_replays_a_history',
    'test_merge_that_ends_in_an_error_costs_no_other_result',
    'test_merges_run_that_would_compare_nothing_is_refused',
    'test_history_of_a_checkout_that_is_no_repository_is_refused',
]

# a side whose merge driver adds a line to the file it merged, and that runs
# every other command as the side it wraps
LINE = """
import subprocess, sys
result = subprocess.run(sys.argv[1:])
if '_merge' in sys.argv:
    merged = sys.argv[sys.argv.index('_merge') + 2]
    with open(merged, 'ab') as handle:
        handle.write(b'\\n')
sys.exit(result.returncode)
"""
# a side whose merge driver merges nothing and says so
FAILING = """
import subprocess, sys
if '_merge' in sys.argv:
    sys.exit(1)
sys.exit(subprocess.run(sys.argv[1:]).returncode)
"""
# a side whose `update` also writes a file beside the wiki it runs on
STRAYING = """
import pathlib, subprocess, sys
result = subprocess.run(sys.argv[1:])
if ('update' in sys.argv) and ('--check' not in sys.argv):
    root = pathlib.Path(sys.argv[sys.argv.index('--path') + 1])
    (root.parent / 'stray.txt').write_bytes(b'')
sys.exit(result.returncode)
"""
# a side whose program is gone once it ran a command: it is handed the path
# of its own interpreter, a link, and removes it
VANISHING = """
import os, subprocess, sys
link, *command = sys.argv[1:]
result = subprocess.run(command)
if '--version' not in command:
    os.unlink(link)
sys.exit(result.returncode)
"""
# the index both branches of the second merge add a row to, as a manifest path
_INDEX = 'tree/hello/guides/_index.md'
# how many merges the long history holds: more than two workers take in one
# run of neighbors each
_LONG = 17


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
@pytest.mark.parametrize(
    ('script', 'expected', 'driven'),
    [
        (LINE, (merges.MERGE, merges.MERGE, 'tree', _INDEX), True),
        (FAILING, (merges.MERGE, merges.MERGE, 'unmerged', _INDEX), True),
        (STRAYING, ('hello', compare.END, 'tree', 'tree/stray.txt'), False),
    ],
    ids=['line', 'failing', 'straying'],
)
def test_side_that_merges_differently_is_reported(
    history: runner.Corpus,
    engine: side.Side,
    tmp_path: pathlib.Path,
    script: str,
    expected: tuple[str, str, str, str],
    driven: bool,
) -> None:
    """A side that leaves another tree or another index than v1 is reported.

    A driver that adds a line to the index it merged shows as a tree
    difference there, and one that merges nothing as index entries left
    unmerged. Neither shows on the merge that reaches no driver. A side
    whose ``update`` writes beside its wiki shows at the end of every
    merge, in the scan of the whole slot.
    """
    (told,) = merges.histories([history])
    plan = merges.prepare(told, work=tmp_path / 'work', at=tmp_path / 'at')
    doctored = _doctored(engine, tmp_path, script)
    done = list(merges.run(plan, plan.merges, [engine, doctored], now=NOW))
    found = {item.commit: item.differences for item in done}
    first, second, _ = (found[merge.commit] for merge in plan.merges)
    stage, case, aspect, detail = expected
    difference = merges.Difference(stage, 'doctored', case, aspect, detail)
    assert difference in second
    if driven:
        assert first == ()
    else:
        assert difference in first


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
    doctored = _doctored(engine, tmp_path, LINE)
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
    index = merges.Difference(merges.MERGE, 'doctored', merges.MERGE, 'tree', _INDEX)
    assert index in {difference for item in held for difference in item.differences}
    assert {item.commit: sorted(item.differences) for item in held} == {
        item.commit: sorted(item.differences) for item in live
    }


@_needs_git
def test_entry_of_another_first_tree_is_reported(
    history: runner.Corpus,
    engine: side.Side,
    tmp_path: pathlib.Path,
) -> None:
    """A cached pass that started from another tree than the engine's is reported.

    An entry holds of its first tree only the digest. Under another file
    mode mask git checks out the first parent with other modes, so the
    engine starts the merge from a tree the entry does not describe.
    """
    (told,) = merges.histories([history])
    plan = merges.prepare(told, work=tmp_path / 'work', at=tmp_path / 'at')
    stamped = merges.stamp(oracle.load(), engine.name)
    store = tmp_path / 'cache'
    sides = selfcheck._seeded(engine)
    first = plan.merges[:1]
    list(merges.run(plan, first, sides, now=NOW, store=store, stamped=stamped))
    other = dataclasses.replace(engine, name='other')
    # the workers are processes of this one, and take its mask
    mask = os.umask(0o077)
    try:
        (held,) = merges.run(
            plan,
            first,
            [engine, other],
            now=NOW,
            second=True,
            store=store,
            stamped=stamped,
        )
    finally:
        os.umask(mask)
    assert held.cached
    moved = merges.Difference(merges.MERGE, engine.name, compare.BASELINE, 'tree')
    assert moved in held.differences


@_needs_git
def test_workers_share_the_merges_of_a_long_history(
    engine: side.Side,
    tmp_path: pathlib.Path,
) -> None:
    """Two workers replay a history between them, and every merge ends once.

    The history holds more merges than two runs of neighbors, so a worker
    takes a second run when its first is done. Its tree declares no wiki:
    a merge is the checkout and git's own merge, which is all the workers
    need to hand each other the slots. Each worker leaves the folder of
    the merges empty, and the cache answers a second run alone.
    """
    checkout = tmp_path / 'checkout'
    checkout.mkdir()
    _git(checkout, 'init', '-q', '-b', 'main')
    (checkout / 'base.txt').write_text('base\n', encoding='utf-8')
    _git(checkout, 'add', '-A')
    _git(checkout, 'commit', '-q', '-m', 'base')
    for number in range(_LONG):
        _git(checkout, 'checkout', '-q', '-b', f'branch-{number}')
        (checkout / f'{number}.txt').write_text(f'{number}\n', encoding='utf-8')
        _git(checkout, 'add', '-A')
        _git(checkout, 'commit', '-q', '-m', f'{number}')
        _git(checkout, 'checkout', '-q', 'main')
        _git(checkout, 'merge', '-q', '--no-ff', '-m', f'{number}', f'branch-{number}')
    long = runner.Corpus(label='long', checkout=checkout, root='wiki')
    (told,) = merges.histories([long])
    plan = merges.prepare(told, work=tmp_path / 'work', at=tmp_path / 'at')
    assert len(plan.merges) == _LONG
    sides = selfcheck._seeded(engine)
    stamped = merges.stamp(oracle.load(), engine.name)
    store = tmp_path / 'cache'
    done = list(
        merges.run(
            plan, plan.merges, sides, now=NOW, store=store, stamped=stamped, jobs=2
        )
    )
    assert sorted(item.commit for item in done) == sorted(
        merge.commit for merge in plan.merges
    )
    assert {(item.error, item.differences, item.cached) for item in done} == {
        (None, (), False)
    }
    assert list((tmp_path / 'at').iterdir()) == []
    again = list(
        merges.run(
            plan, plan.merges, sides, now=NOW, store=store, stamped=stamped, jobs=2
        )
    )
    assert [item.cached for item in again] == [True] * _LONG


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
def test_cache_follows_the_harness_files_of_a_pass(
    engine: side.Side,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cache key moves with a harness file that makes a pass, and with no other.

    How a side is run decides what a pass holds, so a change there leaves
    no entry valid; the perturbation is no part of a merge replay.
    """
    harness = tmp_path / 'parity'
    ignore = shutil.ignore_patterns('tests', '__pycache__')
    shutil.copytree(pathlib.Path(merges.__file__).parent, harness, ignore=ignore)
    monkeypatch.setattr(merges, '_HARNESS', harness)
    base = merges.stamp(oracle.load(), engine.name)
    with open(harness / 'perturb.py', 'a', encoding='utf-8') as handle:
        handle.write('# a change\n')
    assert merges.stamp(oracle.load(), engine.name) == base
    with open(harness / 'side.py', 'a', encoding='utf-8') as handle:
        handle.write('# a change\n')
    moved = merges.stamp(oracle.load(), engine.name)
    assert moved.harness != base.harness
    assert dataclasses.replace(moved, harness=base.harness) == base


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
    assert sorted(document['cache']) == [
        'constraints',
        'engine',
        'git',
        'harness',
        'python',
    ]
    assert not (tmp_path / 'work' / 'merges').exists()
    # a second run names one merge, and the cache holds it
    commit = document['merges'][1]['commit']
    argv[argv.index('--out') + 1] = f'{tmp_path / "again"}'
    assert cli.main([*argv, '--commit', commit[:12]]) == 0
    document = json.loads((tmp_path / 'again' / 'example.json').read_text('utf-8'))
    assert [(row['commit'], row['cached']) for row in document['merges']] == [
        (commit, True)
    ]


@_needs_git
def test_merge_that_ends_in_an_error_costs_no_other_result(
    history: runner.Corpus,
    engine: side.Side,
    tmp_path: pathlib.Path,
) -> None:
    """A merge a side cannot run is recorded, the others run on, and the run exits 2.

    The engine's program is gone after its first command, so every merge
    ends in an error on that side. Each has its row all the same: the
    worker starts the next merge in a new repository. The run leaves its
    work folder and hands the folder of the merges back empty.
    """
    script = tmp_path / 'vanishing.py'
    script.write_text(VANISHING, encoding='utf-8')
    link = tmp_path / 'python'
    link.symlink_to(sys.executable)
    argv = [f'{link}', '-P', f'{script}', f'{link}', *engine.argv]
    listed = ', '.join(json.dumps(part) for part in argv)
    sides = tmp_path / 'sides.toml'
    sides.write_text(f'[vanishing]\nargv = [{listed}]\n', encoding='utf-8')
    argv = [
        'merges',
        '--corpora',
        f'{_declare(tmp_path, history)}',
        '--sides',
        f'{sides}',
        '--engine',
        'vanishing',
        '--at',
        f'{tmp_path / "at"}',
        '--work',
        f'{tmp_path / "work"}',
        '--out',
        f'{tmp_path / "out"}',
        '--now',
        NOW,
    ]
    assert cli.main(argv) == 2
    document = json.loads((tmp_path / 'out' / 'example.json').read_text('utf-8'))
    assert document['counts']['errors'] == 3
    errors = [row['error'] for row in document['merges']]
    assert [error.partition(':')[0] for error in errors] == ['FileNotFoundError'] * 3
    assert (tmp_path / 'work' / 'merges').is_dir()
    assert not (tmp_path / 'at').exists()


@pytest.mark.parametrize(
    ('extra', 'message'),
    [
        (['--sample', '0'], '--sample must be at least 1'),
        (['--jobs', '0'], '--jobs must be at least 1'),
        (['--corpus', 'missing'], 'declares no corpus named'),
        (['--corpus', 'example', '--corpus', 'example'], 'names a corpus twice'),
        (['--engine', 'oracle'], 'the side v1 runs as'),
        (['--engine', 'missing'], 'No side is named'),
        (['--commit', 'deadbeef'], 'names no merge commit'),
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


def _doctored(engine: side.Side, folder: pathlib.Path, text: str) -> side.Side:
    """Return ``engine`` behind the wrapper script ``text``, as the side ``doctored``."""
    script = folder / 'doctored.py'
    script.write_text(text, encoding='utf-8')
    argv = (sys.executable, '-P', f'{script}', *engine.argv)
    return dataclasses.replace(engine, name='doctored', argv=argv)
