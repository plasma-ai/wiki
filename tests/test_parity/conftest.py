"""Shared fixtures for the parity harness tests.

The harness is tested the way it is used: real sides run the real
``wiki`` console script on snapshots of a small checkout, and the tests
read the differences the harness reports.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys

import pytest

from parity import runner, side

from ..test_cli.conftest import _env

# the console script beside the running interpreter: this checkout's install
WIKI = shutil.which('wiki', path=pathlib.Path(sys.executable).parent)
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
#: the instant every side's clock is fixed at
NOW = '2026-01-02T03:04:05Z'
#: the engine under test, reading this worktree's package
ENGINE = side.Side(
    name='engine',
    argv=(f'{WIKI}',),
    env={'PYTHONPATH': f'{REPO_ROOT}'},
)


@pytest.fixture(scope='session')
def corpus(tmp_path_factory: pytest.TempPathFactory) -> runner.Corpus:
    """A committed checkout that holds a small converged wiki.

    The wiki is the repository's example plus two indexed folders, one
    nested folder and a non-Markdown file, so the matrix has folders to
    scope to and pages to read. Every page has a desc and one has tags,
    so each search and match of the matrix has something to find. It is
    built with the real CLI and then committed, as a campaign checkout
    is. READ-ONLY: the harness snapshots the checkout and runs only in
    its clones.
    """
    checkout = tmp_path_factory.mktemp('parity') / 'checkout'
    root = checkout / 'hello'
    ignore = shutil.ignore_patterns('cache', '.obsidian')
    shutil.copytree(REPO_ROOT / 'examples' / 'hello', root, ignore=ignore)
    home = checkout.parent / 'home'
    home.mkdir()
    env = side.environment(ENGINE, home, now=NOW)
    for folder in ('guides', 'guides/deep', 'core'):
        args = ['new', folder, '--desc', f'About {folder}.', '--content', 'Text.']
        made = side.run(ENGINE, [*args, '--path', f'{root}'], cwd=home, env=env)
        assert made.code == 0, made.stderr
    pages = {
        'guides/setup.md': (
            'desc: Installing the widget.\ntags: [install, widget]',
            '# Setup\n\nRun the installer to set up the widget.\n',
        ),
        'guides/deep/notes.md': (
            'desc: Notes on render order.',
            '# Notes\n\nWidgets render after the installer.\n',
        ),
        'core/design.md': (
            'desc: The widget subsystem.',
            '# Design\n\nThe widget subsystem handles rendering.\n',
        ),
    }
    for relpath, (fields, body) in pages.items():
        text = f'---\n{fields}\n---\n\n{body}'
        (root / relpath).write_text(text, encoding='utf-8')
    snippet = 'widget appears in plain code here\n'
    (root / 'core' / 'snippet.txt').write_text(snippet, encoding='utf-8')
    updated = side.run(ENGINE, ['update', '--path', f'{root}'], cwd=home, env=env)
    assert updated.code == 0, updated.stderr
    # commit under the suite's hermetic git environment
    for args in (
        ['init', '-q', '-b', 'main'],
        ['add', '-A'],
        ['-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', 'wiki'],
    ):
        cmd = ['git', '-C', f'{checkout}', *args]
        subprocess.run(cmd, env=_env(), capture_output=True, check=True)
    return runner.Corpus(label='example', checkout=checkout, root='hello')
