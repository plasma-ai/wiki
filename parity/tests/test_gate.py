"""Test the ``parity.gate`` module.

A gate is tested on the smallest thing it runs: a daily gate with no engine
is the oracle check and the fill of the merge cache, on a history of three
merges.
"""

from __future__ import annotations

import json
import pathlib
import shutil

import pytest

from parity import __main__ as cli, gate, runner

from .conftest import NOW, _declare, _needs_git

__all__ = [
    'test_daily_gate_with_no_engine_fills_the_cache_and_times_its_layers',
    'test_layers_of_the_merge_history_share_the_cache',
    'test_layer_runs_every_step_and_times_each',
    'test_layer_ends_as_the_worst_of_its_steps',
    'test_gate_that_would_run_nothing_is_refused',
]


@_needs_git
def test_daily_gate_with_no_engine_fills_the_cache_and_times_its_layers(
    history: runner.Corpus,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A daily gate of v1 alone replays the merges, stores them and times itself.

    The result file holds each layer with its seconds and processor seconds,
    and each step writes its results and its log below the gate's folder. A
    second gate at the same work folder finds every merge in the cache.
    """
    argv = [
        'gate',
        'daily',
        '--corpora',
        f'{_declare(tmp_path, history)}',
        '--cache',
        f'{tmp_path / "cache"}',
        '--work',
        f'{tmp_path / "work"}',
        '--now',
        NOW,
        '--out',
    ]
    # the oracle check reads the remote, which a test cannot hold level
    assert cli.main([*argv, f'{tmp_path / "out"}']) in (0, 1)
    assert 'no engine is named' in capsys.readouterr().out
    document = json.loads((tmp_path / 'out' / 'gate.json').read_text('utf-8'))
    assert (document['gate'], document['engine']) == ('daily', None)
    oracle, merges = document['layers']
    names = (oracle['name'], merges['name'], merges['part'])
    assert names == ('oracle', 'merges', 'history')
    assert merges['exit'] == 0
    assert merges['seconds'] > 0
    # the sides are processes the step waited for
    assert merges['cpu'] > 0
    assert merges['parallelism'] == round(merges['cpu'] / merges['seconds'], 2)
    result = json.loads(
        (tmp_path / 'out' / 'merges' / 'example.json').read_text('utf-8')
    )
    assert result['counts']['merges'] == 3
    assert result['counts']['cached'] == 0
    assert 'example: 0 of 3 merges differ' in (
        tmp_path / 'out' / 'merges.txt'
    ).read_text('utf-8')
    # the same work folder gives every merge the path its entry is keyed on;
    # a gate whose oracle check failed keeps its own, with nothing of a merge
    assert not (tmp_path / 'work' / 'at').exists()
    assert not (tmp_path / 'work' / 'merges' / 'merges').exists()
    shutil.rmtree(tmp_path / 'work', ignore_errors=True)
    assert cli.main([*argv, f'{tmp_path / "again"}']) in (0, 1)
    result = json.loads(
        (tmp_path / 'again' / 'merges' / 'example.json').read_text('utf-8')
    )
    assert result['counts']['cached'] == 3


@_needs_git
def test_layers_of_the_merge_history_share_the_cache(
    history: runner.Corpus,
    tmp_path: pathlib.Path,
) -> None:
    """The layers that replay merges read what another layer of a gate stored.

    The layers of the merge history run as a gate orders them, with the
    second interpreter of v1 as the engine. A first daily gate meets every
    merge as a new one: the engine runs beside v1, and v1 alone then
    stores them. The next daily gate finds no new merge. A full gate reads
    v1's part of every merge from the cache, with and without the engine.
    """
    corpora = _declare(tmp_path, history)

    def replayed(kind: str, name: str) -> dict[str, tuple[int, int]]:
        """Run the gate's history layers; return each one's merges and cached ones."""
        planned = gate.layers(
            kind,
            corpora=corpora,
            labels=[history.label],
            loop=[],
            work=tmp_path / 'work',
            out=tmp_path / name,
            cache=tmp_path / 'cache',
            engine='alt',
            now=NOW,
        )
        counts = {}
        for done in gate.run([layer for layer in planned if layer.part == 'history']):
            assert done.code == 0, done.layer.steps[0].log.read_text('utf-8')
            path = tmp_path / name / done.layer.name / f'{history.label}.json'
            held = json.loads(path.read_text('utf-8'))['counts']
            counts[done.layer.name] = (held['merges'], held['cached'])
        return counts

    first = replayed(gate.DAILY, 'first')
    assert list(first) == ['merges-engine', 'merges']
    assert first == {'merges-engine': (3, 0), 'merges': (3, 0)}
    assert replayed(gate.DAILY, 'second') == {'merges-engine': (0, 0), 'merges': (3, 3)}
    full = replayed(gate.FULL, 'full')
    assert list(full) == ['merges', 'merges-engine']
    assert full == {'merges': (3, 3), 'merges-engine': (3, 3)}


def test_layer_runs_every_step_and_times_each(tmp_path: pathlib.Path) -> None:
    """A layer is as its steps ended: each ran, printed to its own log and is timed.

    One step passes and one is a command line the harness refuses with exit
    2, which is then the layer's.
    """
    passing = gate.Step(
        name='passing',
        argv=(
            'oracle',
            'check',
            '--skip',
            'base',
            '--skip',
            'level',
            '--skip',
            'removed',
        ),
        log=tmp_path / 'passing.txt',
    )
    refused = gate.Step(
        name='refused',
        argv=('matrix',),
        log=tmp_path / 'refused.txt',
    )
    layer = gate.Layer(name='layer', part='self-check', steps=(passing, refused))
    (done,) = gate.run([layer])
    assert [(step.name, step.code) for step in done.steps] == [
        ('passing', 0),
        ('refused', 2),
    ]
    assert done.code == 2
    assert 'the following arguments are required' in refused.log.read_text('utf-8')
    assert passing.log.is_file()
    assert done.seconds >= max(step.seconds for step in done.steps)


@pytest.mark.parametrize(
    ('codes', 'expected'),
    [
        ((0, 0), 0),
        ((0, 1), 1),
        ((1, 2), 2),
        ((0, 3), 2),
        ((-9, 0), 2),
        ((-9, 1), 2),
    ],
)
def test_layer_ends_as_the_worst_of_its_steps(
    tmp_path: pathlib.Path,
    codes: tuple[int, ...],
    expected: int,
) -> None:
    """A layer exits 0, 1 for a result that is not the expected one, or 2.

    A step that a signal ended has a negative code. It is an error, as
    every code above 1 is, whatever the other steps showed.
    """
    steps = tuple(
        gate.Step(name=f'{index}', argv=(), log=tmp_path / f'{index}.txt')
        for index, _ in enumerate(codes)
    )
    layer = gate.Layer(name='layer', part='self-check', steps=steps)
    timed = tuple(
        gate.Timed(name=step.name, code=code, seconds=1.0, cpu=1.0)
        for step, code in zip(steps, codes)
    )
    done = gate.Done(layer=layer, steps=timed, seconds=1.0, load=1.0, load_end=1.0)
    assert done.code == expected


@pytest.mark.parametrize(
    ('extra', 'message'),
    [
        (['--corpus', 'missing'], 'declares no corpus named'),
        (['--loop', 'missing'], 'which the gate does not run on'),
        (['--fresh', '0'], '--fresh must be at least 1'),
        (['--sample', '0'], '--sample must be at least 1'),
        (['--jobs', '0'], '--jobs must be at least 1'),
        (['--engine', 'oracle'], 'the side v1 runs as'),
        (['--engine', 'missing'], 'No side is named'),
    ],
)
def test_gate_that_would_run_nothing_is_refused(
    history: runner.Corpus,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    extra: list[str],
    message: str,
) -> None:
    """A gate the harness cannot run exits 2 before a step starts."""
    argv = [
        'gate',
        'full',
        '--corpora',
        f'{_declare(tmp_path, history)}',
        '--cache',
        f'{tmp_path / "cache"}',
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
    assert not (tmp_path / 'out').exists()
