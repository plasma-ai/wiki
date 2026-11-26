"""Test the ``parity.gate`` module.

A gate is tested on the smallest thing it runs: a daily gate with no engine
is the oracle check and the fill of the merge cache, on a history of three
merges.
"""

from __future__ import annotations

import functools
import json
import pathlib

import pytest

from parity import __main__ as cli, gate, runner

from .conftest import NOW, _declare, _needs_git

__all__ = [
    'test_daily_gate_with_no_engine_fills_the_cache_and_times_its_layers',
    'test_gate_exits_with_the_highest_exit_code_of_its_steps',
    'test_full_gate_names_every_layer_in_order',
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
    # the same work folder gives every merge the path its entry is keyed on
    assert not (tmp_path / 'work' / 'merges').exists()
    assert cli.main([*argv, f'{tmp_path / "again"}']) in (0, 1)
    result = json.loads(
        (tmp_path / 'again' / 'merges' / 'example.json').read_text('utf-8')
    )
    assert result['counts']['cached'] == 3


def test_gate_exits_with_the_highest_exit_code_of_its_steps(
    tmp_path: pathlib.Path,
) -> None:
    """A layer is as its steps ended: each is timed, and the highest exit code stands.

    One step passes and one is a command line the harness refuses with exit
    2; both ran, and each printed to its own log.
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


def test_full_gate_names_every_layer_in_order(tmp_path: pathlib.Path) -> None:
    """A full gate checks the harness first, then the engine, then the history.

    Without an engine no layer compares two engines; without a loop corpus
    no loop runs. A layer whose command takes a corpus has a step for each.
    """
    layers = functools.partial(
        gate.layers,
        corpora=tmp_path / 'corpora.toml',
        work=tmp_path / 'work',
        out=tmp_path / 'out',
        cache=tmp_path / 'cache',
    )
    labels = ['corpus-a', 'corpus-b']
    alone = layers(gate.FULL, labels=labels, loop=[])
    assert [layer.name for layer in alone] == [
        'oracle',
        'selfcheck',
        'registry',
        'replay',
        'fixtures',
        'perturb-corpus',
        'merges-sample',
        'merges',
    ]
    assert [step.name for step in alone[1].steps] == labels
    both = layers(gate.FULL, labels=labels, loop=['corpus-b'], engine='engine')
    assert [layer.name for layer in both] == [
        'oracle',
        'selfcheck',
        'registry',
        'replay',
        'fixtures',
        'perturb-corpus',
        'perturb-loop',
        'merges-sample',
        'matrix',
        'fixtures-engine',
        'perturb-corpus-engine',
        'perturb-loop-engine',
        'merges',
        'merges-engine',
    ]
    daily = layers(gate.DAILY, labels=labels, loop=['corpus-b'], engine='engine')
    assert [layer.name for layer in daily] == [
        'oracle',
        'matrix',
        'fixtures-engine',
        'perturb-corpus-engine',
        'merges-engine',
        'merges',
    ]
    # the engine meets the day's merges before v1 alone stores them
    (new,) = daily[-2].steps
    assert '--new' in new.argv


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
