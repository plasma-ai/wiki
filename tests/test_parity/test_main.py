"""Test the ``parity.__main__`` module.

Drives the harness command line as a gate does: sides and corpora come
from TOML files, and the verdict is the exit code plus the result files.
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

import wiki
from parity import __main__ as cli, runner, selfcheck, side

from .conftest import ENGINE, NOW, WIKI, _needs_engine

__all__ = [
    'test_matrix_command_strips_only_a_declared_notice',
    'test_run_that_would_compare_nothing_is_refused',
    'test_harness_error_exits_2',
    'test_failed_selfcheck_keeps_its_streams_and_trees',
]

# a side that runs the engine and prints everything but the WIKI_FREEZE_TIME
# notice, as a wrapper that fixes the clock by itself does
_RELAY = f"""
import subprocess, sys
result = subprocess.run([{f'{WIKI}'!r}, *sys.argv[1:]], capture_output=True)
notice = {side.NOTICE!r}
stderr = result.stderr
if stderr.startswith(notice):
    stderr = stderr[len(notice):]
sys.stdout.buffer.write(result.stdout)
sys.stderr.buffer.write(stderr)
sys.exit(result.returncode)
"""

# a describe command: what it prints comes from the side's own environment
_DESCRIBE = "import os; print(os.environ['PYTHONPATH'], os.environ['WIKI_FREEZE_TIME'])"


@_needs_engine
@pytest.mark.parametrize(
    argnames=('declared', 'code'),
    argvalues=[(True, 0), (False, 1)],
    ids=['declared', 'undeclared'],
)
def test_matrix_command_strips_only_a_declared_notice(
    tmp_path: pathlib.Path,
    corpus: runner.Corpus,
    declared: bool,
    code: int,
) -> None:
    """A ``WIKI_FREEZE_TIME`` notice is dropped only where a side declares it.

    The engine prints the notice and a wrapper side prints none, so the
    two are equal once the engine's notice is stripped. With the notice
    undeclared the same pair differs on stderr, and the command exits 1
    and keeps the streams that differ.
    """
    relay = tmp_path / 'relay.py'
    relay.write_text(_RELAY, encoding='utf-8')
    path = ENGINE.env['PYTHONPATH']
    pythonpath = json.dumps(path)
    engine = json.dumps([f'{WIKI}'])
    notice = json.dumps(declared)
    describe = json.dumps([sys.executable, '-c', _DESCRIBE])
    relayed = json.dumps([sys.executable, f'{relay}'])
    sides = tmp_path / 'sides.toml'
    sides.write_text(
        f'[engine]\n'
        f'argv = {engine}\n'
        f'notice = {notice}\n'
        f'env = {{ PYTHONPATH = {pythonpath} }}\n'
        f'describe = {describe}\n'
        f'[relay]\n'
        f'argv = {relayed}\n'
        f'notice = false\n'
        f'env = {{ PYTHONPATH = {pythonpath} }}\n',
        encoding='utf-8',
    )
    corpora = _corpora_file(tmp_path, corpus)
    out = tmp_path / 'out'
    work = tmp_path / 'work'
    argv = [
        'matrix',
        *('--sides', f'{sides}', '--a', 'engine', '--b', 'relay'),
        *('--corpora', f'{corpora}', '--stage', 'write'),
        *('--work', f'{work}', '--out', f'{out}', '--now', NOW),
    ]
    assert cli.main(argv) == code
    # the result file names both sides, what they report as their version,
    # and what the one side that has a describe command says it is made of
    document = json.loads((out / 'example.json').read_text(encoding='utf-8'))
    assert [item['version'] for item in document['sides']] == [wiki.__version__] * 2
    described = [item['describe'] for item in document['sides']]
    assert described == [f'{path} {NOW}', None]
    (stage,) = document['stages']
    aspects = {difference['aspect'] for difference in stage['differences']}
    kept = out / 'example' / 'write' / '000 engine.err'
    if declared:
        assert aspects == set()
        assert not kept.exists()
    else:
        assert aspects == {'stderr'}
        assert kept.read_bytes().startswith(side.NOTICE)
    # the snapshot and every clone are gone
    assert not (work / 'example').exists()


@_needs_engine
@pytest.mark.parametrize(
    argnames=('broken', 'message'),
    argvalues=[
        ('stage', 'invalid choice'),
        ('corpora', 'declares no corpus'),
        ('side', 'does not start'),
        ('missing', 'does not start'),
        ('argv', 'argv must be'),
        ('unknown', 'declares no side'),
        ('twice', 'both name'),
    ],
    ids=['stage', 'corpora', 'side', 'missing', 'argv', 'unknown', 'twice'],
)
def test_run_that_would_compare_nothing_is_refused(
    tmp_path: pathlib.Path,
    corpus: runner.Corpus,
    capsys: pytest.CaptureFixture[str],
    broken: str,
    message: str,
) -> None:
    """A run with nothing to compare is no pass.

    A mistyped stage, an empty corpora file, a side that fails, a side
    whose program is missing, an ``argv`` that is no list, a side the
    file does not declare, and one side named twice: each would compare
    nothing, or a side with itself, and report no difference. The
    command exits 2 with a message instead, before it snapshots anything
    or writes a result file.
    """
    # a working side beside the one a case breaks
    nowhere = tmp_path / 'nowhere'
    argvs = {
        'side': [sys.executable, '-c', 'import nonexistent_module'],
        'missing': [f'{nowhere}'],
        'argv': f'{WIKI}',
    }
    engine = json.dumps(argvs.get(broken, [f'{WIKI}']))
    other = json.dumps([f'{WIKI}'])
    pythonpath = json.dumps(ENGINE.env['PYTHONPATH'])
    sides = tmp_path / 'sides.toml'
    sides.write_text(
        f'[engine]\n'
        f'argv = {engine}\n'
        f'env = {{ PYTHONPATH = {pythonpath} }}\n'
        f'[other]\n'
        f'argv = {other}\n'
        f'env = {{ PYTHONPATH = {pythonpath} }}\n',
        encoding='utf-8',
    )
    corpora = _corpora_file(tmp_path, corpus)
    if broken == 'corpora':
        corpora.write_text('', encoding='utf-8')
    # the second side and the stage, as the case names them
    seconds = {'unknown': 'absent', 'twice': 'engine'}
    second = seconds.get(broken, 'other')
    stage = 'wirte' if broken == 'stage' else 'write'
    out = tmp_path / 'out'
    work = tmp_path / 'work'
    argv = [
        'matrix',
        *('--sides', f'{sides}', '--a', 'engine', '--b', second),
        *('--corpora', f'{corpora}', '--stage', stage),
        *('--work', f'{work}', '--out', f'{out}', '--now', NOW),
    ]
    with pytest.raises(SystemExit) as raised:
        cli.main(argv)
    assert raised.value.code == 2
    assert message in capsys.readouterr().err
    assert not work.exists()
    assert not out.exists()


def test_harness_error_exits_2(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An error of the harness itself is exit 2, not the exit 1 of a difference.

    A sides file that does not exist is an error no message is written
    for. The command prints the traceback and exits 2, so a gate that
    reads exit 1 as two sides that differ never takes a broken run for
    one.
    """
    sides = tmp_path / 'absent.toml'
    out = tmp_path / 'out'
    work = tmp_path / 'work'
    argv = [
        'matrix',
        *('--sides', f'{sides}', '--a', 'engine', '--b', 'other'),
        *('--corpora', f'{sides}'),
        *('--work', f'{work}', '--out', f'{out}', '--now', NOW),
    ]
    assert cli.main(argv) == 2
    assert 'FileNotFoundError' in capsys.readouterr().err
    assert not work.exists()
    assert not out.exists()


@_needs_engine
def test_failed_selfcheck_keeps_its_streams_and_trees(
    tmp_path: pathlib.Path,
    corpus: runner.Corpus,
) -> None:
    """A self-check that fails leaves what is needed to see why.

    The side prints its hash seed, so its two seeded runs differ on every
    command and the clean check fails. The command exits 1, the differing
    streams of the failed check are saved beside the result file, and the
    work folder keeps the trees its runs ended with.
    """
    # a side that prints its hash seed
    code = "import os; print(os.environ.get('PYTHONHASHSEED'))"
    seeded = json.dumps([sys.executable, '-c', code])
    sides = tmp_path / 'sides.toml'
    sides.write_text(f'[seeded]\nargv = {seeded}\nnotice = false\n', encoding='utf-8')
    corpora = _corpora_file(tmp_path, corpus)
    out = tmp_path / 'out'
    work = tmp_path / 'work'
    argv = [
        'selfcheck',
        *('--sides', f'{sides}', '--side', 'seeded', '--corpora', f'{corpora}'),
        *('--work', f'{work}', '--out', f'{out}', '--now', NOW),
    ]
    # the check fails and says so in the result file
    assert cli.main(argv) == 1
    document = json.loads((out / 'example.json').read_text(encoding='utf-8'))
    verdicts = {check['name']: check['passed'] for check in document['checks']}
    assert verdicts['clean'] is False
    # the differing stream and the tree it ended with are kept, here for the
    # second of the side's two seeded runs
    seed = selfcheck._SEEDS[-1]
    run = f'seeded seed {seed}'
    stream = out / 'example' / 'clean' / 'pristine' / f'000 {run}.out'
    assert stream.read_bytes() == f'{seed}\n'.encode()
    tree = work / 'example' / 'failed' / 'clean' / 'pristine' / run / 'tree'
    assert (tree / corpus.root / '_index.md').is_file()


# ------ helpers


def _corpora_file(tmp_path: pathlib.Path, corpus: runner.Corpus) -> pathlib.Path:
    """Write a corpora file that declares ``corpus`` and return its path."""
    checkout = json.dumps(f'{corpus.checkout}')
    root = json.dumps(corpus.root)
    path = tmp_path / 'corpora.toml'
    text = f'[example]\ncheckout = {checkout}\nroot = {root}\n'
    path.write_text(text, encoding='utf-8')
    return path
