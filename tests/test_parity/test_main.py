"""Test the ``parity.__main__`` module.

Drives the harness command line as a gate does: sides and corpora come
from TOML files, and the verdict is the exit code plus the result files.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import sys

import pytest

import wiki
from parity import __main__ as cli, runner, side

from .conftest import ENGINE, NOW, WIKI

__all__ = [
    'test_matrix_command_strips_only_a_declared_notice',
    'test_run_that_would_compare_nothing_is_refused',
]

pytestmark = pytest.mark.skipif(
    (WIKI is None) or (shutil.which('git') is None),
    reason='requires the wiki console script and git',
)

# a side that runs the engine and prints everything but the WIKI_NOW notice,
# as a wrapper that fixes the clock by itself does
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
    """A side's ``WIKI_NOW`` notice is dropped only where the side declares it.

    The engine prints the notice and a wrapper side prints none, so the
    two are equal once the engine's notice is stripped. With the notice
    undeclared the same pair differs on stderr, and the command exits 1
    and keeps the streams that differ.
    """
    relay = tmp_path / 'relay.py'
    relay.write_text(_RELAY, encoding='utf-8')
    sides = tmp_path / 'sides.toml'
    sides.write_text(
        f'[engine]\n'
        f'argv = [{json.dumps(f"{WIKI}")}]\n'
        f'notice = {json.dumps(declared)}\n'
        f'env = {{ PYTHONPATH = {json.dumps(ENGINE.env["PYTHONPATH"])} }}\n'
        f'[relay]\n'
        f'argv = [{json.dumps(sys.executable)}, {json.dumps(f"{relay}")}]\n'
        f'notice = false\n'
        f'env = {{ PYTHONPATH = {json.dumps(ENGINE.env["PYTHONPATH"])} }}\n',
        encoding='utf-8',
    )
    corpora = tmp_path / 'corpora.toml'
    corpora.write_text(
        f'[example]\n'
        f'checkout = {json.dumps(f"{corpus.checkout}")}\n'
        f'root = {json.dumps(corpus.root)}\n',
        encoding='utf-8',
    )
    out = tmp_path / 'out'
    argv = [
        'matrix',
        *('--sides', f'{sides}', '--a', 'engine', '--b', 'relay'),
        *('--corpora', f'{corpora}', '--stage', 'write'),
        *('--work', f'{tmp_path / "work"}', '--out', f'{out}', '--now', NOW),
    ]
    assert cli.main(argv) == code
    # the result file names both sides and what they report as their version
    document = json.loads((out / 'example.json').read_text(encoding='utf-8'))
    assert [item['version'] for item in document['sides']] == [wiki.__version__] * 2
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
    assert not (tmp_path / 'work' / 'example').exists()


@pytest.mark.parametrize(
    argnames=('broken', 'message'),
    argvalues=[
        ('stage', 'invalid choice'),
        ('corpora', 'declares no corpus'),
        ('side', 'does not start'),
    ],
)
def test_run_that_would_compare_nothing_is_refused(
    tmp_path: pathlib.Path,
    corpus: runner.Corpus,
    capsys: pytest.CaptureFixture[str],
    broken: str,
    message: str,
) -> None:
    """A mistyped stage, an empty corpora file and a dead side are no pass.

    Each would compare nothing and report no difference. The command exits
    2 with a message instead, before it snapshots anything or writes a
    result file.
    """
    sides = tmp_path / 'sides.toml'
    engine = [f'{WIKI}']
    if broken == 'side':
        engine = [sys.executable, '-c', 'import nonexistent_module']
    sides.write_text(
        f'[engine]\n'
        f'argv = {json.dumps(engine)}\n'
        f'env = {{ PYTHONPATH = {json.dumps(ENGINE.env["PYTHONPATH"])} }}\n',
        encoding='utf-8',
    )
    corpora = tmp_path / 'corpora.toml'
    if broken != 'corpora':
        corpora.write_text(
            f'[example]\n'
            f'checkout = {json.dumps(f"{corpus.checkout}")}\n'
            f'root = {json.dumps(corpus.root)}\n',
            encoding='utf-8',
        )
    else:
        corpora.write_text('', encoding='utf-8')
    out = tmp_path / 'out'
    argv = [
        'matrix',
        *('--sides', f'{sides}', '--a', 'engine', '--b', 'engine'),
        *('--corpora', f'{corpora}'),
        *('--stage', 'wirte' if broken == 'stage' else 'write'),
        *('--work', f'{tmp_path / "work"}', '--out', f'{out}', '--now', NOW),
    ]
    with pytest.raises(SystemExit) as raised:
        cli.main(argv)
    assert raised.value.code == 2
    assert message in capsys.readouterr().err
    assert not (tmp_path / 'work').exists()
    assert not out.exists()
